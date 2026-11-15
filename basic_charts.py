"""Basic exploratory charts with explicit grains, quantities and denominators."""
from math import ceil, floor, log2
from collections import defaultdict
from analysis import ratio, summary
COLORS = ['#2b7c59', '#cf8a25', '#b7c7be']

def pie(parts, denominator_label, unit):
    total = sum((value for label, value in parts))
    if total <= 0:
        return None
    entries = []
    start = 0
    stops = []
    for i, (label, value) in enumerate(parts):
        share = 100 * value / total
        end = start + share
        color = COLORS[i % len(COLORS)]
        entries.append(dict(label=label, value=value, share=share, color=color))
        stops.append(f'{color} {start:.6f}% {end:.6f}%')
        start = end
    return dict(kind='pie', entries=entries, total=total, unit=unit, denominator=denominator_label, gradient='conic-gradient(' + ','.join(stops) + ')')

def histogram(values, unit, percent=False):
    if not values:
        return None
    count = len(values)
    low = 0 if percent else floor(min(values))
    high = 100 if percent else ceil(max(values)) + 1
    bins = 10 if percent else min(10, max(4, ceil(log2(count) + 1)))
    if high <= low:
        high = low + 1
    step = (high - low) / bins
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, max(0, int((v - low) / step)))] += 1
    maximum = max(ceil(max(counts) / 4) * 4, 4)
    entries = []
    for i, n in enumerate(counts):
        a = low + i * step
        b = low + (i + 1) * step
        entries.append(dict(low=a, high=b, count=n, x=60 + i * 400 / bins, width=400 / bins - 2, y=230 - n / maximum * 180, height=n / maximum * 180, label=f'{a:.1f} ≤ value ' + ('≤ ' if i == bins - 1 else '< ') + f'{b:.1f}: {n} observations'))
    return dict(kind='histogram', entries=entries, n=count, unit=unit, low=low, high=high, ticks=[dict(x=60 + i * 100, value=low + i * (high - low) / 4) for i in range(5)], grid=[dict(y=230 - i * 45, value=i * maximum / 4) for i in range(5)])

def scatter(points, x_name, y_name, unit, equality=False):
    if len(points) < 8:
        return None
    maxx = max(max((p['xvalue'] for p in points)) * 1.1, 1)
    maxy = max(max((p['yvalue'] for p in points)) * 1.1, 1)
    if equality:
        maxx = maxy = max(maxx, maxy)
    for p in points:
        p['x'] = 60 + 400 * p['xvalue'] / maxx
        p['y'] = 230 - 180 * p['yvalue'] / maxy
    return dict(kind='scatter', points=points, n=len(points), unit=unit, x_name=x_name, y_name=y_name, maxx=maxx, maxy=maxy, equality=equality, periods=sorted({p['period'] for p in points}), xticks=[dict(x=60 + i * 100, value=i * maxx / 4) for i in range(5)], yticks=[dict(y=230 - i * 45, value=i * maxy / 4) for i in range(5)])

def item_scatter(rows, unit):
    points = []
    for r in rows:
        points.append(dict(xvalue=r['prepared'], yvalue=r['sold'], period=r['period'], description=f"{r['day']} · {r['period']}: prepared {r['prepared']}, sold {r['sold']}, leftover {r['prepared'] - r['sold']}, waste {r['waste']} {unit}"))
    return scatter(points, 'Prepared', 'Sold', unit, equality=True)

def series_scatter(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[r['item'], r['period']].append(r)
    points = []
    for (item, period), g in sorted(groups.items()):
        s = summary(g)
        if s['sell_rate'] is not None:
            points.append(dict(xvalue=s['sell_rate'], yvalue=s['waste_rate'], period=period, description=f"{item} · {period}: sell-through {s['sell_rate']:.2f}%, waste/prepared {s['waste_rate']:.2f}%, {s['count']} records"))
    g = scatter(points, 'Sell-through (%)', 'Waste / prepared (%)', '%', equality=False)
    if g:
        low = max(0, floor(min((p['xvalue'] for p in points))) - 1)
        high = min(100, ceil(max((p['xvalue'] for p in points))) + 1)
        if high <= low:
            high = low + 1
        for p in g['points']:
            p['x'] = 60 + 400 * (p['xvalue'] - low) / (high - low)
        g['xticks'] = [dict(x=60 + i * 100, value=low + i * (high - low) / 4) for i in range(5)]
        g['focused_x'] = True
    return g
