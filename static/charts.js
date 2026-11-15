/* Resize the native SVG coordinate system to its container: no compressed plot. */
document.querySelectorAll('svg.line-chart').forEach(svg=>{
 const graph=JSON.parse(svg.dataset.chart),readout=svg.parentElement.querySelector('.chart-readout');
 const originals=new Map();let width=780,chosen=0;
 svg.querySelectorAll('[x],[x1],[x2],[cx],[points]').forEach(el=>{
  const attrs={};for(const key of ['x','x1','x2','cx','points','width'])if(el.hasAttribute(key))attrs[key]=el.getAttribute(key);
  originals.set(el,attrs);
 });
 const toX=x=>60+(x-70)*(width-85)/650;
 const pointByDate=new Map();graph.paths.forEach(p=>p.points.forEach(v=>pointByDate.set(v.day,v)));
 const dates=[...pointByDate.keys()].sort();
 const picker=svg.parentElement.querySelector('.chart-date-picker');
 if(picker){
  dates.forEach(day=>picker.add(new Option(day,day)));
  picker.addEventListener('change',()=>show(picker.selectedIndex));
 }
 const show=idx=>{
  if(!dates.length)return;
  chosen=Math.max(0,Math.min(dates.length-1,idx));const day=dates[chosen];
  if(picker)picker.selectedIndex=chosen;
  const hits=graph.paths.map(p=>({name:p.name,point:p.points.find(v=>v.day===day&&(!p.dashed||v.day>graph.cutoff))})).filter(p=>p.point);
  readout.textContent=day+' · '+hits.map(h=>h.name+': '+h.point.value.toLocaleString(undefined,{maximumFractionDigits:2})+' '+graph.name).join(' · ')+(graph.context?.[day]?' · '+graph.context[day]:'');
  const x=toX(pointByDate.get(day).x),guide=svg.querySelector('.hover-guide');guide.setAttribute('x1',x);guide.setAttribute('x2',x);guide.setAttribute('visibility','visible');
 };
 const resize=()=>{
  width=Math.max(280,svg.clientWidth);svg.setAttribute('viewBox',`0 0 ${width} 295`);
  originals.forEach((attrs,el)=>Object.entries(attrs).forEach(([key,val])=>{
   if(key==='points')el.setAttribute(key,val.split(' ').map(p=>{const [x,y]=p.split(',');return toX(Number(x))+','+y}).join(' '));
   else el.setAttribute(key,key==='width'?Number(val)*(width-85)/650:toX(Number(val)));
  }));
  svg.querySelectorAll('.x-tick').forEach((el,i)=>el.style.display=width<460&&i>0&&i<graph.ticks.length-1?'none':'');
  if(svg.querySelector('.hover-guide').getAttribute('visibility')==='visible')show(chosen);
 };
 new ResizeObserver(resize).observe(svg);resize();
 const inspect=event=>{
  const rect=svg.getBoundingClientRect(),x=(event.clientX-rect.left)*width/rect.width;
  let best=0,dist=Infinity;
  dates.forEach((d,i)=>{const point=pointByDate.get(d);const delta=Math.abs(toX(point.x)-x);if(delta<dist){dist=delta;best=i}});show(best);
 };
 svg.addEventListener('pointermove',inspect);
 svg.addEventListener('pointerdown',inspect);
 svg.addEventListener('keydown',event=>{
  if(event.key==='ArrowRight'||event.key==='ArrowLeft'){event.preventDefault();show(chosen+(event.key==='ArrowRight'?1:-1))}
 });
 svg.addEventListener('focus',()=>show(chosen));
 show(dates.length-1);
});
// Native scatter/histogram plots preserve label size as their container changes.
document.querySelectorAll('svg.basic-chart').forEach(svg=>{
 const originals=new Map(),marks=[...svg.querySelectorAll('.inspect-mark')];
 const readout=svg.parentElement.querySelector('.chart-readout');let selected=0;
 svg.querySelectorAll('[x],[x1],[x2],[cx],[width]').forEach(el=>{
  const attrs={};for(const k of ['x','x1','x2','cx','width'])if(el.hasAttribute(k))attrs[k]=Number(el.getAttribute(k));originals.set(el,attrs);
 });
 const resize=()=>{
  const width=Math.max(280,svg.clientWidth),scale=(width-85)/400;
  svg.setAttribute('viewBox',`0 0 ${width} 290`);
  originals.forEach((attrs,el)=>Object.entries(attrs).forEach(([key,val])=>el.setAttribute(key,key==='width'?val*scale:60+(val-60)*scale)));
 };
 new ResizeObserver(resize).observe(svg);resize();
 const show=mark=>{if(mark)readout.textContent=mark.dataset.description};
 marks.forEach(mark=>{mark.addEventListener('pointerenter',()=>show(mark));mark.addEventListener('pointerdown',()=>show(mark))});
 svg.addEventListener('keydown',event=>{
  if(event.key==='ArrowRight'||event.key==='ArrowLeft'){event.preventDefault();selected=(selected+(event.key==='ArrowRight'?1:-1)+marks.length)%marks.length;show(marks[selected])}
 });
 svg.addEventListener('focus',()=>show(marks[0]));
});
