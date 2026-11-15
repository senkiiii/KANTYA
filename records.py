"""Food record validation, filtering, persistence, and views."""

import ast
import csv
import io
import json
import secrets
import time
import math
import os
import sqlite3
from datetime import date, datetime, timedelta
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from flask import abort, current_app, flash, redirect, render_template, request, session, url_for

from analysis import summary
from database import db
from operating_calendar import ITEMS, MENU, menu_allowed, BUNDLED_CALENDAR_START, closed_reason, is_operating_day


# Keep deleted IDs out of circulation so old record links cannot open a new row.
INSERT_RECORD = """
    INSERT INTO records(id,item,day,period,prepared,sold,waste,creator,source)
    VALUES (
        max(coalesce((SELECT max(id) FROM records), 0),
            coalesce((SELECT max(record_id) FROM deleted_records), 0)) + 1,
        ?,?,?,?,?,?,?,?
    )
"""


def reject_leftover_override():
    if 'leftover' in request.form:
        raise ValueError('Leftover is calculated by KANTYA.')


def page_number():
    page = int(request.args.get('page', '1'))
    if page < 1:
        raise ValueError('Page must be a positive integer.')
    return page


def quantities(p, s, w):
    vals = []
    for x in (p, s, w):
        if not str(x).strip().isdigit():
            raise ValueError('Quantities must be non-negative whole numbers.')
        quantity = int(x)
        if quantity > 2**63 - 1:
            raise ValueError('Quantity is outside the supported range.')
        vals.append(quantity)
    p, s, w = vals
    if s > p or w > p - s:
        raise ValueError('Sold must not exceed Prepared; Waste must not exceed Leftover.')
    return (p, s, w)


def valid_day(value):
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise ValueError('Use a valid YYYY-MM-DD record date.') from None
    if d.isoformat() != value:
        raise ValueError('Use a valid YYYY-MM-DD record date.')
    if d > datetime.now(ZoneInfo('Asia/Manila')).date():
        raise ValueError('Future food records are disabled in this local prototype.')
    if value < BUNDLED_CALENDAR_START or value > current_app.config["CALENDAR_END"]:
        raise ValueError('This date is outside the supplied operating calendar. Extend calendar coverage first.')
    if not is_operating_day(d):
        raise ValueError('This is a known closed date (weekend, holiday, or semester break).')
    return value


def page_link(filters, page, endpoint='dashboard'):
    q = {k: v for k, v in filters.items() if v}
    q['page'] = page
    return url_for(endpoint) + '?' + urlencode(q)


def scoped():
    with db() as c:
        first = c.execute('SELECT MIN(day) FROM records').fetchone()[0]
        latest = c.execute('SELECT MAX(day) FROM records').fetchone()[0]
    start = request.args.get('start', first or BUNDLED_CALENDAR_START)
    end = request.args.get('end', latest or current_app.config["CALENDAR_END"])
    item = request.args.get('item', '')
    period = request.args.get('period', '')
    view = request.args.get('view', 'overview')
    grain = request.args.get('grain', 'auto')
    if view == 'waste' and session.get('role') != 'Manager':
        from flask import abort
        abort(403)
    if date.fromisoformat(start).isoformat() != start or date.fromisoformat(end).isoformat() != end:
        raise ValueError('Use YYYY-MM-DD dates.')
    if start > end:
        raise ValueError('Start date must not exceed end date.')
    if item and item not in ITEMS:
        raise ValueError('Unknown food item.')
    if period and period not in ['Breakfast', 'Lunch', 'Snack']:
        raise ValueError('Unknown selling period.')
    if view not in ['overview', 'patterns', 'waste']:
        raise ValueError('Unknown analysis view.')
    if grain not in ['auto', 'daily', 'weekly']:
        raise ValueError('Unknown chart aggregation.')
    sql = 'SELECT * FROM records WHERE day BETWEEN ? AND ?'
    args = [start, end]
    for col, val in [('item', item), ('period', period)]:
        if val:
            sql += ' AND ' + col + '=?'
            args.append(val)
    with db() as c:
        raw = [dict(r) for r in c.execute(sql + ' ORDER BY day,item,period', args)]
    excluded = {}
    for r in raw:
        reason = closed_reason(r['day'])
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
    rows = [r for r in raw if is_operating_day(r['day'])]
    expected = []
    d = date.fromisoformat(max(start, BUNDLED_CALENDAR_START))
    last = date.fromisoformat(min(end, current_app.config["CALENDAR_END"]))
    while d <= last:
        if is_operating_day(d):
            expected.append(d.isoformat())
        d += timedelta(days=1)
    expected_pairs = bundled_pairs()
    expected_pairs = {pair for pair in expected_pairs if (not item or pair[0] == item) and (not period or pair[1] == period)}
    present = {(r['item'], r['period'], r['day']) for r in rows}
    missing = sum(((i, p, d) not in present for i, p in expected_pairs for d in expected if menu_allowed(d,p,i)))
    coverage = dict(included=len(rows), excluded=sum(excluded.values()), reasons=excluded, dates=len({r['day'] for r in rows}), expected_dates=len(expected), missing=missing, latest=latest, first=first, outside=start < BUNDLED_CALENDAR_START or end > current_app.config["CALENDAR_END"])
    return (rows, dict(start=start, end=end, item=item, period=period, view=view, grain=grain), coverage)


