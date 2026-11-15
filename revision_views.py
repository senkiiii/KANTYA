"""KANTYA finalized operational views. Data source is explicitly simulated."""
import csv
import io
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo
from flask import current_app, abort, request, render_template, Response, session
from database import db
from operating_calendar import MENU, ITEMS, menu_allowed, is_operating_day

PALETTE=['#00a65a','#38b879','#70c999','#a4ddbb','#d7eee0']
WASTE_PALETTE=['#f07824','#f49652','#f7b17f','#fad0b0','#fce8d8']
DATA=Path(__file__).resolve().parent/'data'
with (DATA/'FIXED_MENU_PRICES_COSTS.csv').open(encoding='utf-8-sig',newline='') as f:
    PRICE={r['food_item_name']:(float(r['selling_price_php']),float(r['unit_preparation_cost_php'])) for r in csv.DictReader(f)}

def money(value):return '₱'+format(value,',.0f')
def all_records():
    with db() as c:return [dict(r) for r in c.execute('SELECT * FROM records ORDER BY day,item,period')]
def enrich(rows):
    result=[]
    for row in rows:
        row=dict(row)
        price,cost=PRICE.get(row['item'],(0,0))
        row.update(category=next((r['category'] for r in MENU if r['food_item_name']==row['item']),''),
                   unit=ITEMS.get(row['item'],'unit'),revenue=row['sold']*price,
                   preparation_cost=row['prepared']*cost,
                   waste_cost=row['waste']*cost,
                   contribution=row['sold']*price-row['prepared']*cost,
                   leftover=row['prepared']-row['sold'])
        result.append(row)
    return result

def totals(rows):
    return {k:sum(r[k] for r in rows) for k in ('prepared','sold','waste','leftover','revenue','preparation_cost','contribution','waste_cost')}

def rank(rows,metric='sold',n=5):
    agg=defaultdict(float)
    for r in rows:agg[r['item']]+=r[metric]
    return sorted(agg.items(),key=lambda x:(-x[1],x[0]))[:n]

