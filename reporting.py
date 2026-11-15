"""Dashboard, reports, and forecast views."""

import csv
import json
import math
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from statistics import mean

from flask import current_app, flash, redirect, render_template, request, session, url_for

from analysis import line_chart
from backup_view import backup_panel, can_manage_backups
from dashboard_analysis import workspace
from dashboard_view import dashboard_view
from database import db
from operating_calendar import HOLIDAYS, ITEMS, SEMESTER_BREAKS, is_operating_day
from records import bundled_pairs, page_number, scoped

FORECAST_HORIZON = 5
METHOD_LABELS = {'SES': 'Simple exponential smoothing', 'HoltWintersAdd5': 'Holt-Winters additive', 'Naive': 'Last-observation baseline', 'SeasonalNaive5': 'Five-observation baseline', 'MovingAverage5': 'Five-observation moving average', 'Holt': 'Holt trend', 'SARIMA_1_0_0x1_0_0_5': 'Seasonal ARIMA'}


def dashboard():
    return analysis_page()


def waste():
    args = request.args.to_dict()
    args['view'] = 'waste'
    return redirect(url_for('dashboard', **args))


def reports():
    return analysis_page(report=True)


def analysis_page(report=False):
    try:
        rows, filters, coverage = scoped()
        page_number()
    except (ValueError, TypeError) as e:
        return (render_template('error.html', message=str(e)), 400)
    data = workspace(rows, filters, ITEMS, is_operating_day, bundled_pairs())
    today = datetime.now(ZoneInfo('Asia/Manila')).date()
    metrics = dashboard_view(rows, filters, today)
    backup_allowed = report and can_manage_backups()
    backup = backup_panel() if backup_allowed and request.args.get('backup') == 'open' else None
    return render_template('report.html' if report else 'analytics.html', backup_allowed=backup_allowed, backup=backup, filters=filters, items=ITEMS,
                           coverage=coverage, metrics=metrics,
                           development=any(r['source'].startswith(('simulated ', 'synthetic')) for r in rows), **data)