def bundled_pairs():
    return {(r['food_item_name'],r['selling_period']) for r in MENU}


def index():
    try:
        day = request.args.get('day', '')
        if day:
            date.fromisoformat(day)
            return redirect(url_for('index', start=day, end=day, item=request.args.get('item', ''), period=request.args.get('period', '')))
        rows, filters, coverage = scoped()
        page = page_number()
        query = request.args.get('q', '').strip()
        sort = request.args.get('sort', 'newest')
        if len(query) > 100 or sort not in ('newest', 'oldest'):
            raise ValueError('Invalid search or sort option.')
        filters.update(q=query, sort=sort)
        if query:
            rows = [row for row in rows if query.casefold() in row['item'].casefold()]
    except (ValueError, TypeError):
        return ('Invalid date, page or filter.', 400)
    coverage['included'] = len(rows)
    coverage['dates'] = len({row['day'] for row in rows})
    selected_totals = summary(rows) if filters['item'] and rows else None
    rows = sorted(rows, key=lambda r: (r['day'], r['item'], r['period']), reverse=sort == 'newest')
    page_size = 50
    pages = max(1, math.ceil(len(rows) / page_size))
    page = min(page, pages)
    return render_template('index.html', rows=rows[(page - 1) * page_size:page * page_size], selected_totals=selected_totals, items=ITEMS, filters=filters, coverage=coverage, count=len(rows), page=page, pages=pages, previous=page_link(filters, page - 1, 'index') if page > 1 else None, following=page_link(filters, page + 1, 'index') if page < pages else None)


PERIODS = ('Breakfast', 'Lunch', 'Snack')


def record_return():
    target = request.args.get('back', '')
    if target.startswith('/') and not target.startswith('//') and '\\' not in target and not any(ord(c) < 32 for c in target):
        path = urlsplit(target)
        if not path.netloc and not path.scheme and path.path in ('/', '/dashboard'):
            return target
    return url_for('index')


def record_panel(template, **values):
    page = template if request.headers.get('X-Kantya-Panel') == '1' else 'record_page.html'
    return render_template(page, panel_template=template, back=record_return(), items=ITEMS, periods=PERIODS, **values)


def can_correct(row):
    return session['role'] == 'Manager' or row['creator'] == session['uid']


def view_record(rid):
    with db() as c:
        r = c.execute('SELECT * FROM records WHERE id=?', (rid,)).fetchone()
        if r is None:
            abort(404)
        editable = can_correct(r)
        history = [dict(entry) for entry in c.execute(
            'SELECT old_values,new_values,reason,corrected_at FROM corrections WHERE record_id=? ORDER BY id DESC',
            (rid,),
        )] if editable else []
    for entry in history:
        for key in ('old_values', 'new_values'):
            entry[key] = dict(zip(('Prepared', 'Sold', 'Actual waste'), ast.literal_eval(entry[key])))
    return record_panel('record_details.html', r=r, editable=editable, history=history)


