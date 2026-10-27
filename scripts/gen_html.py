"""Interactive viewer: the SVG map and the ASCII grid side by side, linked by OSM id.
Usage: python3 gen_html.py out/ out/reed_world.html"""
import json, sys, subprocess, html
out, dst = sys.argv[1], sys.argv[2]
subprocess.run([sys.executable, '-I', 'render_svg.py', f'{out}/reed_world.geojson', f'{out}/_embed.svg', '--embed'], check=True)
svg = open(f'{out}/_embed.svg').read()
svg = svg.replace('<svg ', '<svg id="map" role="img" aria-label="Map of Reed College campus" ', 1)
svg = __import__('re').sub(r' width="\d+" height="\d+">', '>', svg, count=1)
w = json.load(open(f'{out}/reed_world.geojson'))
ids = json.load(open(f'{out}/reed_world_ascii_ids.json'))
txt = open(f'{out}/reed_world_ascii.txt').read().split('\n')
start = next(i for i, l in enumerate(txt) if l.startswith('-----')) + 1
rows = txt[start:start + ids['rows']]
info = {}
KIND = {'building': 'Building', 'path': 'Footpath', 'road': 'Street', 'service_road': 'Service road', 'stream': 'Stream',
        'water': 'Water', 'woods': 'Woods', 'scrub': 'Scrub', 'green': 'Lawn / park', 'field': 'Sports field',
        'parking': 'Parking', 'campus_boundary': 'Campus boundary', 'golf': 'Golf course', 'landmark_tree': 'Named tree', 'venue': 'Venue'}
for f in w['features']:
    p = f['properties']
    k = KIND.get(p['kind'], p['kind'])
    if p['kind'] == 'building': k = ('Campus building' if p['realm'] == 'campus' else 'Neighbourhood building')
    info[p['id']] = {k2: v for k2, v in {'n': p.get('name'), 'k': k, 'no': p.get('map_no'), 'c': p.get('category'), 'u': p.get('use'),
                                         'x': p.get('changes'), 'lv': p.get('building:levels')}.items() if v}
data = {'ids': ids['ids'], 'grid': ids['grid'], 'rows': rows, 'info': info}

