"""Three.js top-down wireframe walk of the world layer.
Usage: python3 gen_walk.py out/reed_world.geojson out/reed_walk.html"""
import json, sys
from shapely.geometry import shape

src, dst = sys.argv[1], sys.argv[2]
w = json.load(open(src))
r1 = lambda cs: [v for x, y in cs for v in (round(x, 1), round(y, 1))]

def rings(g):
    polys = [g] if g.geom_type == 'Polygon' else [p for p in getattr(g, 'geoms', []) if p.geom_type == 'Polygon']
    return [r1(list(p.exterior.coords)[:-1]) for p in polys]

def lines(g):
    if g.geom_type == 'LineString': return [r1(g.coords)]
    if g.geom_type in ('MultiLineString', 'GeometryCollection'): return [r1(p.coords) for p in g.geoms if p.geom_type == 'LineString']
    if g.geom_type in ('Polygon', 'MultiPolygon'): return [r1(list(p.exterior.coords)) for p in ([g] if g.geom_type == 'Polygon' else g.geoms)]
    return []

B, L = [], {'path': [], 'road': [], 'service_road': [], 'water': [], 'stream': [], 'campus_boundary': [], 'field': [], 'woods': []}
for f in w['features']:
    p = f['properties']; g = shape(f['geometry']); k = p['kind']
    if k == 'building':
        lv = p.get('building:levels'); h = p.get('height')
        try: hm, est = (float(str(h).split()[0]), 0) if h else ((float(lv) * 3.5, 0) if lv else ((7.0 if p['realm'] == 'campus' else 6.0), 1))
        except ValueError: hm, est = 7.0, 1
        for rg in rings(g):
            B.append({'i': p['id'], 'n': p.get('name', ''), 'c': 1 if p['realm'] == 'campus' else 0, 'h': hm, 'e': est,
                      'no': p.get('map_no', ''), 'u': p.get('use', ''), 'r': rg})
    elif k in L:
        L[k] += lines(g)
    elif k == 'scrub':
        L['woods'] += lines(g)
W, H = w['meta']['size_m']
start = next(shape(f['geometry']).centroid for f in w['features'] if f['properties'].get('name') == 'Eliot Circle')
data = {'W': W, 'H': H, 'B': B, 'L': L, 'start': [round(start.x, 1), round(start.y - 25, 1)]}

