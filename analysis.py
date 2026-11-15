"""Pure, scope-consistent analytical summaries; no inferred demand or risk thresholds."""
from collections import defaultdict
from datetime import date, timedelta
from math import ceil, floor, log10
from statistics import mean, median
from urllib.parse import urlencode
PERIODS = ['Breakfast', 'Lunch', 'Snack']
WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']

def ratio(numerator, denominator):
    return 100 * numerator / denominator if denominator > 0 else None

def quantile(values, p):
    values = sorted(values)
    if not values:
        return None
    pos = (len(values) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)

def summary(rows):
    prepared = sum((r['prepared'] for r in rows))
    sold = sum((r['sold'] for r in rows))
    waste = sum((r['waste'] for r in rows))
    days = {r['day'] for r in rows}
    daily = defaultdict(int)
    for r in rows:
        daily[r['day']] += r['sold']
    eligible = [r for r in rows if r['prepared'] > 0]
    return dict(prepared=prepared, sold=sold, leftover=prepared - sold, waste=waste, sell_rate=ratio(sold, prepared), leftover_rate=ratio(prepared - sold, prepared), waste_rate=ratio(waste, prepared), waste_of_leftover=ratio(waste, prepared - sold), count=len(rows), dates=len(days), mean_daily=mean(daily.values()) if daily else None, median_daily=median(daily.values()) if daily else None, full_sale=sum((r['sold'] == r['prepared'] for r in eligible)), eligible=len(eligible), full_sale_rate=ratio(sum((r['sold'] == r['prepared'] for r in eligible)), len(eligible)), leftover_records=sum((r['prepared'] > r['sold'] for r in rows)), waste_records=sum((r['waste'] > 0 for r in rows)))

def link(filters, item=None, view=None, endpoint='/dashboard', period=None):
    query = {k: v for k, v in filters.items() if k in ['start', 'end', 'item', 'period', 'view', 'grain'] and v}
    if item is not None:
        query['item'] = item
    if view is not None:
        query['view'] = view
    if period is not None:
        query['period'] = period
    return endpoint + '?' + urlencode(query)

def horizontal(entries, name, suffix='', filters=None, view=None):
    entries = [e for e in entries if e['value'] is not None]
    if not entries:
        return None
    maximum = max(max((e['value'] for e in entries)), 1)
    for e in entries:
        e['width'] = 100 * e['value'] / maximum
        if filters is not None and 'item' in e:
            e['href'] = link(filters, item=e['item'], period=e.get('period'), view=view)
    return dict(kind='horizontal', name=name, entries=entries, maximum=maximum, suffix=suffix)