def record_values():
    try:
        reject_leftover_override()
    except ValueError as e:
        return None, {'quantities': str(e)}
    values = [request.form.get(field, '') for field in ('prepared', 'sold', 'waste')]
    for field, value in zip(('prepared', 'sold', 'waste'), values):
        try:
            quantities(value, '0', '0')
        except ValueError as e:
            return None, {field: str(e)}
    prepared, sold, waste = map(int, values)
    if sold > prepared:
        return None, {'sold': 'Sold cannot exceed Prepared.'}
    if waste > prepared - sold:
        return None, {'waste': 'Actual Waste cannot exceed Leftover.'}
    return (prepared, sold, waste), {}


def add():
    errors = {}
    duplicate = None
    values = request.form if request.method == 'POST' else request.args
    if request.method == 'POST':
        item, period, day = (values.get(field, '') for field in ('item', 'period', 'day'))
        qty, errors = record_values()
        if item not in ITEMS:
            errors['item'] = 'Select a valid food item.'
        if period not in PERIODS:
            errors['period'] = 'Select a valid selling period.'
        try:
            valid_day(day)
        except ValueError as e:
            errors['day'] = str(e)
        if item in ITEMS and period in PERIODS and 'day' not in errors and not menu_allowed(day,period,item):
            errors['item'] = 'This item is not scheduled for the selected day and period.'
        if not errors:
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                existing = c.execute('SELECT id FROM records WHERE item=? AND day=? AND period=?', (item, day, period)).fetchone()
                if existing:
                    duplicate = existing['id']
                else:
                    c.execute(INSERT_RECORD,
                              (item, day, period, *qty, session['uid'], 'manual'))
            if duplicate is None:
                flash('Record saved.')
                return redirect(record_return())
            errors['duplicate'] = 'A record already exists for this item, date and selling period.'
    return record_panel('form.html', r=None, values=values, errors=errors, duplicate=duplicate)


def delete_record(rid):
    error = None
    reason = request.form.get('reason', '').strip()
    with db() as c:
        if request.method == 'POST':
            c.execute('BEGIN IMMEDIATE')
        record = c.execute('SELECT * FROM records WHERE id=?', (rid,)).fetchone()
        if record is None:
            abort(404)
        if request.method == 'POST':
            if not reason or len(reason) > 1000:
                error = 'Enter a deletion reason of 1 to 1,000 characters.'
            else:
                history = [dict(row) for row in c.execute(
                    'SELECT * FROM corrections WHERE record_id=? ORDER BY id', (rid,)
                )]
                c.execute(
                    'INSERT INTO deleted_records(record_id,snapshot,corrections,deleted_by,reason) VALUES(?,?,?,?,?)',
                    (rid, json.dumps(dict(record)), json.dumps(history), session['uid'], reason),
                )
                c.execute('DELETE FROM corrections WHERE record_id=?', (rid,))
                c.execute('DELETE FROM records WHERE id=?', (rid,))
    if request.method == 'POST' and not error:
        flash('Record deleted. The audit copy was retained.')
        return redirect(record_return(), code=303)
    return record_panel('delete_record.html', r=record, reason=reason, error=error)


def correct(rid):
    with db() as c:
        r = c.execute('SELECT * FROM records WHERE id=?', (rid,)).fetchone()
    if r is None:
        abort(404)
    if not can_correct(r):
        abort(403)
    errors = {}
    if request.method == 'POST':
        qty, errors = record_values()
        reason = request.form.get('reason', '').strip()
        if not reason or len(reason) > 1000:
            errors['reason'] = 'Enter a correction reason of 1 to 1,000 characters.'
        if not errors:
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                current = c.execute('SELECT * FROM records WHERE id=?', (rid,)).fetchone()
                if current is None:
                    abort(404)
                if not can_correct(current):
                    abort(403)
                c.execute('INSERT INTO corrections(record_id,manager_id,old_values,new_values,reason) VALUES(?,?,?,?,?)',
                          (rid, session['uid'], str([current[k] for k in ('prepared', 'sold', 'waste')]), str(list(qty)), reason))
                c.execute('UPDATE records SET prepared=?,sold=?,waste=? WHERE id=?', (*qty, rid))
            flash('Correction saved with history.')
            return redirect(record_return())
    return record_panel('form.html', r=r, values=request.form if request.method == 'POST' else dict(r), errors=errors, duplicate=None)


