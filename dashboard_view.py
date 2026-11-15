"""Operational dashboard summaries for the selected record scope."""

from math import cos, radians, sin
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, median

from analysis import line_chart
from operating_calendar import ITEMS, is_operating_day


def unit_totals(rows):
    totals = {}
    for row in rows:
        unit = ITEMS[row['item']].lower() + 's'
        bucket = totals.setdefault(unit, dict(unit=unit, prepared=0, sold=0, leftover=0, waste=0))
        for key in ('prepared', 'sold', 'waste'):
            bucket[key] += row[key]
        bucket['leftover'] += row['prepared'] - row['sold']
    return sorted(totals.values(), key=lambda row: row['unit'])


def bars(entries, suffix='', decimals=0):
    maximum = max((entry['value'] for entry in entries), default=0)
    return [dict(entry, width=(100 * entry['value'] / maximum if maximum else 0),
                 display=f"{entry['value']:,.{decimals}f}{suffix}") for entry in entries]


def dashboard_view(rows, filters, today):
    yesterday = (today - timedelta(days=1)).isoformat()
    dates = sorted({row['day'] for row in rows if row['day'] <= today.isoformat()})
    day = yesterday if yesterday in dates else (dates[-1] if dates else None)
    daily = [row for row in rows if row['day'] == day]
    previous_day = None
    if day:
        previous = date.fromisoformat(day) - timedelta(days=1)
        while previous.isoformat() >= filters['start']:
            if is_operating_day(previous):
                previous_day = previous.isoformat()
                break
            previous -= timedelta(days=1)
    prior = {(r['item'], r['period']): r for r in rows if r['day'] == previous_day}
    pairs = {(r['item'], r['period']) for r in daily}
    comparable = bool(pairs) and pairs == prior.keys()
    sold_before = {r['unit']: r['sold'] for r in unit_totals(prior.values())}
    units = unit_totals(daily)
    for row in units:
        previous_value = sold_before.get(row['unit'])
        row['delta'] = row['sold'] - previous_value if comparable and previous_value is not None else None

    item_rows = defaultdict(list)
    period_rows = defaultdict(list)
    for row in rows:
        item_rows[row['item']].append(row)
        period_rows[row['period']].append(row)
    sold_rate = []
    waste_rate = []
    for item, records in item_rows.items():
        prepared = sum(r['prepared'] for r in records)
        if prepared:
            sold_rate.append(dict(label=item, value=100 * sum(r['sold'] for r in records) / prepared))
            waste_rate.append(dict(label=item, value=100 * sum(r['waste'] for r in records) / prepared))

    period_rate = []
    for period in ('Breakfast', 'Lunch', 'Snack'):
        by_item = defaultdict(lambda: [0, 0])
        for row in period_rows[period]:
            totals = by_item[row['item']]
            totals[0] += row['prepared']
            totals[1] += row['sold']
        rates = [100 * sold / prepared for prepared, sold in by_item.values() if prepared]
        if rates:
            period_rate.append(dict(label=period, value=median(rates), count=len(rates)))

    guidance = []
    for row in daily:
        history = [r for r in item_rows[row['item']] if r['period'] == row['period'] and r['day'] <= day][-5:]
        if len(history) < 3:
            continue
        avg_leftover = mean(r['prepared'] - r['sold'] for r in history)
        avg_sold = mean(r['sold'] for r in history)
        full = sum(r['prepared'] > 0 and r['sold'] == r['prepared'] for r in history)
        if avg_leftover or full:
            guidance.append(dict(item=row['item'], period=row['period'], unit=ITEMS[row['item']].lower()+'s',
                                 average=avg_sold, leftover=avg_leftover, full=full, count=len(history)))
    guidance.sort(key=lambda g: (-(g['leftover'] / (g['average'] + g['leftover'])), -g['full'], g['item']))

    waste_count = sum(r['waste'] > 0 for r in rows)
    pie = []
    if rows:
        start = 0
        for name, count, color in [('Waste recorded', waste_count, '#a95724'),
                                   ('Zero recorded waste', len(rows) - waste_count, '#17634d')]:
            if not count:
                continue
            share = 100 * count / len(rows)
            angle = radians((start + share / 2) * 3.6 - 90)
            pie.append(dict(name=name, count=count, share=share, offset=start, color=color,
                            x=50 if share == 100 else 50 + 29 * cos(angle),
                            y=50 if share == 100 else 50 + 29 * sin(angle)))
            start += share

    sales_trend = None
    if filters['item'] and rows:
        by_date = defaultdict(int)
        for row in rows:
            by_date[row['day']] += row['sold']
        sales_trend = line_chart([('Sold per operating date', sorted(by_date.items())[-30:])], ITEMS[filters['item']])
    sort_rate = lambda entries: sorted(entries, key=lambda entry: (-entry['value'], entry['label']))
    return dict(day=day, yesterday=yesterday, previous=previous_day, comparable=comparable,
                sales_trend=sales_trend, units=units, daily=daily, guidance=guidance[:3], pie=pie,
                sold_bars=bars(sort_rate(sold_rate), '%', 1), waste_bars=bars(sort_rate(waste_rate), '%', 2),
                period_bars=bars(period_rate, '%', 1), waste_units=unit_totals(rows))