page = r'''<title>Reed College World Layer</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cormorant+SC:wght@600&family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Mono:wght@400;600&display=swap">
<style>
/* Layout: a survey sheet. Map plate on top, grid plate below, a fixed readout that names whatever is under the cursor in both. */
:root{
  --bg:#ecebe6; --plate:#f4f2ec; --ink:#1b1a18; --mute:#6d6a62; --rule:#c9c6bd; --hi:#b3261e;
  --display:'Cormorant SC',Georgia,serif; --body:'IBM Plex Sans',system-ui,sans-serif; --mono:'IBM Plex Mono',ui-monospace,Menlo,monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#151514;--plate:#f4f2ec;--ink:#e9e7e1;--mute:#a19d93;--rule:#3a3935;--hi:#e0675d;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#151514;--plate:#f4f2ec;--ink:#e9e7e1;--mute:#a19d93;--rule:#3a3935;--hi:#e0675d;color-scheme:dark}
body{background:var(--bg);color:var(--ink);font-family:var(--body);font-size:15px;line-height:1.5}
.wrap{max-width:1320px;margin:0 auto;padding-block:28px 48px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));display:grid;gap:22px}
header{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:8px 24px;border-bottom:1px solid var(--rule);padding-bottom:14px}
h1{font-family:var(--display);font-weight:600;font-size:clamp(28px,4vw,44px);letter-spacing:.06em;margin:0;line-height:1;text-wrap:balance}
.meta{font-family:var(--mono);font-size:12px;color:var(--mute);letter-spacing:.02em}
.lede{max-width:68ch;margin:0;color:var(--mute)}
.grid2{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:22px;align-items:start}
@media (max-width:900px){.grid2{grid-template-columns:minmax(0,1fr)}}
.plates{display:grid;gap:22px;min-width:0}
figure{margin:0;display:grid;gap:8px;min-width:0}
figcaption{font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:var(--mute);display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.plate{background:var(--plate);border:1px solid var(--rule);overflow:hidden}
#map{display:block;width:100%;height:auto}
#map .cb path,#map .nb path,#map .venue{cursor:pointer}
#map .cb path:hover,#map .cb path.sel{fill:#b3261e;stroke:#b3261e}
#map .nb path.sel,#map .nb path:hover{fill:#e4a49d}
#map .path path.sel,#map .road path.sel,#map .service path.sel,#map .stream path.sel{stroke:#b3261e}
#map .water path.sel,#map .woods path.sel,#map .green path.sel,#map .field path.sel,#map .parking path.sel{stroke:#b3261e;stroke-width:2}
#ascii{display:block;width:100%;cursor:crosshair}
aside{position:sticky;top:calc(env(safe-area-inset-top,0px) + 16px);display:grid;gap:14px;border-top:3px solid var(--ink);padding-top:12px;min-width:0}
.ro-k{font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:var(--mute)}
.ro-n{font-family:var(--display);font-size:28px;line-height:1.05;margin:0;text-wrap:balance}
.ro-n .no{font-family:var(--mono);font-size:14px;color:var(--hi);vertical-align:middle;margin-right:8px}
.ro p{margin:0;max-width:60ch}
.ro .chg{font-size:13px;color:var(--mute);border-left:2px solid var(--rule);padding-left:10px}
.id{font-family:var(--mono);font-size:12px;color:var(--mute);font-variant-numeric:tabular-nums}
.empty{color:var(--mute);font-size:14px}
.legend{display:grid;grid-template-columns:auto 1fr;gap:3px 10px;font-size:12px;color:var(--mute);font-family:var(--mono)}
.legend b{font-weight:600;color:var(--ink)}
footer{font-family:var(--mono);font-size:11px;color:var(--mute);border-top:1px solid var(--rule);padding-top:10px}
:focus-visible{outline:2px solid var(--hi);outline-offset:2px}
</style>
<div class="wrap">
<header><h1>Reed College · World</h1><span class="meta">STATE ONLY · NO TIME · NO AGENTS · NO RULES</span></header>
<p class="lede">The same campus drawn twice from one OpenStreetMap file. Every footprint, path and pond keeps its OSM id, so pointing at a building on the map lights up the same cells on the grid, and the reverse.</p>
<div class="grid2">
 <div class="plates">
  <figure><figcaption><span>Plate 1 · Vector map</span><span>UTM 10N · 1 unit = 1 m · north up</span></figcaption><div class="plate">__SVG__</div></figure>
  <figure><figcaption><span>Plate 2 · Character grid</span><span>1 cell = 8 m × 16 m · __COLS__ × __ROWS__</span></figcaption><div class="plate"><canvas id="ascii" aria-label="ASCII grid of the campus"></canvas></div></figure>
 </div>
 <aside aria-live="polite">
  <div class="ro" id="ro"><p class="empty">Point at anything on either plate. Click to pin it.</p></div>
  <div class="legend"><b>#</b><span>campus building</span><b>o</b><span>neighbourhood building</span><b>~</b><span>water, stream</span><b>.</b><span>footpath</span><b>= -</b><span>street, service road</span><b>:</b><span>parking</span><b>,</b><span>sports field</span><b>%</b><span>woods, scrub</span><b>"</b><span>lawn, park</span><b>12</b><span>2010 campus-map number</span></div>
 </aside>
</div>
<footer>© OpenStreetMap contributors (ODbL) · building uses from reed_college_buildings.csv</footer>
</div>
<script>
const D=__DATA__;
const ro=document.getElementById('ro'), svg=document.getElementById('map'), cv=document.getElementById('ascii'), cx=cv.getContext('2d');
let hover=null, pinned=null;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function show(id){
  const f=id&&D.info[id];
  if(!f){ro.innerHTML='<p class="empty">Point at anything on either plate. Click to pin it.</p>';return}
  ro.innerHTML=`<span class="ro-k">${esc(f.k)}${f.c?' · '+esc(f.c):''}</span>
   <h2 class="ro-n">${f.no&&f.no!=='—'?`<span class="no">${esc(f.no)}</span>`:''}${esc(f.n||'Unnamed')}</h2>
   ${f.u?`<p>${esc(f.u)}</p>`:''}${f.x?`<p class="chg">${esc(f.x)}</p>`:''}
   ${!f.n&&f.k.startsWith('Campus')?'<p class="chg">No name in OpenStreetMap. Left blank on purpose.</p>':''}
   <span class="id">${esc(id)}${f.lv?' · '+esc(f.lv)+' levels':''}</span>`;
}
function select(id){
  svg.querySelectorAll('.sel').forEach(e=>e.classList.remove('sel'));
  if(id){const e=document.getElementById(id.replace('/','-')); if(e) e.classList.add('sel')}
  show(id); drawGrid(id);
}
const cur=()=>hover||pinned;
svg.addEventListener('mouseover',e=>{const t=e.target.closest('[data-osm]');hover=t?t.dataset.osm:null;select(cur())});
svg.addEventListener('mouseleave',()=>{hover=null;select(cur())});
svg.addEventListener('click',e=>{const t=e.target.closest('[data-osm]');pinned=t?t.dataset.osm:null;select(cur())});
// grid
const R=D.rows.length, C=D.rows[0].length; let cw=0,ch=0;
function size(){const w=cv.parentElement.clientWidth, dpr=window.devicePixelRatio||1; cw=w/C; ch=cw*2; cv.width=w*dpr; cv.height=ch*R*dpr; cv.style.height=ch*R+'px'; cx.setTransform(dpr,0,0,dpr,0,0); drawGrid(cur())}
function drawGrid(id){
  if(!cw) return;
  const k=id?D.ids.indexOf(id):-2;
  cx.fillStyle='#f4f2ec'; cx.fillRect(0,0,cw*C,ch*R);
  cx.font=`${Math.max(cw*1.62,4)}px 'IBM Plex Mono',ui-monospace,Menlo,monospace`; cx.textBaseline='middle'; cx.textAlign='center';
  for(let r=0;r<R;r++){const row=D.rows[r], g=D.grid[r];
    for(let c=0;c<C;c++){const on=g[c]===k;
      if(on){cx.fillStyle='#b3261e';cx.fillRect(c*cw,r*ch,cw+.5,ch+.5)}
      const s=row[c]; if(s===' ') continue;
      cx.fillStyle=on?'#f4f2ec':(/[0-9#AT]/.test(s)?'#1b1a18':'#6d6a62'); cx.fillText(s,c*cw+cw/2,r*ch+ch/2)}}
}
function cellId(e){const b=cv.getBoundingClientRect(), c=Math.floor((e.clientX-b.left)/cw), r=Math.floor((e.clientY-b.top)/ch); const v=D.grid[r]&&D.grid[r][c]; return v>=0?D.ids[v]:null}
cv.addEventListener('mousemove',e=>{const id=cellId(e); if(id!==hover){hover=id;select(cur())}});
cv.addEventListener('mouseleave',()=>{hover=null;select(cur())});
cv.addEventListener('click',e=>{pinned=cellId(e);select(cur())});
new ResizeObserver(size).observe(cv.parentElement);
document.fonts&&document.fonts.ready.then(size);
// open on Eliot Hall so the linkage is visible at rest
const eliot=Object.keys(D.info).find(k=>D.info[k].n==='Eliot Hall'); pinned=eliot; size(); select(eliot);
</script>'''
page = page.replace('__SVG__', svg).replace('__DATA__', json.dumps(data, separators=(',', ':'))).replace('__COLS__', str(ids['cols'])).replace('__ROWS__', str(ids['rows']))
open(dst, 'w').write(page)
print(dst, len(page) // 1024, 'KB')