def import_csv():
    """Validated two-stage import: preview first, explicitly confirm valid rows."""
    result=None;errors=[];message=None
    if request.method=='POST':
        action=request.form.get('action','preview')
        if action=='confirm':
            token=request.form.get('token','')
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                stage=c.execute('SELECT payload,expires_at FROM import_staging WHERE token=? AND user_id=?',(token,session['uid'])).fetchone()
                if not stage or stage['expires_at']<int(time.time()):
                    message='Import preview expired. Upload again.'
                else:
                    payload=json.loads(stage['payload']);count=0;skipped=0
                    for row in payload['valid']:
                        # Revalidate because the calendar and existing data could have changed.
                        item,day,period,p,sold,w=row
                        if not menu_allowed(day,period,item):skipped+=1;continue
                        if c.execute('SELECT id FROM records WHERE item=? AND day=? AND period=?',(item,day,period)).fetchone():
                            skipped+=1;continue
                        c.execute(INSERT_RECORD,(item,day,period,p,sold,w,session['uid'],'CSV import'))
                        count+=1
                    c.execute('DELETE FROM import_staging WHERE token=?',(token,))
                    flash(f'CSV import complete: {count} inserted; {skipped} skipped due to new duplicates or changes.')
                    return redirect(url_for('index'),code=303)
        elif action=='preview':
            uploaded=request.files.get('file')
            if not uploaded or not uploaded.filename.lower().endswith('.csv'):
                message='Please select a CSV file.'
            else:
                try:
                    raw=uploaded.read()
                    if not raw:
                        raise ValueError('CSV file is empty. Download the template and enter at least one row.')
                    if len(raw)>5*1024*1024:
                        raise ValueError('CSV file exceeds the 5 MB upload limit.')
                    stream=io.StringIO(raw.decode('utf-8-sig'), newline='')
                    sample=stream.read(4096)
                    stream.seek(0)
                    try:
                        dialect=csv.Sniffer().sniff(sample,delimiters=',;\t')
                        reader=csv.DictReader(stream,dialect=dialect)
                    except csv.Error:
                        stream.seek(0)
                        reader=csv.DictReader(stream)
                    if not reader.fieldnames:
                        raise ValueError('CSV headers are missing. Use the downloadable template.')
                    reader.fieldnames=[(header or '').strip().casefold().replace(' ','_') for header in reader.fieldnames]
                    aliases={'date':['record_date','date','day'],
                             'item':['food_item_name','food_item','item'],
                             'period':['selling_period','period'],
                             'prepared':['quantity_prepared','prepared'],
                             'sold':['quantity_sold','sold'],
                             'waste':['actual_waste','waste']}
                    cols={k:next((x for x in opts if x in reader.fieldnames),None) for k,opts in aliases.items()}
                    if not all(cols.values()):
                        raise ValueError('Missing required CSV columns: '+', '.join(k for k,v in cols.items() if not v)+'. Download the CSV template and keep its headers.')
                    valid=[];seen=set();countdup=0
                    with db() as c:
                        present={(r['day'],r['item'],r['period']) for r in c.execute('SELECT day,item,period FROM records')}
                    for lineno,row in enumerate(reader,start=2):
                        if lineno>10002:raise ValueError('Maximum 10,000 rows per import. Split larger files.')
                        try:
                            day=valid_day((row[cols['date']] or '').strip())
                            item=(row[cols['item']] or '').strip();period=(row[cols['period']] or '').strip()
                            if item not in ITEMS or period not in PERIODS or not menu_allowed(day,period,item):
                                raise ValueError('Item not scheduled on selected date/period')
                            p,sold,w=quantities(row[cols['prepared']],row[cols['sold']],row[cols['waste']])
                            key=(day,item,period)
                            if key in seen or key in present:
                                countdup+=1;continue
                            seen.add(key);valid.append((item,day,period,p,sold,w))
                        except (ValueError,KeyError,TypeError) as exc:
                            errors.append(dict(row=lineno,item=row.get(cols['item'],'') if cols['item'] else '',reason=str(exc)))
                    token=secrets.token_urlsafe(24)
                    payload=dict(valid=valid,errors=errors,duplicates=countdup)
                    with db() as c:
                        c.execute('DELETE FROM import_staging WHERE expires_at<?',(int(time.time()),))
                        c.execute('INSERT INTO import_staging(token,user_id,payload,expires_at) VALUES(?,?,?,?)',(token,session['uid'],json.dumps(payload),int(time.time())+1800))
                    if not valid and not errors and not countdup:
                        raise ValueError('The CSV has no data rows. Enter records below the header.')
                    result=dict(token=token,valid=len(valid),invalid=len(errors),duplicates=countdup,preview=valid[:15])
                except (UnicodeError,ValueError,csv.Error,TypeError) as exc:
                    message=str(exc)
        else:message='Unknown import action.'
    return record_panel('import_panel.html',result=result,import_errors=errors[:100],import_message=message,r=None)