def line_chart(lines, name, dates=None, forecast_start=None):
    labels = dates or sorted({d for _, data in lines for d, v in data})
    values = [v for _, data in lines for d, v in data if v is not None]
    if not labels or not values:
        return None
    target_step = max(max(values) * 1.12 / 4, 1)
    magnitude = 10 ** floor(log10(target_step))
    step = next(multiplier * magnitude for multiplier in (1, 2, 2.5, 5, 10)
                if multiplier * magnitude >= target_step)
    maximum = 4 * ceil(step)
    paths = []
    for i, (label, data) in enumerate(lines):
        lookup = dict(data)
        points = []
        segments = []
        chunk = []
        for j, d in enumerate(labels):
            v = lookup.get(d)
            if v is None:
                if chunk:
                    segments.append(' '.join(chunk))
                    chunk = []
                continue
            x = 70 + j * 650 / max(len(labels) - 1, 1)
            y = 225 - v / maximum * 175
            chunk.append(f'{x:.2f},{y:.2f}')
            points.append(dict(x=x, y=y, day=d, value=v))
        if chunk:
            segments.append(' '.join(chunk))
        paths.append(dict(name=label, color=['#146c4c', '#cf8a25', '#4c79a3'][i % 3], segments=segments, points=points, dashed='forecast' in label.lower(), high_day=max(points,key=lambda point:point['value'])['day'] if points else None, low_day=min(points,key=lambda point:point['value'])['day'] if points else None))
    ticks = []
    for i in sorted({0, len(labels) // 3, 2 * len(labels) // 3, len(labels) - 1}):
        label = labels[i]
        try:
            label = date.fromisoformat(label).strftime('%d %b')
        except ValueError:
            pass
        ticks.append(dict(x=70 + i * 650 / max(len(labels) - 1, 1), label=label))
    return dict(kind='line', name=name, paths=paths, ticks=ticks, maximum=maximum, cutoff=forecast_start, forecast_x=70 + labels.index(forecast_start) * 650 / max(len(labels) - 1, 1) if forecast_start in labels else None, grid=[dict(y=225 - i * 175 / 4, value=i * maximum / 4) for i in range(5)])

def daily_chart(rows, fields, operating, name):
    if not rows:
        return None
    lookup = defaultdict(lambda: defaultdict(int))
    for r in rows:
        for field in fields:
            lookup[r['day']][field] += r['prepared'] - r['sold'] if field == 'leftover' else r[field]
    dates = []
    d = date.fromisoformat(min(lookup))
    end = date.fromisoformat(max(lookup))
    while d <= end:
        if operating(d):
            dates.append(d.isoformat())
        d += timedelta(days=1)
    names = {'prepared': 'Prepared', 'sold': 'Recorded sales', 'leftover': 'Leftover', 'waste': 'Actual waste'}
    return line_chart([(names[f], [(d, lookup[d][f] if d in lookup else None) for d in dates]) for f in fields], name, dates)

def workspace(rows, filters, units, operating):
    selected = bool(filters['item'])
    groups = defaultdict(list)
    series = defaultdict(list)
    for r in rows:
        groups[r['item']].append(r)
        series[r['item'], r['period']].append(r)
    totals = []
    for item, g in sorted(groups.items()):
        totals.append(dict(item=item, unit=units[item], href=link(filters, item=item), **summary(g)))
    detail = []
    for (item, period), g in sorted(series.items()):
        v = [r['sold'] for r in g]
        detail.append(dict(item=item, period=period, unit=units[item], href=link(filters, item=item, period=period), median=median(v), q1=quantile(v, 0.25), q3=quantile(v, 0.75), minimum=min(v), maximum=max(v), **summary(g)))
    charts = []
    insights = []
    patterns = []
    focus = summary(rows) if selected and rows else None
    unit = units.get(filters['item'], '')
    view = filters['view']
    if not selected:
        sell = sorted(totals, key=lambda g: -(g['sell_rate'] if g['sell_rate'] is not None else -1))
        waste = sorted(totals, key=lambda g: -(g['waste_rate'] if g['waste_rate'] is not None else -1))

        def entry(g, field):
            return dict(label=g['item'], value=g[field], item=g['item'], context=f"{g['count']} records · {g['dates']} observed dates")
        if view in ['overview', 'patterns']:
            charts.append(dict(title='Sell-through by item', note='Sold ÷ Prepared. Each bar uses totals for one item; quantities are not added across units.', graph=horizontal([entry(g, 'sell_rate') for g in sell], 'Sell-through', '%', filters, view)))
        if view in ['overview', 'waste']:
            charts.append(dict(title='Actual waste relative to preparation', note='Actual Waste ÷ Prepared. A descriptive comparison, not an approved risk threshold.', graph=horizontal([entry(g, 'waste_rate') for g in waste], 'Actual waste / prepared', '%', filters, 'waste')))
        if view == 'waste':
            charts.append(dict(title='Leftover relative to preparation', note='Leftover ÷ Prepared. Leftover and Actual Waste describe different quantities.', graph=horizontal([entry(g, 'leftover_rate') for g in waste], 'Leftover / prepared', '%', filters, 'waste')))
        if waste and waste[0]['waste_rate'] is not None:
            g = waste[0]
            insights.append(dict(title='A place to investigate', text=f"{g['item']} has the highest recorded waste-to-prepared ratio in this scope: {g['waste_rate']:.2f}% ({g['waste']:,} {g['unit']} discarded from {g['prepared']:,} prepared). This does not establish the cause.", href=link(filters, item=g['item'], view='waste'), action='Review item history'))
    elif rows:
        daily = defaultdict(lambda: dict(sold=0, prepared=0, waste=0))
        for r in rows:
            for field in ['sold', 'prepared', 'waste']:
                daily[r['day']][field] += r[field]
        weekday = []
        for day in WEEKDAYS:
            vals = [g['sold'] for d, g in daily.items() if date.fromisoformat(d).strftime('%A') == day]
            if vals:
                weekday.append(dict(label=day, value=mean(vals), context=f'{len(vals)} observed dates'))
        periods = []
        for period in PERIODS:
            g = [r for r in rows if r['period'] == period]
            if g:
                periods.append(dict(label=period, value=mean((r['sold'] for r in g)), context=f'{len(g)} observed dates'))
        if view == 'overview':
            charts = [dict(title='Preparation and recorded sales', note=f'Daily totals in {unit.lower()}. Missing operating records appear as gaps; closed dates are omitted.', graph=daily_chart(rows, ['prepared', 'sold'], operating, unit), wide=True), dict(title='Typical sales by weekday', note='Mean daily sold across the selected selling periods; weekdays with no observations are omitted.', graph=horizontal(weekday, f'Mean daily sold · {unit}')), dict(title='Typical sales by selling period', note='Mean sold per observed item–date–period record; compare the same item.', graph=horizontal(periods, f'Mean sold per date · {unit}'))]
        elif view == 'patterns':
            monthly = defaultdict(list)
            for d, g in daily.items():
                monthly[d[:7]].append(g['sold'])
            points = [(m + '-01', mean(v)) for m, v in sorted(monthly.items())]
            charts = [dict(title='Typical daily sales by month', note='Monthly mean of observed daily totals, not monthly sums. Months with no operating observations are gaps.', graph=line_chart([('Mean daily sold', points)], unit, monthly_dates(filters)), wide=True), dict(title='Weekday sales pattern', note='Average daily totals; observation counts show the amount of evidence.', graph=horizontal(weekday, f'Mean daily sold · {unit}')), dict(title='Selling-period sales pattern', note='Average sales for this item per observed date in each period.', graph=horizontal(periods, f'Mean sold · {unit}'))]
            for p in PERIODS:
                for day in WEEKDAYS:
                    g = [r for r in rows if r['period'] == p and date.fromisoformat(r['day']).strftime('%A') == day]
                    if g:
                        patterns.append(dict(period=p, weekday=day, count=len(g), mean=mean((r['sold'] for r in g)), median=median((r['sold'] for r in g))))
            if weekday:
                best = max(weekday, key=lambda g: g['value'])
                low = min(weekday, key=lambda g: g['value'])
                insights.append(dict(title='Observed weekday difference', text=f"{best['label']} averages {best['value']:.1f} {unit.lower()} sold per observed date; {low['label']} averages {low['value']:.1f}. These are descriptive averages, not proof of a weekday effect."))
        else:
            charts = [dict(title='Unsold food and recorded discards', note=f'Daily totals in {unit.lower()}. Actual Waste is a subset of Leftover under the record rule; the disposition of remaining leftover is unknown.', graph=daily_chart(rows, ['leftover', 'waste'], operating, unit), wide=True)]
            for field, title, note in [('leftover_rate', 'Leftover / prepared', 'Unsold fraction of preparation.'), ('waste_rate', 'Actual waste / prepared', 'Recorded discarded fraction of preparation; descriptive ratio.')]:
                entries = []
                for p in PERIODS:
                    g = [r for r in rows if r['period'] == p]
                    if g:
                        entries.append(dict(label=p, value=summary(g)[field], context=f'{len(g)} records'))
                charts.append(dict(title=title, note=note, graph=horizontal(entries, title, '%')))
            insights.append(dict(title='Recurring recorded waste', text=f"Actual waste was recorded in {focus['waste_records']} of {focus['count']} records; leftovers in {focus['leftover_records']}. Review the underlying dates before changing preparation."))
        if view == 'overview' and focus['eligible']:
            insights.append(dict(title='Full-sale records', text=f"Sold equals Prepared in {focus['full_sale']} of {focus['eligible']} records with positive preparation ({focus['full_sale_rate']:.1f}%). This flags records for review; it does not prove a stockout or unmet demand."))
    return dict(totals=totals, series=detail, selected=selected, focus=focus, unit=unit, charts=charts, insights=insights[:2], patterns=patterns)

def monthly_dates(filters):
    d = date.fromisoformat(filters['start']).replace(day=1)
    end = date.fromisoformat(filters['end'])
    result = []
    while d <= end:
        result.append(d.isoformat())
        d = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    return result