page = r'''<title>Reed Campus Walk</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>
/* One dark plate: a vector-monitor survey of the campus. Ink is light, the walker is the only colour. */
:root{--bg:#0d0f0e;--ink:#e8e6df;--dim:#7b7d76;--faint:#3a3d39;--walker:#ff5a3c;--mono:ui-monospace,'SF Mono',Menlo,Consolas,monospace;color-scheme:dark}
html,body{height:100%}
body{background:var(--bg);color:var(--ink);font-family:var(--mono);overflow:hidden}
#stage{position:fixed;inset:0;touch-action:none}
.hud{position:fixed;pointer-events:none;font-size:12px;line-height:1.5;letter-spacing:.02em;text-shadow:0 0 3px var(--bg),0 0 6px var(--bg),0 0 10px var(--bg)}
.tl{top:calc(env(safe-area-inset-top,0px) + 14px);left:max(16px,env(safe-area-inset-left,0px));right:max(16px,env(safe-area-inset-right,0px))}
.bl{bottom:calc(env(safe-area-inset-bottom,0px) + 14px);left:max(16px,env(safe-area-inset-left,0px))}
.br{bottom:calc(env(safe-area-inset-bottom,0px) + 14px);right:max(16px,env(safe-area-inset-right,0px));text-align:right;color:var(--dim)}
.k{color:var(--dim);text-transform:uppercase;letter-spacing:.14em;font-size:10px}
#place{font-size:20px;line-height:1.2;max-width:34ch;margin-top:2px}
#place .no{color:var(--walker);margin-right:8px}
#use{color:var(--dim);max-width:52ch;margin-top:4px}
#scale{display:flex;align-items:center;gap:8px}
#bar{height:4px;border:1px solid var(--ink);border-top:0}
kbd{font-family:var(--mono);border:1px solid var(--faint);padding:0 4px;color:var(--ink)}
.ctl{position:fixed;right:max(16px,env(safe-area-inset-right,0px));bottom:calc(env(safe-area-inset-bottom,0px) + 62px);display:grid;gap:8px;background:rgba(13,15,14,.82);border:1px solid var(--faint);padding:10px 12px;font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.12em}
.row{display:grid;grid-template-columns:44px 28px 120px 28px;align-items:center;gap:6px}
.ctl button{font:inherit;font-size:15px;letter-spacing:0;color:var(--ink);background:transparent;border:1px solid var(--faint);height:28px;cursor:pointer}
.ctl button:hover{border-color:var(--ink)}.ctl button:focus-visible,.ctl input:focus-visible{outline:1px solid var(--walker);outline-offset:2px}
.ctl input[type=range]{width:120px;accent-color:var(--walker)}
.ctl .presets{display:flex;gap:6px}.ctl .presets button{flex:1;font-size:10px;letter-spacing:.1em;text-transform:uppercase}
@media (max-width:600px){#use{display:none}#place{font-size:16px}.keys{display:none}.ctl{bottom:calc(env(safe-area-inset-bottom,0px) + 32px);left:max(16px,env(safe-area-inset-left,0px));justify-content:center}.bl{bottom:calc(env(safe-area-inset-bottom,0px) + 166px)}.br{font-size:10px}}
</style>
<canvas id="stage" tabindex="0" aria-label="Top-down wireframe of Reed College. Use arrow keys or WASD to walk."></canvas>
<div class="hud tl"><div class="k" id="where">Reed College · world layer</div><div id="place">—</div><div id="use"></div></div>
<div class="hud bl"><div id="scale"><div id="bar"></div><span id="barl">50 m</span></div><div class="k" id="pos" style="margin-top:6px"></div></div>
<div class="hud br"><div class="keys"><kbd>W A S D</kbd> walk · <kbd>Shift</kbd> run · <kbd>Q</kbd>/<kbd>E</kbd>, wheel or pinch zoom · <kbd>T</kbd> tilt</div><div><span class="keys">drag to walk on touch · </span>© OpenStreetMap contributors</div></div>
<div class="ctl" role="group" aria-label="View">
 <div class="row"><span>Zoom</span><button id="zout" aria-label="Zoom out">−</button><input id="zoom" type="range" min="0" max="1000" value="0" aria-label="Zoom"><button id="zin" aria-label="Zoom in">+</button></div>
 <div class="row"><span>Tilt</span><span></span><input id="tilt" type="range" min="0" max="60" value="0" aria-label="Tilt in degrees"><span id="tiltv">0°</span></div>
 <div class="presets"><button id="pStreet">Street</button><button id="pQuad">Quad</button><button id="pCampus">Campus</button></div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>
const D=__DATA__;
const cv=document.getElementById('stage');
const R=new THREE.WebGLRenderer({canvas:cv,antialias:true});R.setPixelRatio(Math.min(devicePixelRatio,2));R.setClearColor(0x0d0f0e);
const S=new THREE.Scene();
const cam=new THREE.PerspectiveCamera(40,1,1,5000);cam.up.set(0,0,-1);
// world x = east, world y = north -> three (x, height, -y)
const segs=(arr,col,op)=>{const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(arr,3));
  const m=new THREE.LineBasicMaterial({color:col,transparent:op<1,opacity:op});const l=new THREE.LineSegments(g,m);S.add(l);return l};
function polyline(list,y){const a=[];for(const c of list)for(let i=0;i<c.length-2;i+=2)a.push(c[i],y,-c[i+1],c[i+2],y,-c[i+3]);return a}
function boxes(filter){const a=[];for(const b of D.B){if(!filter(b))continue;const r=b.r,n=r.length/2,h=b.h;
  for(let i=0;i<n;i++){const j=(i+1)%n,x0=r[2*i],z0=-r[2*i+1],x1=r[2*j],z1=-r[2*j+1];
    a.push(x0,0,z0,x1,0,z1, x0,h,z0,x1,h,z1, x0,0,z0,x0,h,z0)}}return a}
segs(polyline(D.L.road,0),0x4a4d48,1);
segs(polyline(D.L.service_road,0),0x3a3d39,1);
segs(polyline(D.L.field,0),0x3a3d39,1);
segs(polyline(D.L.woods,0),0x2c3a30,1);
segs(polyline(D.L.path,.05),0x8a8c84,1);
segs(polyline(D.L.water,0),0x6f8fa3,1);
segs(polyline(D.L.stream,0),0x6f8fa3,1);
segs(polyline(D.L.campus_boundary,.1),0xe8e6df,.35);
segs(boxes(b=>!b.c),0x454843,1);
segs(boxes(b=>b.c&&!b.e),0xe8e6df,1);
segs(boxes(b=>b.c&&b.e),0xa9a79f,1);
// walker
const me=new THREE.Group();
const ring=new THREE.RingGeometry(1.4,2,32);ring.rotateX(-Math.PI/2);
me.add(new THREE.Mesh(ring,new THREE.MeshBasicMaterial({color:0xff5a3c})));
const tick=new THREE.BufferGeometry();tick.setAttribute('position',new THREE.Float32BufferAttribute([0,.2,0,0,.2,-5],3));
me.add(new THREE.Line(tick,new THREE.LineBasicMaterial({color:0xff5a3c})));
S.add(me);
let px=D.start[0],py=D.start[1],hd=0,camH=650,camHt=650,tilt=0,tiltT=0;const ZMIN=30,ZMAX=1400;
// footprint lookup
const BB=D.B.filter(b=>b.n).map(b=>{let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;for(let i=0;i<b.r.length;i+=2){x0=Math.min(x0,b.r[i]);x1=Math.max(x1,b.r[i]);y0=Math.min(y0,b.r[i+1]);y1=Math.max(y1,b.r[i+1])}return {b,x0,y0,x1,y1}});
function inside(r,x,y){let c=false;for(let i=0,n=r.length/2,j=n-1;i<n;j=i++){const xi=r[2*i],yi=r[2*i+1],xj=r[2*j],yj=r[2*j+1];if((yi>y)!==(yj>y)&&x<(xj-xi)*(y-yi)/(yj-yi)+xi)c=!c}return c}
function near(x,y){let best=null,bd=40;for(const o of BB){const dx=Math.max(o.x0-x,0,x-o.x1),dy=Math.max(o.y0-y,0,y-o.y1),d=Math.hypot(dx,dy);
  if(d===0&&inside(o.b.r,x,y))return {b:o.b,d:0};if(d<bd){bd=d;best=o.b}}return best?{b:best,d:bd}:null}
const el=id=>document.getElementById(id);let lastId=null;
function hud(){const n=near(px,py);const id=n?n.b.i+(n.d===0):null;
  if(id!==lastId){lastId=id;
    if(!n){el('where').textContent='Reed College · world layer';el('place').textContent='Open ground';el('use').textContent=''}
    else{el('where').textContent=(n.d===0?'Inside':'Near')+' · '+n.b.i+(n.b.e?' · height estimated':' · '+n.b.h.toFixed(0)+' m');
      el('place').innerHTML=(n.b.no&&n.b.no!=='—'?'<span class="no">'+n.b.no+'</span>':'')+n.b.n.replace(/</g,'&lt;');el('use').textContent=n.b.u}}
  el('pos').textContent=`x ${px.toFixed(0)} m · y ${py.toFixed(0)} m · heading ${((hd*180/Math.PI+360)%360).toFixed(0)}°`;
  const ppm=innerHeight/(2*camH*Math.tan(THREE.MathUtils.degToRad(20)));
  const m=[10,20,50,100,200].find(v=>v*ppm>70)||200;el('bar').style.width=(m*ppm)+'px';el('barl').textContent=m+' m'}
// input
const keys={};addEventListener('keydown',e=>{keys[e.key.toLowerCase()]=1;if(e.key.startsWith('Arrow'))e.preventDefault()});addEventListener('keyup',e=>{keys[e.key.toLowerCase()]=0});
const zs=el('zoom'),ts=el('tilt');
const z2s=h=>Math.round(1000*Math.log(h/ZMIN)/Math.log(ZMAX/ZMIN)), s2z=v=>ZMIN*Math.pow(ZMAX/ZMIN,v/1000);
function setZoom(h){camHt=Math.min(ZMAX,Math.max(ZMIN,h));zs.value=1000-z2s(camHt)}
function setTilt(d){tiltT=Math.min(60,Math.max(0,d));ts.value=tiltT;el('tiltv').textContent=Math.round(tiltT)+'°'}
setZoom(camHt);
zs.addEventListener('input',()=>{camHt=s2z(1000-zs.value)});
ts.addEventListener('input',()=>setTilt(+ts.value));
el('zin').onclick=()=>setZoom(camHt/1.5);el('zout').onclick=()=>setZoom(camHt*1.5);
el('pStreet').onclick=()=>{setZoom(60);setTilt(50)};el('pQuad').onclick=()=>{setZoom(260);setTilt(25)};el('pCampus').onclick=()=>{setZoom(1100);setTilt(0)};
addEventListener('wheel',e=>{setZoom(camHt*Math.exp(e.deltaY*.001))},{passive:true});
addEventListener('keydown',e=>{if(e.key==='-'||e.key==='_')setZoom(camHt*1.25);if(e.key==='='||e.key==='+')setZoom(camHt/1.25);if(e.key.toLowerCase()==='t')setTilt(tiltT>0?0:45)});
const pts=new Map();let pinch0=null;
let drag=null;cv.addEventListener('pointerdown',e=>{pts.set(e.pointerId,[e.clientX,e.clientY]);cv.setPointerCapture(e.pointerId);
  if(pts.size===2){drag=null;const [a,b]=[...pts.values()];pinch0={d:Math.hypot(a[0]-b[0],a[1]-b[1]),h:camHt}}else{drag={x:e.clientX,y:e.clientY,dx:0,dy:0}}});
cv.addEventListener('pointermove',e=>{if(!pts.has(e.pointerId))return;pts.set(e.pointerId,[e.clientX,e.clientY]);
  if(pinch0&&pts.size===2){const [a,b]=[...pts.values()];setZoom(pinch0.h*pinch0.d/Math.max(10,Math.hypot(a[0]-b[0],a[1]-b[1])))}});
cv.addEventListener('pointermove',e=>{if(drag){drag.dx=e.clientX-drag.x;drag.dy=e.clientY-drag.y}});
['pointerup','pointercancel'].forEach(t=>cv.addEventListener(t,e=>{pts.delete(e.pointerId);if(pts.size<2)pinch0=null;drag=null}));
function size(){R.setSize(innerWidth,innerHeight,false);cam.aspect=innerWidth/innerHeight;cam.updateProjectionMatrix();lastId=null}
addEventListener('resize',size);size();cv.focus();
let t0=performance.now();
function frame(t){const dt=Math.min(.05,(t-t0)/1000);t0=t;
  let vx=(keys.d||keys.arrowright?1:0)-(keys.a||keys.arrowleft?1:0),vy=(keys.w||keys.arrowup?1:0)-(keys.s||keys.arrowdown?1:0);
  if(drag&&Math.hypot(drag.dx,drag.dy)>8){vx=drag.dx;vy=-drag.dy}
  const m=Math.hypot(vx,vy);
  if(m){const sp=(keys.shift?6:1.4)*(drag?Math.min(1,m/120)*4:1)*(Math.min(camH,400)/60);  // walk 1.4 m/s, scaled so it stays usable when zoomed out
    px=Math.min(D.W,Math.max(0,px+vx/m*sp*dt));py=Math.min(D.H,Math.max(0,py+vy/m*sp*dt));hd=Math.atan2(vx,vy)}
  if(keys.q)setZoom(camHt*.98);if(keys.e)setZoom(camHt*1.02);
  camH+=(camHt-camH)*.12;tilt+=(tiltT-tilt)*.12;
  me.position.set(px,0,-py);me.rotation.y=-hd;
  const tr=THREE.MathUtils.degToRad(tilt);cam.position.set(px,camH*Math.cos(tr),-py+camH*Math.sin(tr)+.001);cam.lookAt(px,0,-py);
  R.render(S,cam);hud();requestAnimationFrame(frame)}
requestAnimationFrame(frame);
</script>'''
open(dst, 'w').write(page.replace('__DATA__', json.dumps(data, separators=(',', ':'))))
print(dst, len(B), 'footprints', sum(len(v) for v in L.values()), 'lines')