def menu_options():
    from flask import jsonify
    day=request.args.get('day','')
    period=request.args.get('period','')
    try:
        valid_day(day)
        options=[dict(item=r['food_item_name'],unit=r['unit']) for r in MENU if r['weekday']==date.fromisoformat(day).strftime('%A') and r['selling_period']==period]
        return jsonify(valid=True,weekday=date.fromisoformat(day).strftime('%A'),items=options)
    except ValueError as error:
        return jsonify(valid=False,reason=str(error),items=[])


def bulk_entry():
    day=request.values.get('day','')
    error=None;entries=[]
    if day:
        try:
            valid_day(day)
            entries=[r for r in MENU if r['weekday']==date.fromisoformat(day).strftime('%A')]
        except ValueError as exc:error=str(exc)
    if request.method=='POST' and not error:
        batch=[]
        try:
            if not day:raise ValueError('Choose an operating date.')
            for idx,item in enumerate(entries):
                vals=[request.form.get(f'{k}_{idx}','').strip() for k in ('prepared','sold','waste')]
                if not any(vals):continue # blank is missing, not zero
                if not all(vals):raise ValueError('Complete all three quantities for '+item['food_item_name'])
                p,so,w=quantities(*vals)
                batch.append((item['food_item_name'],day,item['selling_period'],p,so,w,session['uid'],'daily bulk'))
            if not batch:raise ValueError('Enter at least one complete food record.')
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                for item,d,period,*_ in batch:
                    if c.execute('SELECT id FROM records WHERE item=? AND day=? AND period=?',(item,d,period)).fetchone():
                        raise ValueError('Duplicate record: '+item+' / '+period+' / '+d)
                c.executemany(INSERT_RECORD,batch)
            flash(f'{len(batch)} scheduled food records saved.')
            return redirect(url_for('index'),code=303)
        except (ValueError,sqlite3.IntegrityError) as exc:error=str(exc)
    return record_panel('bulk_panel.html',entries=entries,day=day,bulk_error=error,r=None)

def csv_import_errors(token):
    from flask import Response
    with db() as c:
        stage=c.execute('SELECT payload,expires_at FROM import_staging WHERE token=? AND user_id=?',(token,session['uid'])).fetchone()
    if not stage or stage['expires_at']<int(time.time()):
        abort(404)
    failures=json.loads(stage['payload']).get('errors',[])
    out=io.StringIO();writer=csv.writer(out);writer.writerow(['CSV Row','Food Item','Error Reason'])
    for r in failures:writer.writerow([r['row'],r.get('item',''),r['reason']])
    return Response('\ufeff'+out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="KANTYA_Import_Errors.csv"'})

def csv_template():
    data='\ufeffrecord_date,food_item_name,selling_period,quantity_prepared,quantity_sold,actual_waste\r\n'
    from flask import Response
    return Response(data,mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="KANTYA_Import_Template.csv"'})

def seed_synthetic():
    """Load the supplied simulated history on fresh installations only."""
    from pathlib import Path
    with db() as c:
        if c.execute('SELECT COUNT(*) FROM records').fetchone()[0]:
            return
        if c.execute("SELECT value FROM settings WHERE key='synthetic_seeded'").fetchone():
            return
        path=Path(current_app.root_path)/'data'/'HISTORICAL_FOOD_RECORDS_WITH_FINANCIALS.csv'
        if not path.exists():
            return
        batch=[]
        with path.open(encoding='utf-8-sig',newline='') as stream:
            for row in csv.DictReader(stream):
                d=row['record_date']; i=row['food_item_name']; period=row['selling_period']
                if not is_operating_day(d) or not menu_allowed(d,period,i):
                    continue
                p,s,w=quantities(row['quantity_prepared'],row['quantity_sold'],row['actual_waste'])
                batch.append((i,d,period,p,s,w,None,'synthetic validated simulation'))
        c.executemany(INSERT_RECORD,batch)
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('synthetic_seeded','yes')")
