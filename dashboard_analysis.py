"""Question-led dashboard evidence, retaining item units and explicit denominators."""
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, median, pstdev
from math import ceil, floor
from basic_charts import pie, histogram, item_scatter, series_scatter
from analysis import workspace as base_workspace, summary, quantile, ratio, link, horizontal, line_chart, WEEKDAYS, PERIODS

def operating_dates(filters, operating):
    first = max(filters['start'], '2025-10-01')
    last = min(filters['end'], '2026-09-30')
    d = date.fromisoformat(first)
    end = date.fromisoformat(last)
    result = []
    while d <= end:
        if operating(d):
            result.append(d.isoformat())
        d += timedelta(days=1)
    return result

def daily_groups(rows):
    out = defaultdict(list)
    for r in rows:
        out[r['day']].append(r)
    return out

def comparison(rows, filters, units, operating, expected_pairs=None):
    dates = operating_dates(filters, operating)
    n = min(20, len(dates) // 2)
    if n < 5:
        return dict(available=False, n=n, rows=[])
    prior = dates[-2 * n:-n]
    recent = dates[-n:]
    out = []
    pairs = expected_pairs if expected_pairs is not None else {(r['item'], r['period']) for r in rows}
    pairs = {(i, p) for i, p in pairs if (not filters['item'] or i == filters['item']) and (not filters['period'] or p == filters['period'])}
    for item in sorted({r['item'] for r in rows}):
        a = [r for r in rows if r['item'] == item and r['day'] in prior]
        b = [r for r in rows if r['item'] == item and r['day'] in recent]
        sa = summary(a)
        sb = summary(b)
        eligible_pairs = [p for i, p in pairs if i == item]
        missing_a = n * len(eligible_pairs) - len(a)
        missing_b = n * len(eligible_pairs) - len(b)
        complete = missing_a == 0 and missing_b == 0 and bool(a) and bool(b)
        change = ratio(sb['mean_daily'] - sa['mean_daily'], sa['mean_daily']) if complete and sa['mean_daily'] is not None and (sb['mean_daily'] is not None) else None
        waste_delta = sb['waste_rate'] - sa['waste_rate'] if complete and sa['waste_rate'] is not None and (sb['waste_rate'] is not None) else None
        out.append(dict(item=item, unit=units[item], prior=sa, recent=sb, change=change, waste_delta=waste_delta, complete=complete, missing=missing_a + missing_b, href=link(filters, item=item)))
    return dict(available=bool(out), n=n, prior_start=prior[0], prior_end=prior[-1], recent_start=recent[0], recent_end=recent[-1], rows=out)

def dot_chart(entries, name, suffix='%', focused=False):
    entries = [e for e in entries if e['value'] is not None]
    if not entries:
        return None
    low = max(0, floor(min((e['value'] for e in entries))) - 1) if focused else 0
    high = min(100, ceil(max((e['value'] for e in entries))) + 1) if focused else max(ceil(max((e['value'] for e in entries))), 1)
    if high <= low:
        high = low + 1
    for e in entries:
        e['position'] = 100 * (e['value'] - low) / (high - low)
    return dict(kind='dot', entries=entries, name=name, suffix=suffix, low=low, high=high, ticks=[dict(position=25 * i, value=low + (high - low) * i / 4) for i in range(5)], focused=focused)

def outcomes(entries):
    out = []
    for e, rows in entries:
        s = summary(rows)
        if not s['prepared']:
            continue
        out.append(dict(label=e, prepared=s['prepared'], sold=s['sold'], waste=s['waste'], unknown=s['leftover'] - s['waste'], sold_rate=s['sell_rate'], waste_rate=s['waste_rate'], unknown_rate=ratio(s['leftover'] - s['waste'], s['prepared'])))
    return dict(kind='outcomes', entries=out) if out else None

def heatmap(rows, filters, units, metric='weekday_index'):
    selected = bool(filters['item'])
    if metric == 'monthly_waste':
        columns = sorted({r['day'][:7] for r in rows})
        d = date.fromisoformat(max(filters['start'], '2025-10-01')).replace(day=1)
        end = date.fromisoformat(min(filters['end'], '2026-09-30'))
        columns = []
        while d <= end:
            columns.append(d.strftime('%Y-%m'))
            d = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
        labels = [date.fromisoformat(m + '-01').strftime('%b %y') for m in columns]
    else:
        columns = WEEKDAYS
        labels = [x[:3] for x in columns]
    keys = [p for p in PERIODS if any((r['period'] == p for r in rows))] if selected else sorted({r['item'] for r in rows})
    matrix = []
    numbers = []
    for key in keys:
        g = [r for r in rows if (r['period'] == key if selected else r['item'] == key)]
        total = summary(g)
        cells = []
        for col in columns:
            subset = [r for r in g if (r['day'][:7] == col if metric == 'monthly_waste' else date.fromisoformat(r['day']).strftime('%A') == col)]
            s = summary(subset)
            value = None
            if subset:
                if metric in ['monthly_waste', 'weekday_waste']:
                    value = s['waste_rate']
                elif selected:
                    value = s['mean_daily']
                else:
                    value = ratio(s['mean_daily'], total['mean_daily']) if total['mean_daily'] is not None else None
            if value is not None:
                numbers.append(value)
            tip = f'{key} · {col}: {value:.1f}' if value is not None else f'{key} · {col}: no usable observations'
            tip += f"; {s['dates']} dates, {s['prepared']} prepared, {s['waste']} recorded waste"
            cells.append(dict(value=value, dates=s['dates'], prepared=s['prepared'], waste=s['waste'], tip=tip))
        matrix.append(dict(label=key, cells=cells, href=link(filters, item=filters['item'] if selected else key, period=key if selected else None, view='waste' if 'waste' in metric else 'patterns')))
    maximum = max(numbers, default=1)
    minimum = min(numbers, default=0)
    indexed = metric == 'weekday_index' and (not selected)
    deviation = max([abs(v - 100) for v in numbers], default=1) or 1
    for row in matrix:
        for c in row['cells']:
            c['color'] = '43,124,89' if not indexed or c['value'] is None or c['value'] >= 100 else '207,138,37'
            c['intensity'] = (0.12 + 0.72 * abs(c['value'] - 100) / deviation if indexed else 0.12 + 0.72 * (c['value'] / maximum)) if c['value'] is not None and maximum > 0 else 0.12
    suffix = '%' if 'waste' in metric else '' if selected else ''
    return dict(kind='heatmap', indexed=indexed, columns=labels, rows=matrix, maximum=maximum, minimum=minimum, suffix=suffix, unit=units.get(filters['item'], 'Index (item average = 100)') if 'waste' not in metric else 'Waste / prepared (%)') if matrix else None

def distribution(rows, unit):
    groups = []
    for p in PERIODS:
        vals = [r['sold'] for r in rows if r['period'] == p]
        if vals:
            groups.append(dict(label=p, n=len(vals), minimum=min(vals), q1=quantile(vals, 0.25), median=median(vals), q3=quantile(vals, 0.75), maximum=max(vals)))
    maximum = max((g['maximum'] for g in groups), default=1) or 1
    for g in groups:
        for field in ['minimum', 'q1', 'median', 'q3', 'maximum']:
            g[field + '_pos'] = 100 * g[field] / maximum
    return dict(kind='distribution', entries=groups, maximum=maximum, unit=unit) if groups else None

def trend(rows, fields, filters, operating, unit):
    groups = daily_groups(rows)
    dates = operating_dates(filters, operating)
    grain = filters.get('grain', 'auto')
    weekly = grain == 'weekly' or (grain == 'auto' and len(dates) > 60)
    names = {'prepared': 'Prepared', 'sold': 'Recorded sales', 'leftover': 'Leftover', 'waste': 'Actual waste'}
    points = {f: [] for f in fields}
    contexts = {}
    if weekly:
        weeks = defaultdict(list)
        for d in dates:
            monday = date.fromisoformat(d) - timedelta(days=date.fromisoformat(d).weekday())
            weeks[monday.isoformat()].append(d)
        if dates:
            d = date.fromisoformat(dates[0]) - timedelta(days=date.fromisoformat(dates[0]).weekday())
            end = date.fromisoformat(dates[-1])
            while d <= end:
                weeks.setdefault(d.isoformat(), [])
                d += timedelta(days=7)
        labels = sorted(weeks)
        for week in labels:
            observed = [d for d in weeks[week] if d in groups]
            contexts[week] = f'{len(observed)} observed dates / {len(weeks[week])} operating dates'
            for f in fields:
                values = [sum((r['prepared'] - r['sold'] if f == 'leftover' else r[f] for r in groups[d])) for d in observed]
                points[f].append((week, mean(values) if values else None))
    else:
        labels = dates
        for d in dates:
            contexts[d] = f'{len(groups[d])} item–period records' if d in groups else 'Missing operating records'
            for f in fields:
                points[f].append((d, sum((r['prepared'] - r['sold'] if f == 'leftover' else r[f] for r in groups[d])) if d in groups else None))
    g = line_chart([(names[f], points[f]) for f in fields], unit, labels)
    if g:
        g['weekly'] = weekly
        g['context'] = contexts
        for path in g['paths']:
            for point in path['points']:
                point['context'] = contexts[point['day']]
    return g

def workspace(rows, filters, units, operating, expected_pairs=None):
    data = base_workspace(rows, filters, units, operating)
    totals = data['totals']
    selected = data['selected']
    focus = data['focus']
    unit = data['unit']
    view = filters['view']
    grouped = defaultdict(list)
    for r in rows:
        grouped[r['item']].append(r)
    compare = comparison(rows, filters, units, operating, expected_pairs)
    charts = []
    insights = []

    def panel(title, note, graph, wide=False):
        if not graph:
            return
        kind = graph['kind']
        insight = None
        if kind in ('dot', 'horizontal', 'pie') and graph['entries']:
            top = max(graph['entries'], key=lambda entry: entry['value'])
            suffix = graph.get('suffix', '')
            if kind == 'pie':
                insight = f"{top['label']} is the largest share at {top['share']:.2f}% ({top['value']:,} {graph['unit']})."
            else:
                value = f"{top['value']:,.2f}{suffix}" if suffix == '%' else f"{top['value']:,.1f} {suffix}".rstrip()
                insight = f"{top['label']} has the highest displayed value at {value}."
                if title == 'Recorded waste by food item':
                    title = f"{top['label']} had the highest recorded waste rate at {value}"
            top['highlight'] = True
        elif kind == 'line':
            latest = [f"{path['name']} {path['points'][-1]['value']:,.1f}" for path in graph['paths'] if path['points']]
            if latest:
                insight = f"Latest displayed values: {', '.join(latest)} ({graph['name']})."
        elif kind == 'histogram':
            top = max(graph['entries'], key=lambda entry: entry['count'])
            insight = f"The most common range contains {top['count']:,} of {graph['n']:,} observations."
        elif kind == 'scatter':
            insight = f"{graph['n']:,} observations are plotted. Inspect a point for its exact pair of values."
        elif kind == 'outcomes':
            top = max(graph['entries'], key=lambda entry: entry['waste_rate'])
            insight = f"{top['label']} has the largest recorded waste share at {top['waste_rate']:.2f}% of its preparation."
        elif kind == 'distribution':
            top = max(graph['entries'], key=lambda entry: entry['median'])
            insight = f"{top['label']} has the highest median at {top['median']:,.1f} {graph['unit']}."
        elif kind == 'heatmap':
            cells = [(cell['value'], row['label'], column) for row in graph['rows']
                     for cell, column in zip(row['cells'], graph['columns']) if cell['value'] is not None]
            if cells:
                value, label, column = max(cells)
                insight = f"{label} · {column} is the highest displayed cell at {value:,.1f}{graph['suffix']}."
        charts.append(dict(title=title, note=note, insight=insight, graph=graph, wide=wide))

    def entries(field):
        return [dict(label=t['item'], value=t[field], href=link(filters, item=t['item'], view=view), context=f"{t['count']} records · {t['dates']} dates · {t['unit']}") for t in sorted(totals, key=lambda t: -(t[field] or 0))]
    if not selected:
        if view == 'overview':
            panel('Sell-through by food item', 'Sold / prepared, using quantity totals within each item. Focused  percentage scale makes small recorded differences visible.', dot_chart(entries('sell_rate'), 'Sell-through', focused=True))
            panel('Recorded waste by food item', 'Actual waste / prepared. Zero-based percentage axis; each point retains its own denominator.', dot_chart(entries('waste_rate'), 'Waste / prepared'))
            panel('How often was waste recorded?', 'Composition of included records: positive waste versus zero recorded waste. Counts represent records, not physical quantities or waste weight.', pie([('Positive recorded waste', sum((r['waste'] > 0 for r in rows))), ('Zero recorded waste', sum((r['waste'] == 0 for r in rows)))], 'Included records', 'records'))
            panel('Distribution of waste-to-prepared ratios', 'One observation = one positive-preparation record. Bins show how many records fall within each percentage range, including records with zero waste.', histogram([100 * r['waste'] / r['prepared'] for r in rows if r['prepared'] > 0], 'Waste / prepared (%)', percent=True))
            panel('Sell-through and waste by item–period series', 'Each point summarizes one item × selling period. Both axes are ratios of quantity totals; the relationship is constrained by waste ≤ prepared − sold and is not causal.', series_scatter(rows))
            panel('Preparation outcomes by food item', 'Each row = 100% of that item’s preparation. Remaining leftover has unknown disposition. See exact quantities in the item comparison below.', outcomes([(t['item'], grouped[t['item']]) for t in totals]))
        elif view == 'patterns':
            panel('Weekday sales relative to each item’s average', 'Mean daily sales / that item’s scope-wide daily mean × 100. Index 100 is its own average, not a target. Periods are summed within each date.', heatmap(rows, filters, units), True)
            panel('Full-sale record frequency', 'Positive-preparation records where sold equals prepared. This is a review signal, not proof of a stockout.', dot_chart([dict(label=t['item'], value=t['full_sale_rate'], href=link(filters, item=t['item'], view='patterns'), context=f"{t['full_sale']} / {t['eligible']} eligible records") for t in sorted(totals, key=lambda t: -(t['full_sale_rate'] or 0))], 'Full-sale records'))
            variation = []
            for t in totals:
                vals = [sum((r['sold'] for r in g)) for g in daily_groups(grouped[t['item']]).values()]
                if len(vals) > 1 and mean(vals) > 0:
                    variation.append(dict(label=t['item'], value=100 * pstdev(vals) / mean(vals), href=link(filters, item=t['item'], view='patterns'), context=f"{len(vals)} dates · mean {mean(vals):.1f} {t['unit'].lower()} / date"))
            variation.sort(key=lambda x: -x['value'])
            panel('Relative variation in daily sales', 'Coefficient of variation = observed daily standard deviation / daily mean × 100. Periods combined per date; zero means are omitted. This is descriptive variability, not risk.', dot_chart(variation, 'Daily sales variability (CV)'))
        else:
            panel('Recorded waste rate by month and item', 'Each cell = 100 × recorded waste / prepared for that item and month. Missing observations and zero denominators stay blank.', heatmap(rows, filters, units, 'monthly_waste'), True)
            panel('Preparation outcomes by food item', 'Sold, recorded waste, and remaining leftover account for preparation. Physical counts are not pooled across items.', outcomes([(t['item'], grouped[t['item']]) for t in totals]), True)
        if totals:
            worst = max(totals, key=lambda t: t['waste_rate'] if t['waste_rate'] is not None else -1)
            if worst['waste_rate'] is not None:
                insights.append(dict(title='Largest recorded waste ratio', text=f"{worst['item']}: {worst['waste_rate']:.2f}% ({worst['waste']} {worst['unit'].lower()} recorded waste / {worst['prepared']:,} prepared). Review its periods and dates to investigate; the cause is not recorded.", href=link(filters, item=worst['item'], view='waste'), action='Inspect waste history'))
    elif focus:
        byperiod = [(p, [r for r in rows if r['period'] == p]) for p in PERIODS if any((r['period'] == p for r in rows))]
        if view == 'overview':
            g = trend(rows, ['prepared', 'sold'], filters, operating, unit)
            panel('Preparation and recorded sales', ('Weekly mean of observed daily totals; week labels are Mondays. Boundary weeks use only selected dates, and closed weeks are gaps.' if g and g['weekly'] else 'Daily totals; closed dates omitted, missing operating dates shown as gaps.') + f' Unit: {unit.lower()}.', g, True)
            weekdays = []
            daily = daily_groups(rows)
            for day in WEEKDAYS:
                vals = [sum((r['sold'] for r in g)) for d, g in daily.items() if date.fromisoformat(d).strftime('%A') == day]
                if vals:
                    weekdays.append(dict(label=day, value=mean(vals), context=f'{len(vals)} observed dates'))
            panel('Typical daily sales by weekday', f'Mean sold per observed date in {unit.lower()}; all selected periods combined.', horizontal(weekdays, f'Mean daily sold · {unit}'))
            panel('Where did preparation go?', 'Parts of this item’s total preparation: sold, recorded waste, and remaining leftover. Remaining leftover disposition is unknown.', pie([('Sold', focus['sold']), ('Recorded waste', focus['waste']), ('Remaining leftover', focus['leftover'] - focus['waste'])], 'Prepared', unit))
            panel('Distribution of daily sales', f'Each observation is the total recorded sales for one date across selected periods, in {unit.lower()}. Bar heights count observed dates; missing dates are not zeros.', histogram([sum((r['sold'] for r in g)) for g in daily.values()], unit))
            panel('Preparation versus recorded sales', f'One point = one date × selling period for this item. Dashed diagonal: sold equals prepared. Quantities in {unit.lower()}; gaps below the diagonal are leftovers. This is not proof of independent demand.', item_scatter(rows, unit))
            if len(byperiod) > 1:
                panel('Typical sales by selling period', f'Mean sold per observed date in {unit.lower()}, with actual observation counts. This compares periods within the same item.', horizontal([dict(label=p, value=mean((r['sold'] for r in g)), context=f'{len(g)} observed dates') for p, g in byperiod], f'Mean sold · {unit}'))
            else:
                recurrence = [dict(label='All preparation sold', value=sum((r['prepared'] > 0 and r['sold'] == r['prepared'] for r in rows))), dict(label='Leftover, zero recorded waste', value=sum((r['prepared'] > r['sold'] and r['waste'] == 0 for r in rows))), dict(label='Leftover with recorded waste', value=sum((r['prepared'] > r['sold'] and r['waste'] > 0 for r in rows)))]
                if any((r['prepared'] == 0 for r in rows)):
                    recurrence.append(dict(label='Zero preparation', value=sum((r['prepared'] == 0 for r in rows))))
                for e in recurrence:
                    e['context'] = f"{e['value']} / {len(rows)} records ({100 * e['value'] / len(rows):.1f}%)"
                panel('How often was food unsold or wasted?', 'Mutually exclusive record outcomes; bar lengths count records. Quantity composition in the pie answers a different question.', horizontal(recurrence, 'Observed record count'))
        elif view == 'patterns':
            panel('Sales distribution by selling period', f'Minimum to maximum with median and Q1–Q3. One observation = one date × period; unit: {unit.lower()}. These are descriptive ranges, not prediction intervals.', distribution(rows, unit), True)
            panel('Weekday × selling-period sales', f'Mean sales in {unit.lower()} with observed-date counts. Compare this item’s service periods; blank means no observations.', heatmap(rows, filters, units), True)
            months = defaultdict(list)
            for d, g in daily_groups(rows).items():
                months[d[:7]].append(sum((r['sold'] for r in g)))
            panel('Typical daily sales by month', 'Mean of daily totals, not monthly totals; counts allow for different numbers of operating dates.', horizontal([dict(label=date.fromisoformat(m + '-01').strftime('%b %Y'), value=mean(v), context=f'{len(v)} observed dates') for m, v in sorted(months.items())], f'Mean daily sold · {unit}'), True)
        else:
            g = trend(rows, ['leftover', 'waste'], filters, operating, unit)
            panel('Leftover and recorded waste', ('Weekly mean of observed daily totals; Monday week labels, selected dates only. Closed weeks are gaps.' if g and g['weekly'] else 'Daily totals; closed dates omitted.') + f' Unit: {unit.lower()}. Remaining leftover disposition is unknown.', g, True)
            panel('Weekday × selling-period waste rates', 'Recorded waste / prepared (%) from quantity totals in each cell. Counts show observed dates; blank means no denominator.', heatmap(rows, filters, units, 'weekday_waste'), True)
            panel('Frequency of records with waste', 'Number of records with positive waste / observed records in that period. Frequency and quantity are different measures.', dot_chart([dict(label=p, value=ratio(sum((r['waste'] > 0 for r in g)), len(g)), context=f"{sum((r['waste'] > 0 for r in g))} / {len(g)} records") for p, g in byperiod], 'Records with waste'))
        if focus['eligible']:
            insights.append(dict(title='Full-sale records', text=f"Sold equals prepared in {focus['full_sale']} / {focus['eligible']} eligible records ({focus['full_sale_rate']:.1f}%). Review availability notes before interpreting this as unmet demand.", href=link(filters, item=filters['item'], endpoint='/'), action='Check supporting records'))
        if focus['waste_rate'] is not None:
            insights.append(dict(title='Unsold quantity and recorded waste', text=f"{focus['leftover']:,} {unit.lower()} were unsold; {focus['waste']:,} were recorded as waste. The disposition of the remaining {focus['leftover'] - focus['waste']:,} is unknown. Waste / prepared: {focus['waste_rate']:.2f}%.", href=link(filters, view='waste'), action='Inspect waste patterns'))
    if selected and rows and (view == 'patterns'):
        daily = daily_groups(rows)
        weekday_evidence = []
        for day in WEEKDAYS:
            vals = [sum((r['sold'] for r in g)) for d, g in daily.items() if date.fromisoformat(d).strftime('%A') == day]
            if vals:
                weekday_evidence.append(dict(day=day, n=len(vals), value=mean(vals)))
        if len(weekday_evidence) > 1:
            high = max(weekday_evidence, key=lambda x: x['value'])
            low = min(weekday_evidence, key=lambda x: x['value'])
            insights.insert(0, dict(title='Observed weekday contrast', text=f"{high['day']} averages {high['value']:.1f} {unit.lower()} sold per date (n={high['n']}); {low['day']} averages {low['value']:.1f} (n={low['n']}). These averages describe this scope, not a proven weekday effect."))
    if selected and rows and (view == 'waste'):
        period_evidence = []
        for p in PERIODS:
            g = [r for r in rows if r['period'] == p]
            s = summary(g)
            if s['waste_rate'] is not None:
                period_evidence.append(dict(period=p, **s))
        if period_evidence:
            high = max(period_evidence, key=lambda x: x['waste_rate'])
            insights.insert(0, dict(title='Selling period to review', text=f"{high['period']}: waste / prepared is {high['waste_rate']:.2f}% ({high['waste']} / {high['prepared']} {unit.lower()}); positive waste appears in {high['waste_records']} / {high['count']} records. Cause and disposition require operational follow-up.", href=link(filters, period=high['period'], view='waste'), action='Review this period'))
    comparable = [r for r in compare['rows'] if r['change'] is not None]
    if comparable:
        c = max(comparable, key=lambda r: abs(r['change']))
        insights.insert(0, dict(title='Recent sales movement', text=f"{c['item']}: mean daily sold changed {c['change']:+.1f}% ({c['prior']['mean_daily']:.1f} → {c['recent']['mean_daily']:.1f} {c['unit'].lower()}) across two {compare['n']}-operating-date windows. This does not identify the reason.", href=link(filters, item=c['item'], view='patterns'), action='Explore sales patterns'))
    exceptions = sorted([dict(r, leftover=r['prepared'] - r['sold'], waste_rate=ratio(r['waste'], r['prepared']), href=link(dict(filters, start=r['day'], end=r['day']), item=r['item'], period=r['period'], view='waste')) for r in rows if r['prepared'] > 0 and r['waste'] > 0], key=lambda r: (-r['waste_rate'], r['day'], r['item'], r['period']))[:10]
    eligible = sum((t['eligible'] for t in totals))
    full = sum((t['full_sale'] for t in totals))
    for p in charts:
        if p['graph']['kind'] == 'outcomes':
            for e in p['graph']['entries']:
                e['unit'] = unit if selected else units[e['label']]
    data.update(charts=charts, insights=insights[:4], comparison=compare, exceptions=exceptions, portfolio=dict(median_sell=median([t['sell_rate'] for t in totals if t['sell_rate'] is not None]) if any((t['sell_rate'] is not None for t in totals)) else None, median_waste=median([t['waste_rate'] for t in totals if t['waste_rate'] is not None]) if any((t['waste_rate'] is not None for t in totals)) else None, full_rate=ratio(full, eligible), full=full, eligible=eligible, item_count=len(totals)))
    return data