def model_metadata():
    path = os.path.join(current_app.root_path, 'data', 'forecast_reference', 'metadata.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def signature(rows):
    import hashlib
    payload = dict(rows=rows, method=current_app.config["FORECAST_METHOD"], horizon=FORECAST_HORIZON, calendar_end=current_app.config["CALENDAR_END"], holidays=sorted(HOLIDAYS), breaks=SEMESTER_BREAKS, extension=current_app.config["CALENDAR_EXTENSION"])
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def future_operating_dates(after_day):
    if not after_day:
        return []
    d = date.fromisoformat(after_day) + timedelta(days=1)
    end = date.fromisoformat(current_app.config["CALENDAR_END"])
    future = []
    while d <= end and len(future) < FORECAST_HORIZON:
        if is_operating_day(d):
            future.append(d.isoformat())
        d += timedelta(days=1)
    return future


def forecast():
    with db() as c:
        rows = [dict(r) for r in c.execute('SELECT item,day,period,prepared,sold,waste FROM records ORDER BY item,period,day') if is_operating_day(r['day'])]
        runs = c.execute('SELECT * FROM forecast_runs ORDER BY id DESC').fetchall()
    cutoff = max((r['day'] for r in rows), default=None)
    item = request.args.get('item') or 'Chicken Adobo'
    period = request.args.get('period') or 'Lunch'
    if item not in ITEMS or period not in ('Breakfast', 'Lunch', 'Snack'):
        return ('Invalid forecast filter.', 400)
    history = [r for r in rows if r['item'] == item and r['period'] == period]
    run, results = None, []
    for candidate in runs:
        matches = [r for r in json.loads(candidate['results']) if r['item'] == item and r['period'] == period]
        if matches:
            run, results = candidate, sorted(matches, key=lambda r: r['day'])
            break
    future_dates = future_operating_dates(cutoff)
    forecast_available = len(future_dates) == FORECAST_HORIZON
    error = None
    demo = False
    if request.method == 'POST':
        if session['role'] != 'Manager':
            return ('Forbidden', 403)
        try:
            demo_requested = request.form.get('mode') == 'demo'
            target_dates = future_dates
            if demo_requested:
                if not history:
                    raise ValueError('Choose an item and selling period with sales history for the demo.')
                day = date.fromisoformat(history[-1]['day'])
                target_dates = []
                while len(target_dates) < FORECAST_HORIZON:
                    day += timedelta(days=1)
                    if day.weekday() < 5:
                        target_dates.append(day.isoformat())
            if not demo_requested and not forecast_available:
                raise ValueError(f'The calendar ends on {current_app.config["CALENDAR_END"]}. Supply confirmed coverage for five dates after the history cutoff before generation.')
            from forecasting import predict
            if len(history) < 20:
                raise ValueError(f'{item} / {period}: at least 20 observations are required.')
            dates = {r['day'] for r in history}
            d = date.fromisoformat(history[0]['day'])
            while not demo_requested and d <= date.fromisoformat(cutoff):
                if is_operating_day(d) and d.isoformat() not in dates:
                    raise ValueError(f'Missing operating record: {item} / {period} / {d}.')
                d += timedelta(days=1)
            estimates = predict(current_app.config["FORECAST_METHOD"], [r['sold'] for r in history], FORECAST_HORIZON)
            if len(estimates) != FORECAST_HORIZON or any(not math.isfinite(value) or value < 0 for value in estimates):
                raise ValueError('The forecast did not produce five valid sales estimates.')
            results = [
                dict(item=item, period=period, day=forecast_day, raw=float(value),
                     quantity=math.floor(float(value) + 0.5), unit=ITEMS[item])
                for forecast_day, value in zip(target_dates, estimates)
            ]
            if demo_requested:
                demo = True
                run = None
            else:
                with db() as c:
                    c.execute('INSERT INTO forecast_runs(cutoff,generated_at,manager_id,method,signature,results) VALUES(?,?,?,?,?,?)', (cutoff, datetime.now(ZoneInfo('Asia/Manila')).isoformat(), session['uid'], current_app.config["FORECAST_METHOD"], signature(rows), json.dumps(results)))
                flash('Five-operating-day sales forecast saved.')
                return redirect(url_for('forecast', item=item, period=period))
        except (ValueError, ImportError) as e:
            error = str(e)
    outdated = bool(run and (run['signature'] != signature(rows) or run['method'] != current_app.config["FORECAST_METHOD"]))
    elapsed = bool(not demo and results and min(r['day'] for r in results) < datetime.now(ZoneInfo('Asia/Manila')).date().isoformat())
    recent = [r for r in history if not run or r['day'] <= run['cutoff']][-15:]
    actual = [(r['day'], r['sold']) for r in recent]
    historical_graph = line_chart([('Recorded sales', actual)], ITEMS[item])
    graph = None
    if results:
        predicted = [(r['day'], r['quantity']) for r in results]
        if actual:
            predicted = [actual[-1]] + predicted
        chart_cutoff = history[-1]['day'] if demo else run['cutoff']
        days = sorted({day for day, _ in actual + predicted} | {chart_cutoff})
        graph = line_chart([('Recorded sales', actual), ('Forecast estimate', predicted)], ITEMS[item],
                           dates=days, forecast_start=chart_cutoff)
    recent_five = recent[-FORECAST_HORIZON:]
    history_summary = dict(
        first=recent[0]['day'], last=recent[-1]['day'], count=len(recent),
        recent_count=len(recent_five), latest=recent[-1],
        high=max(recent, key=lambda r: r['sold']),
        low=min(recent, key=lambda r: r['sold']),
        sold=mean(r['sold'] for r in recent_five),
        leftover=mean(r['prepared'] - r['sold'] for r in recent_five),
    ) if recent else None
    summary = None
    if results:
        high = max(results, key=lambda r: r['quantity'])
        low = min(results, key=lambda r: r['quantity'])
        comparison = None
        if len(recent) >= FORECAST_HORIZON:
            recent_mean = mean(r['sold'] for r in recent[-FORECAST_HORIZON:])
            forecast_mean = mean(r['quantity'] for r in results)
            difference = forecast_mean - recent_mean
            comparison = dict(recent=recent_mean, forecast=forecast_mean,
                              direction='about the same as' if abs(difference) < 1 else ('higher than' if difference > 0 else 'lower than'))
        summary = dict(high=high, low=low, comparison=comparison,
                       first=recent[0]['day'] if recent else None,
                       last=recent[-1]['day'] if recent else None,
                       leftover=mean(r['prepared'] - r['sold'] for r in recent[-FORECAST_HORIZON:]) if recent else None)
    with open(os.path.join(current_app.root_path, 'data', 'forecast_reference', 'test_item_metrics.csv'), encoding='utf-8') as f:
        evaluation = [r for r in csv.DictReader(f) if (not item or r['food_item_name'] == item) and (not period or r['selling_period'] == period)]
    for r in evaluation:
        r['unit'] = ITEMS[r['food_item_name']]
        for field in ['MAE', 'RMSE', 'MAPE_nonzero']:
            r[field] = float(r[field])
    return render_template('forecast.html', items=ITEMS, run=run, results=results, outdated=outdated, elapsed=elapsed, error=error, graph=graph, historical_graph=historical_graph, item=item, period=period, cutoff=cutoff, future_dates=future_dates, forecast_available=forecast_available, demo=demo, demo_available=len(history) >= 20, horizon=FORECAST_HORIZON, calendar_end=current_app.config["CALENDAR_END"], forecast_summary=summary, history_summary=history_summary, missing_dates=FORECAST_HORIZON-len(future_dates), unit=ITEMS[item],
                           method=METHOD_LABELS.get(current_app.config["FORECAST_METHOD"], current_app.config["FORECAST_METHOD"]), evaluation=evaluation, metadata=model_metadata())