def chart_svg(hist,forecast,mode='monthly',width=1060,height=350):
    """Full series data retained; labels collision-aware and every point has a title tooltip."""
    width=max(width,52*(len(hist)+len(forecast)) if mode=='daily' else 22*(len(hist)+len(forecast)) if mode=='weekly' else width)
    W,H=width,height;left,right,top,bottom=54,20,38,54; plot=W-left-right;bottom_y=H-bottom
    points=hist+forecast; count=len(points)
    if not count:return '<p class="helper">No history or forecast is available for this selection.</p>'
    max_y=max([1]+[max(float(x.get('prepared',0)),float(x.get('sold',0))) for x in points])*1.13
    step=plot/max(count,1);barwidth=max(5,min(22,step*.44))
    def y(v):return round(bottom_y-(float(v)/max_y)*(bottom_y-top),2)
    def x(i):return round(left+step*(i+.5),2)
    boundary=left+step*len(hist)
    parts=[f'<svg viewBox="0 0 {W} {H}" style="width:{W}px;max-width:none" class="kantya-svg" role="img" aria-label="Historical preparation and sales with highlighted future forecast and direct labels">']
    parts.append(f'<rect x="{boundary:.1f}" y="18" width="{max(0,W-right-boundary):.1f}" height="{bottom_y-15}" rx="6" fill="#fff2df"/>')
    for i in range(5):
        v=max_y*i/4; yy=y(v)
        parts.extend([f'<line x1="{left}" x2="{W-right}" y1="{yy}" y2="{yy}" stroke="#e4eae5"/>',f'<text x="{left-8}" y="{yy+4}" text-anchor="end" font-size="11" fill="#64746a">{v:,.0f}</text>'])
    parts.append(f'<line x1="{boundary:.1f}" x2="{boundary:.1f}" y1="30" y2="{bottom_y}" stroke="#de8a2f" stroke-width="2" stroke-dasharray="5 5"/>')
    if forecast:parts.append(f'<text x="{(boundary+W-right)/2:.1f}" y="30" font-size="12" text-anchor="middle" font-weight="700" fill="#ad651b">FORECAST PERIOD</text>')
    lines={'hist':[],'future':[]}
    for i,p in enumerate(points):
        fx=x(i); prep=float(p.get('prepared',0));sold=float(p.get('sold',0)); isforecast=i>=len(hist)
        tint='#f4ac69' if isforecast else '#91a69b'; tip=escape(f"{p['label']}: {'Suggested preparation' if isforecast else 'Actual prepared'} {prep:,.0f}; {'Forecast sold' if isforecast else 'Actual sold'} {sold:,.0f}")
        parts.append(f'<rect x="{fx-barwidth/2:.1f}" y="{y(prep)}" width="{barwidth:.1f}" height="{bottom_y-y(prep):.1f}" rx="2" fill="{tint}" opacity=".72" data-tooltip="{tip}"><title>{tip}</title></rect>')
        lines['future' if isforecast else 'hist'].append((fx,y(sold),p))
        if isforecast or (len(hist)<=15 or i%max(1,math.ceil(len(hist)/9))==0):
            parts.append(f'<text x="{fx:.1f}" y="{max(45,y(prep)-6):.1f}" font-size="10" font-weight="600" fill="#53685a" text-anchor="middle">{prep:,.0f}</text>')
        if isforecast or i%max(1,math.ceil(count/12))==0 or i==len(hist)-1:
            parts.append(f'<text x="{fx:.1f}" y="{H-29}" font-size="10" fill="#607266" text-anchor="middle">{escape(p["label"][-7:])}</text>')
    for key,color in [('hist','#148456'),('future','#e66921')]:
        row=lines[key]; anchor=lines['hist'][-1:] if key=='future' and hist else []
        line=anchor+row
        if len(line)>1:parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" points="'+ ' '.join(f'{xx},{yy}' for xx,yy,_ in line)+'"/>')
        for j,(xx,yy,p) in enumerate(row):
            parts.append(f'<circle cx="{xx}" cy="{yy}" r="3.7" fill="{color}" data-tooltip="{escape(p["label"])}: {p["sold"]:,.0f} units sold"><title>{escape(p["label"])}: {p["sold"]:,.0f} units sold</title></circle>')
            if key=='future' or (len(hist)<=12 or j%max(1,math.ceil(len(hist)/8))==0):
                parts.append(f'<text x="{xx}" y="{max(43,yy-16)}" text-anchor="middle" font-size="11" font-weight="700" fill="{color}" paint-order="stroke" stroke="white" stroke-width="3">{p["sold"]:,.0f}</text>')
    parts.append('</svg>')
    return ''.join(parts)

