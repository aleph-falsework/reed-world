"""Render reed_world.geojson as a monochrome, diagrammatic SVG (1 SVG unit = 1 metre).
Usage: python3 render_svg.py out/reed_world.geojson out/reed_world_lowfi.svg [--embed]
--embed omits the XML prolog and makes features hoverable (for the HTML version)."""
import json, sys, html
from shapely.geometry import shape

src, dst = sys.argv[1], sys.argv[2]
d = json.load(open(src))
W, H = d['meta']['size_m']
PAD_T, PAD_B = 70, 70
TH = H + PAD_T + PAD_B

def Y(y): return round(H - y + PAD_T, 2)  # north up: flip y

def ring(coords):
    return 'M' + 'L'.join(f'{x:.1f},{Y(y)}' for x, y in coords) + 'Z'

def path_d(g):
    t = g.geom_type
    if t == 'Polygon':
        return ring(g.exterior.coords) + ''.join(ring(i.coords) for i in g.interiors)
    if t == 'LineString':
        return 'M' + 'L'.join(f'{x:.1f},{Y(y)}' for x, y in g.coords)
    if t.startswith('Multi') or t == 'GeometryCollection':
        return ''.join(path_d(p) for p in g.geoms if p.geom_type != 'Point')
    return ''

def sid(fid): return fid.replace('/', '-')

LAYERS = [  # (kind filter, css class) in draw order
    ('golf', 'golf'), ('green', 'green'), ('woods', 'woods'), ('scrub', 'woods'), ('field', 'field'),
    ('parking', 'parking'), ('water', 'water'), ('stream', 'stream'),
    ('road', 'road'), ('service_road', 'service'), ('path', 'path'),
    ('campus_boundary', 'boundary'),
]
groups = {c: [] for _, c in LAYERS}
bld_n, bld_c, labels, marks = [], [], [], []
for f in d['features']:
    p = f['properties']; g = shape(f['geometry']); k = p['kind']
    attrs = f'id="{sid(p["id"])}" data-osm="{p["id"]}"'
    if k == 'building':
        el = f'<path {attrs} d="{path_d(g)}"/>'
        if p['realm'] == 'campus':
            meta = ' '.join(f'data-{a}="{html.escape(str(p[b]))}"' for a, b in (('name', 'name'), ('no', 'map_no'), ('cat', 'category'), ('use', 'use'), ('chg', 'changes')) if b in p)
            bld_c.append(f'<path {attrs} {meta} class="b{"" if "name" in p else " unnamed"}" d="{path_d(g)}"/>')
            if 'name' in p:
                c = g.representative_point()
                labels.append((c.x, c.y, p['name'], g.area))
        else:
            bld_n.append(el)
        continue
    if k in ('landmark_tree', 'venue'):
        if g.geom_type == 'Point':
            marks.append((k, g.x, g.y, p.get('name', ''), p['id'], p))
        continue
    for kk, cls in LAYERS:
        if kk == k:
            groups[cls].append(f'<path {attrs} d="{path_d(g)}"/>')

# collapse a few long names so labels fit
SHORT = {'Eric V. Hauser Memorial Library': 'Hauser Library', 'Reed College Sports Center': 'Sports Center',
         'Educational Technology Center': 'ETC', 'Health and Counseling Center': 'Health & Counseling',
         'Reed College Apartments': 'Reed Apts', 'Dorothy Johansen House': 'Johansen House',
         'Performing Arts Building': 'Performing Arts', 'Vollum College Center': 'Vollum',
         'Gray Campus Center': 'Gray Campus Ctr', 'The Horrid Art Project': 'Horrid Art Project',
         'Reed Research Reactor': 'Research Reactor'}
seen = set(); lab_svg = []
for x, y, n, a in sorted(labels, key=lambda t: -t[3]):
    n2 = SHORT.get(n, n)
    if n2 in seen: continue  # one label per name (e.g. the four Reed Apts blocks)
    seen.add(n2)
    lab_svg.append((x, Y(y), html.escape(n2)))

mark_svg = []
for k, x, y, n, fid, p in marks:
    if k == 'venue':
        mark_svg.append(f'<g id="{sid(fid)}" data-osm="{fid}" data-name="{html.escape(n)}" data-no="{p.get("map_no","")}" data-cat="{html.escape(p.get("category",""))}" data-use="{html.escape(p.get("use",""))}" class="venue"><circle cx="{x:.1f}" cy="{Y(y)}" r="4"/></g><text class="vlab" x="{x+6:.1f}" y="{Y(y)+2}">{html.escape(n)}</text>')
    else:
        mark_svg.append(f'<circle id="{sid(fid)}" data-osm="{fid}" data-name="{html.escape(n)}" class="tree" cx="{x:.1f}" cy="{Y(y)}" r="2.2"/>')

# named areas (canyon, lake, groves, parking) as italic area labels
area_lab = []
for f in d['features']:
    p = f['properties']
    if p['kind'] in ('water', 'woods', 'green', 'parking', 'field') and 'name' in p and p['name'] not in ('Eliot Circle',):
        g = shape(f['geometry']);
        if g.area < 600 or g.geom_type not in ('Polygon', 'MultiPolygon'): continue
        c = g.representative_point()
        area_lab.append(f'<text class="area" x="{c.x:.1f}" y="{Y(c.y)}">{html.escape(p["name"])}</text>')
    if p.get('name') == 'Eliot Circle' and not any('Eliot Circle' in a for a in area_lab):
        c = shape(f['geometry']).representative_point()
        area_lab.append(f'<text class="area" x="{c.x:.1f}" y="{Y(c.y)+18}">Eliot Circle</text>')