def revenue_svg(months):
    if not months:return '<p class="rev-note">No scheduled future projected revenue.</p>'
    W,H=550,265; x0=45; y_bottom=222; ceiling=max(1,max(m['total'] for m in months))*1.22
    gap=(W-75)/len(months); barwidth=min(49,gap*.52)
    def y(v):return y_bottom-float(v)/ceiling*178
    elements=[f'<svg class="kantya-svg" viewBox="0 0 {W} {H}" role="img" aria-label="Projected normal and event-uplift revenue stacked bars with total revenue line">']
    for n in range(5):
        v=ceiling*n/4;yy=y(v)
        elements.append(f'<line x1="40" x2="{W-10}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#e2eae4"/><text x="36" y="{yy+4:.1f}" text-anchor="end" font-size="9" fill="#688074">{v/1000:.0f}k</text>')
    line=[]
    for i,m in enumerate(months):
        x=x0+gap*(i+.5); base=m['base'];uplift=m['uplift'];total=m['total']
        y_base=y(base);y_total=y(total)
        elements.append(f'<rect x="{x-barwidth/2:.1f}" y="{y_base:.1f}" width="{barwidth:.1f}" height="{y_bottom-y_base:.1f}" rx="2" fill="#168052" data-tooltip="{m["month"]}: normal PHP {base:,.0f}"><title>{m["month"]}: normal PHP {base:,.0f}</title></rect>')
        if uplift:
            elements.append(f'<rect x="{x-barwidth/2:.1f}" y="{y_total:.1f}" width="{barwidth:.1f}" height="{y_base-y_total:.1f}" fill="#eea66a" data-tooltip="Event uplift PHP {uplift:,.0f}"><title>Hypothetical event addition PHP {uplift:,.0f}</title></rect>')
            elements.append(f'<text x="{x:.1f}" y="{max(30,(y_base+y_total)/2+3):.1f}" text-anchor="middle" font-size="9" fill="#784516">+{uplift/1000:.0f}k</text>')
        line.append((x,y_total,total))
        elements.append(f'<text x="{x:.1f}" y="242" text-anchor="middle" font-size="11" fill="#536c5d">{escape(m["month"])}</text>')
    elements.append('<polyline fill="none" stroke="#c85d1c" stroke-width="2.6" points="'+' '.join(f'{x:.1f},{yy:.1f}' for x,yy,_ in line)+'"/>')
    for x,yy,v in line:
        elements.append(f'<circle cx="{x:.1f}" cy="{yy:.1f}" r="3.3" fill="#c85d1c" data-tooltip="Projected total PHP {v:,.0f}"><title>Projected total PHP {v:,.0f}</title></circle>')
        elements.append(f'<text x="{x:.1f}" y="{max(20,yy-9):.1f}" text-anchor="middle" font-size="10" font-weight="700" fill="#b45418" paint-order="stroke" stroke="white" stroke-width="3">{v/1000:,.0f}k</text>')
    elements.append('</svg>')
    return ''.join(elements)

def monthly_series(rows):
    groups=defaultdict(lambda:dict(prepared=0,sold=0))
    for r in rows:
        k=r['day'][:7];groups[k]['prepared']+=r['prepared'];groups[k]['sold']+=r['sold']
    return [dict(label=k,**v) for k,v in sorted(groups.items())]

def fixed_menu():
    days=['Monday','Tuesday','Wednesday','Thursday','Friday'];periods=['Breakfast','Lunch','Snack']
    out=[]
    for day in days:
        group={period:[r['food_item_name'] for r in MENU if r['weekday']==day and r['selling_period']==period and r['category']!='Drink'] for period in periods}
        group['Drinks']=list(dict.fromkeys(r['food_item_name'] for r in MENU if r['weekday']==day and r['category']=='Drink'))
        out.append(dict(day=day,**group))
    return out

def filtered_menu():
    """Only menu entries that match the current Dashboard selection."""
    category=request.args.get('category','')
    chosen_item=request.args.get('item','')
    period=request.args.get('period','')
    weekday=request.args.get('weekday','Overall')
    result=[]
    days=['Monday','Tuesday','Wednesday','Thursday','Friday']
    for day in days:
        if weekday not in ('', 'Overall', day):
            continue
        matches=[m for m in MENU if m['weekday']==day
                 and (not category or m['category']==category)
                 and (not chosen_item or m['food_item_name']==chosen_item)
                 and (not period or m['selling_period']==period)]
        if not matches:
            continue
        group={k:[] for k in ('Breakfast','Lunch','Snack','Drinks')}
        for m in matches:
            col='Drinks' if m['category']=='Drink' else m['selling_period']
            if col in group and m['food_item_name'] not in group[col]:
                group[col].append(m['food_item_name'])
        result.append(dict(day=day,**group))
    return result

def projected_rows(horizon,chosen_item='',category=''):
    """Rolling item-period demand estimates from current saved synthetic transaction data.

    A recent-eight level and clipped short-term trend supply a transparent planning
    baseline; projected values change whenever the records are corrected.
    """
    import calendar
    history=all_records()
    past=defaultdict(list)
    for r in history:
        past[(r['item'],r['period'])].append(r)
    for entries in past.values():entries.sort(key=lambda r:r['day'])
    today=datetime.now(ZoneInfo('Asia/Manila')).date()
    latest=max((date.fromisoformat(r['day']) for r in history),default=today)
    start=max(today,latest)+timedelta(days=1)
    month=today.month-1+horizon;year=today.year+month//12;month=month%12+1
    stop=date(year,month,min(today.day,calendar.monthrange(year,month)[1]))
    output=[]; d=start
    while d < stop:
        if is_operating_day(d):
            weekday=d.strftime('%A')
            for menu in MENU:
                if menu['weekday']!=weekday:continue
                item,period=menu['food_item_name'],menu['selling_period']
                if chosen_item and item!=chosen_item:continue
                if category and menu['category']!=category:continue
                history_group=past.get((item,period),[])
                if not history_group:continue
                vals=[r['sold'] for r in history_group]
                recent=vals[-8:];level=sum(recent)/len(recent)
                prev=vals[-8:-4] if len(vals)>=8 else recent
                trend=((sum(recent[-4:])/len(recent[-4:]))-(sum(prev)/len(prev))) if prev else 0
                trend=max(-level*.08,min(level*.08,trend))
                # Half the short trend limits unrealistic long-range extrapolation.
                sold=max(0,round(level+trend*.5))
                prepared=math.ceil(sold*1.08)
                price,cost=PRICE.get(item,(0,0))
                output.append(dict(day=d.isoformat(),item=item,period=period,unit=ITEMS[item],category=menu['category'],sold=sold,prepared=prepared,revenue=sold*price,contribution=sold*price-prepared*cost))
        d+=timedelta(days=1)
    return output

def scope_records(rows):
    cat=request.args.get('category','');item=request.args.get('item','');period=request.args.get('period','');weekday=request.args.get('weekday','Overall')
    if cat:rows=[r for r in rows if r['category']==cat]
    if item:rows=[r for r in rows if r['item']==item]
    if period:rows=[r for r in rows if r['period']==period]
    if weekday and weekday!='Overall':rows=[r for r in rows if date.fromisoformat(r['day']).strftime('%A')==weekday]
    return rows