# scale bar (200 m) + north arrow
sx, sy = 20, TH - 34
scale = (f'<g class="scale"><rect x="{sx}" y="{sy}" width="100" height="5" class="sb1"/><rect x="{sx+100}" y="{sy}" width="100" height="5" class="sb0"/>'
         f'<text x="{sx}" y="{sy+18}">0</text><text x="{sx+100}" y="{sy+18}">100</text><text x="{sx+200}" y="{sy+18}">200 m</text></g>')
north = (f'<g class="north" transform="translate({W-30},{TH-48})"><path d="M0,-18 L8,10 L0,4 L-8,10 Z"/>'
         f'<text x="0" y="24">N</text></g>')
title = (f'<text class="title" x="20" y="34">REED COLLEGE</text>'
         f'<text class="sub" x="20" y="52">World layer · state only · UTM 10N, 1 unit = 1 m</text>')
credit = f'<text class="credit" x="{W-60}" y="{TH-10}">© OpenStreetMap contributors</text>'

css = """
svg{background:#f4f2ec}
.frame{fill:#f4f2ec}
.golf path,.green path{fill:#e6e3da;stroke:none}
.woods path{fill:url(#woods);stroke:#9b978c;stroke-width:.4}
.field path{fill:#f4f2ec;stroke:#6d6a62;stroke-width:.8}
.parking path{fill:url(#hatch);stroke:#9b978c;stroke-width:.5}
.water path{fill:#c9c6bd;stroke:#2a2926;stroke-width:.8}
.stream path{fill:none;stroke:#2a2926;stroke-width:1.6}
.road path{fill:none;stroke:#b9b5aa;stroke-width:9;stroke-linecap:round;stroke-linejoin:round}
.service path{fill:none;stroke:#cfccc3;stroke-width:4.5;stroke-linecap:round;stroke-linejoin:round}
.path path{fill:none;stroke:#55524b;stroke-width:.9;stroke-dasharray:none}
.boundary path{fill:none;stroke:#1b1a18;stroke-width:1.4;stroke-dasharray:8 4}
.nb path{fill:#dedbd2;stroke:#bdb9ae;stroke-width:.4}
.cb path{fill:#1b1a18;stroke:#1b1a18;stroke-width:.4}
.cb path.unnamed{fill:#6d6a62}
.labels text{font:600 6.4px 'Helvetica Neue',Arial,sans-serif;fill:#1b1a18;text-anchor:middle;dominant-baseline:middle}
.halo text{fill:#f4f2ec;stroke:#f4f2ec;stroke-width:2.4px;stroke-linejoin:round}
.area{font:italic 8px Georgia,serif;fill:#55524b;text-anchor:middle}
.tree{fill:#f4f2ec;stroke:#1b1a18;stroke-width:.8}
.venue circle{fill:#f4f2ec;stroke:#1b1a18;stroke-width:1.4}
.vlab{font:600 6.4px Helvetica,Arial,sans-serif;fill:#1b1a18}
.title{font:700 22px Georgia,serif;letter-spacing:6px;fill:#1b1a18}
.sub,.scale text,.credit{font:9px Helvetica,Arial,sans-serif;fill:#55524b}
.scale text{text-anchor:middle}.credit{text-anchor:end}
.sb1{fill:#1b1a18}.sb0{fill:#f4f2ec;stroke:#1b1a18;stroke-width:1}
.north path{fill:#1b1a18}.north text{font:700 10px Helvetica,Arial;text-anchor:middle;fill:#1b1a18}
"""
defs = ('<defs><pattern id="hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        '<line x1="0" y1="0" x2="0" y2="4" stroke="#cfccc3" stroke-width="1.2"/></pattern>'
        '<pattern id="woods" width="7" height="7" patternUnits="userSpaceOnUse"><rect width="7" height="7" fill="#e6e3da"/>'
        '<circle cx="2" cy="2" r=".9" fill="#7d796f"/><circle cx="5.5" cy="5.5" r=".9" fill="#7d796f"/></pattern></defs>')

g = lambda cls, items: f'<g class="{cls}">' + ''.join(items) + '</g>'
body = (defs + f'<style>{css}</style>' + f'<rect class="frame" width="{W:.0f}" height="{TH:.0f}"/>' + title
        + ''.join(g(c, groups[c]) for c in dict.fromkeys(c for _, c in LAYERS) if c != 'boundary')
        + g('nb', bld_n) + g('boundary', groups['boundary']) + g('cb', bld_c)
        + g('marks', mark_svg) + g('areas', area_lab) + '<g class="labels halo">' + ''.join(f'<text x="{x:.1f}" y="{y}">{t}</text>' for x,y,t in lab_svg) + '</g>'
        + '<g class="labels">' + ''.join(f'<text x="{x:.1f}" y="{y}">{t}</text>' for x,y,t in lab_svg) + '</g>' + scale + north + credit)
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {TH:.0f}" width="{W*1.5:.0f}" height="{TH*1.5:.0f}">' + body + '</svg>')
if '--embed' not in sys.argv:
    svg = '<?xml version="1.0" encoding="UTF-8"?>\n' + svg
open(dst, 'w').write(svg)
print('wrote', dst, len(svg)//1024, 'KB')