def dashboard():
    history=enrich(all_records());rows=scope_records(history);t=totals(rows)
    item_stats=[]
    for item in sorted({r['item'] for r in rows}):
        group=[r for r in rows if r['item']==item];v=totals(group)
        item_stats.append(dict(item=item,unit=ITEMS[item],**v))
    item_stats.sort(key=lambda r:-r['sold'])
    sold_ranking=rank(rows,'sold')
    waste_metric=request.args.get('waste_metric','cost')
    if waste_metric not in ('cost','rate'):waste_metric='cost'
    if waste_metric=='rate':
        waste_ranking=sorted([(r['item'],(100*r['waste']/r['prepared'] if r['prepared'] else 0)) for r in item_stats],key=lambda x:(-x[1],x[0]))[:5]
    else:waste_ranking=rank(rows,'waste_cost')
    horizon=5;future=projected_rows(horizon,request.args.get('item',''),request.args.get('category',''))
    if request.args.get('weekday','Overall')!='Overall':future=[r for r in future if date.fromisoformat(r['day']).strftime('%A')==request.args['weekday']]
    if request.args.get('period'):future=[r for r in future if r['period']==request.args['period']]
    with (DATA/'SIMULATED_EVENT_CALENDAR.csv').open(encoding='utf-8-sig',newline='') as source:
        events=[r for r in csv.DictReader(source) if r['status']=='hypothetical future scenario']
    future_t=defaultdict(lambda:dict(sold=0,base=0,uplift=0))
    for r in future:
        group=future_t[r['day'][:7]];group['sold']+=r['sold'];group['base']+=r['revenue']
        applicable=[e for e in events if e['start_date']<=r['day']<=e['end_date']]
        if applicable:
            group['uplift']+=r['revenue']*float(applicable[0]['uplift_assumption_percent'])/100
    month_forecasts=[dict(month=k,**v,total=v['base']+v['uplift']) for k,v in sorted(future_t.items())]
    prepared=t['prepared'];sellthrough=t['sold']/prepared*100 if prepared else None
    if item_stats:
        leader=item_stats[0];worst=max(item_stats,key=lambda r:r['waste_cost']);insight=(f"Across the selected records, {t['sold']:,} items were sold and {money(t['revenue'])} in simulated revenue was recorded. "
          f"{leader['item']} leads the selected sales ranking with {leader['sold']:,} items sold. "
          f"Sell-through is {sellthrough:.1f}% of prepared items." if sellthrough is not None else 'No prepared quantities were recorded.')
        insight+=f" {worst['item']} has the highest selected waste cost at {money(worst['waste_cost'])}. "
        insight+=f"The forward scenario covers {len(future):,} scheduled item-date forecasts, but actual campus activity can differ from these assumptions. Review preparation quantities before upcoming service dates and monitor items with the greatest recorded waste to limit avoidable losses."
    else:insight='No matching records. Adjust the filters before interpreting sales, waste or revenue.'
    if session['role']=='Staff':
        insight=(f"In the selected records, {t['sold']:,} items were sold from {t['prepared']:,} prepared. "
                 f"{item_stats[0]['item']} leads the sold-quantity ranking with {item_stats[0]['sold']:,} items. " if item_stats else 'No recorded food-item observations match this view. ')
        insight+=f"The recorded actual waste includes {t['waste']:,} items. Check the daily menu and incomplete entries before the next service period. Compare prepared and sold quantities to avoid excessive preparation. Use read-only forecast guidance for upcoming scheduled menu items."
    return render_template('rev_dashboard.html',rows=rows,totals=t,sellthrough=sellthrough,ranking=sold_ranking,waste_ranking=waste_ranking,food=item_stats,menu=filtered_menu(),months=month_forecasts,revenue_graph=revenue_svg(month_forecasts),waste_metric=waste_metric,insight=insight,items=ITEMS,categories=sorted({r['category'] for r in MENU}),item_categories={m['food_item_name']:m['category'] for m in MENU},filters=request.args,role=session['role'],develop=True)

def forecast():
    history=enrich(all_records());cat=request.args.get('category','');item=request.args.get('item','');horizon=request.args.get('horizon','5');grain=request.args.get('grain','monthly')
    if session['role']!='Manager':
        cat='';item='';horizon='5';grain='monthly'
    if horizon not in ('3','5') or grain not in ('monthly','weekly','daily') or (item and item not in ITEMS):abort(400)
    history=[r for r in history if (not item or r['item']==item) and (not cat or r['category']==cat)]
    future=projected_rows(int(horizon),item,cat)
    def grouped(rows,mode):
        group=defaultdict(lambda:dict(prepared=0,sold=0))
        for r in rows:
            d=date.fromisoformat(r['day']);key=(d.isoformat() if mode=='daily' else (d-timedelta(days=d.weekday())).isoformat() if mode=='weekly' else d.strftime('%Y-%m'))
            group[key]['prepared']+=r['prepared'];group[key]['sold']+=r['sold']
        return [dict(label=k,**v) for k,v in sorted(group.items())]
    hist=grouped(history,grain);proj=grouped(future,grain)
    if hist and proj and hist[-1]['label']==proj[0]['label']:
        hist[-1]['label']+=' A'
        proj[0]['label']+=' F'
    svg=chart_svg(hist,proj,grain)
    t=totals(history);ft=dict(sold=sum(r['sold'] for r in future),prepared=sum(r['prepared'] for r in future),revenue=sum(r['revenue'] for r in future),contribution=sum(r['contribution'] for r in future))
    itembreak=defaultdict(lambda:dict(sold=0,prepared=0,revenue=0,contribution=0))
    for r in future:
        dst=itembreak[r['item']]
        for key in dst:dst[key]+=r[key]
    itemrows=[dict(item=item,unit=ITEMS[item],**v) for item,v in itembreak.items()]
    itemrows.sort(key=lambda r:-r['sold'])
    upcoming=sorted(future,key=lambda r:(r['day'],r['item']))[:10]
    performance=t['sold']/t['prepared']*100 if t['prepared'] else None
    highest=itemrows[0]['item'] if itemrows else 'No item'
    insights=(f"The selected historical records include {t['sold']:,} recorded items sold and {t['prepared']:,} prepared. "
              f"The simulated future scenario expects {ft['sold']:,} items sold across scheduled offerings within {horizon} months. "
              f"{highest} has the highest projected sold quantity within the current selection. "
              f"A planning buffer of 8% is applied separately to each item-date before preparation quantities are combined. "
              f"Historical sell-through is {performance:.1f}%; compare preparation and sales to identify overproduction or possible shortages. " if performance is not None else 'No valid preparation comparison is available. ')
    insights+='Review the nearest scheduled dates before preparing food, and treat future quantities and financial outcomes as simulated estimates rather than guaranteed demand.'
    return render_template('rev_forecast.html',ft=ft,history=hist,projection=proj,svg=svg,itemrows=itemrows,upcoming=upcoming,insight=insights,items=ITEMS,categories=sorted({r['category'] for r in MENU}),item_categories={m['food_item_name']:m['category'] for m in MENU},filters=request.args,role=session['role'],horizon=horizon,grain=grain,scenario_end='2027-04-11')

def report_rows():return enrich(all_records())
def report_summary(rows):
    per=defaultdict(list)
    for r in rows:per[r['item']].append(r)
    return sorted([dict(item=k,unit=ITEMS[k],**totals(v)) for k,v in per.items()],key=lambda x:x['item'])

def reports():
    if session['role']!='Manager':abort(403)
    tab=request.args.get('tab','reports')
    if tab not in ('reports','users'):abort(400)
    if tab=='users':
        with db() as c:
            users=[dict(r) for r in c.execute("SELECT id,email,username,role,status FROM users ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,role,email")]
        return render_template('rev_reports.html',tab=tab,users=users)
    rows=report_rows(); summary=totals(rows)
    return render_template('rev_reports.html',tab=tab,coverage=(rows[0]['day'],rows[-1]['day']) if rows else ('—','—'),summary=summary,items=report_summary(rows),count=len(rows))

def report_export(fmt):
    if session['role']!='Manager':abort(403)
    if fmt not in ('csv','pdf'):abort(404)
    rows=report_rows()
    columns=['day','item','period','prepared','sold','leftover','waste','revenue','preparation_cost','contribution','waste_cost']
    if fmt=='csv':
        f=io.StringIO();writer=csv.writer(f);writer.writerow(columns)
        for r in rows:writer.writerow([r[c] for c in columns])
        return Response(('\ufeff'+f.getvalue()).encode('utf-8'),mimetype='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="KANTYA_Canteen_Operations.csv"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    try:
        from reportlab.lib.pagesizes import A4
    except ImportError:
        return minimal_pdf_export(rows)
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Table,TableStyle,Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    out=io.BytesIO();doc=SimpleDocTemplate(out,pagesize=A4,leftMargin=35,rightMargin=35,topMargin=38,bottomMargin=35)
    styles=getSampleStyleSheet(); elements=[Paragraph('KANTYA | Canteen Operations Report',styles['Title']),Spacer(1,12)]
    t=totals(rows); coverage=f'{rows[0]["day"]} to {rows[-1]["day"]}' if rows else 'No records'
    elements.append(Paragraph('Coverage: '+coverage+' | Records: '+str(len(rows)),styles['Normal']))
    for label,value in [('Prepared',format(t['prepared'],',')),('Sold',format(t['sold'],',')),('Actual waste',format(t['waste'],',')),('Revenue',money(t['revenue'])),('Preparation Cost',money(t['preparation_cost'])),('Estimated Contribution',money(t['contribution'])),('Waste Cost',money(t['waste_cost']))]:
        elements.append(Paragraph(label+': '+value.replace('₱','PHP '),styles['Normal']))
    elements.append(Spacer(1,14));elements.append(Paragraph('Food Item Breakdown',styles['Heading2']))
    table=[['Food item','Prepared','Sold','Waste','Revenue (PHP)','Contribution (PHP)']]
    for r in report_summary(rows):table.append([r['item'],str(r['prepared']),str(r['sold']),str(r['waste']),format(r['revenue'],',.0f'),format(r['contribution'],',.0f')])
    grid=Table(table,repeatRows=1,colWidths=[140,62,55,55,92,92],hAlign='LEFT')
    grid.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#DCEDE3')),('GRID',(0,0),(-1,-1),.25,colors.HexColor('#BBD1C3')),('FONTSIZE',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7)]))
    elements.append(grid)
    try:
        doc.build(elements)
    except Exception:
        current_app.logger.exception('PDF rendering failed; using accessible fallback document')
        return minimal_pdf_export(rows)
    return Response(out.getvalue(),mimetype='application/pdf',headers={'Content-Disposition':'attachment; filename="KANTYA_Canteen_Operations.pdf"','Cache-Control':'no-store'})


def minimal_pdf_export(rows):
    """Readable, dependency-free PDF fallback for installations missing reportlab."""
    t=totals(rows)
    summary=["KANTYA | Canteen Operations Report", "",f"Records: {len(rows):,}"]
    if rows: summary.append(f"Coverage: {rows[0]['day']} to {rows[-1]['day']}")
    summary.extend([f"{label}: {v:,.0f}" for label,v in [
        ('Prepared',t['prepared']),('Sold',t['sold']),('Leftover',t['leftover']),
        ('Actual Waste',t['waste']),('Revenue (PHP)',t['revenue']),
        ('Preparation Cost (PHP)',t['preparation_cost']),
        ('Estimated Contribution (PHP)',t['contribution']),('Waste Cost (PHP)',t['waste_cost'])]])
    summary+=['','FOOD ITEM BREAKDOWN','Item | Prepared | Sold | Waste | Revenue PHP | Contribution PHP']
    for v in report_summary(rows):
        summary.append(f"{v['item']} | {v['prepared']:,} | {v['sold']:,} | {v['waste']:,} | {v['revenue']:,.0f} | {v['contribution']:,.0f}")
    chunks=[summary[i:i+37] for i in range(0,len(summary),37)]
    objects=[None,None,'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    pages=[]
    for chunk in chunks:
        content=['BT','/F1 9 Tf','38 795 Td','13 TL']
        for line in chunk:
            safe=line.encode('ascii','replace').decode('ascii').replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
            content.append(f'({safe}) Tj T*')
        content.append('ET')
        stream='\n'.join(content).encode('ascii')
        content_obj=len(objects)+1;objects.append(f'<< /Length {len(stream)} >>\nstream\n'+stream.decode('ascii')+'\nendstream')
        page_obj=len(objects)+1;objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_obj} 0 R >>')
        pages.append(f'{page_obj} 0 R')
    objects[0]='<< /Type /Catalog /Pages 2 0 R >>'
    objects[1]='<< /Type /Pages /Kids ['+' '.join(pages)+f'] /Count {len(pages)} >>'
    output=b'%PDF-1.4\n';offsets=[0]
    for i,body in enumerate(objects,1):
        offsets.append(len(output));output+=f'{i} 0 obj\n{body}\nendobj\n'.encode('ascii')
    startxref=len(output)
    output+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode('ascii')
    for off in offsets[1:]:output+=f'{off:010d} 00000 n \n'.encode('ascii')
    output+=f'trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{startxref}\n%%EOF\n'.encode('ascii')
    return Response(output,mimetype='application/pdf',headers={'Content-Disposition':'attachment; filename="KANTYA_Canteen_Operations.pdf"','Cache-Control':'no-store'})
