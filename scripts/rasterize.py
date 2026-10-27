"""Rasterize the world layer into (a) an ASCII grid with an id grid, (b) a Tiled JSON map.
Same feature ids (OSM ids) as reed_world.geojson.
Usage: python3 rasterize.py out/reed_world.geojson out/"""
import json, sys
import numpy as np
from shapely.geometry import shape, box
from shapely import STRtree
from PIL import Image, ImageDraw

src, out = sys.argv[1], sys.argv[2]
d = json.load(open(src))
W, H = d['meta']['size_m']
feats = d['features']

# raster classes, highest priority first: (class, test, ascii char)
CLASSES = [
    ('campus_building', 'area', '#'), ('neighborhood_building', 'area', 'o'),
    ('water', 'area', '~'), ('stream', 'line', '~'), ('path', 'line', '.'),
    ('road', 'line', '='), ('service_road', 'line', '-'), ('parking', 'area', ':'),
    ('field', 'area', ','), ('woods', 'area', '%'), ('green', 'area', '"'),
    ('golf', 'area', '"'), ('campus_ground', 'area', ' '), ('outside', None, ' '),
]
CI = {c[0]: i for i, c in enumerate(CLASSES)}

def cls_of(p):
    k = p['kind']
    if k == 'building': return p['realm'] + '_building'
    if k == 'scrub': return 'woods'
    if k == 'campus_boundary': return 'campus_ground'
    return k if k in CI else None

geoms, gcls, gid = [], [], []
for f in feats:
    c = cls_of(f['properties'])
    if c is None: continue
    g = shape(f['geometry'])
    if g.geom_type == 'Point': continue
    geoms.append(g); gcls.append(CI[c]); gid.append(f['properties']['id'])
tree = STRtree(geoms)
is_line = np.array([CLASSES[c][1] == 'line' for c in gcls])
gcls = np.array(gcls)

def rasterize(cw, ch):
    nx, ny = int(np.ceil(W / cw)), int(np.ceil(H / ch))
    cls = np.full((ny, nx), CI['outside']); idg = np.full((ny, nx), -1)
    boxes, cents, rc = [], [], []
    for r in range(ny):           # row 0 = north
        y0 = H - (r + 1) * ch
        for c in range(nx):
            x0 = c * cw
            boxes.append(box(x0, y0, x0 + cw, y0 + ch)); rc.append((r, c))
    from shapely.geometry import Point
    cents = [b.centroid for b in boxes]
    # lines: any touch of the cell; areas: cell centre inside
    li, lg = tree.query(boxes, predicate='intersects')
    ai, ag = tree.query(cents, predicate='within')
    for ci, gi, need_line in ((li, lg, True), (ai, ag, False)):
        m = is_line[gi] == need_line
        for cell, g in zip(ci[m], gi[m]):
            r, c = rc[cell]
            if gcls[g] < cls[r, c]:
                cls[r, c] = gcls[g]; idg[r, c] = g
    return cls, idg

# ---------------- ASCII: 8 m x 16 m cells (square-looking in a monospace font) ----------------
CW, CH = 8, 16
cls, idg = rasterize(CW, CH)
ny, nx = cls.shape
grid = [[CLASSES[cls[r, c]][2] for c in range(nx)] for r in range(ny)]
# stamp 2010 map numbers onto campus buildings at their representative point
key = []
for f in feats:
    p = f['properties']
    if p['kind'] == 'building' and 'map_no' in p and p['map_no'] not in [k[0] for k in key]:
        pt = shape(f['geometry']).representative_point()
        c, r = int(pt.x // CW), int((H - pt.y) // CH)
        s = p['map_no'] if p['map_no'] != '—' else ('T' if p['name'] == 'Trillium' else 'A')
        for i, ch in enumerate(s):
            if 0 <= c + i < nx: grid[r][c + i] = ch
        key.append((p['map_no'], s, p.get('name', ''), p['id']))
legend = ('#  campus building   o  neighbourhood building   ~  water / stream   .  footpath\n'
          '=  street            -  service road             :  parking          ,  sports field\n'
          '%  woods / scrub     "  lawn / park / garden     (digits = 2010 campus-map number; T = Trillium, A = Performing Arts)')
hdr = (f'REED COLLEGE — WORLD LAYER (state only) — ASCII grid {nx} x {ny}\n'
       f'one character = {CW} m east-west x {CH} m north-south (so shapes read true in a monospace font); north is up\n'
       f'{legend}\n' + '-' * nx)
foot = ('-' * nx + f'\nScale: {"|" + "-"*(100//CW - 1) + "|"} 100 m\n© OpenStreetMap contributors\n\nKEY\n' +
        '\n'.join(f'{s:>3}  {n:<34} {i}' for _, s, n, i in sorted(key, key=lambda k: (k[1].isdigit() is False, int(k[1]) if k[1].isdigit() else 0))))
open(f'{out}/reed_world_ascii.txt', 'w').write(hdr + '\n' + '\n'.join(''.join(row) for row in grid) + '\n' + foot + '\n')
ids = sorted(set(gid))
idx = {v: i for i, v in enumerate(ids)}
json.dump({'cell_m': [CW, CH], 'cols': nx, 'rows': ny, 'row0': 'north', 'ids': ids,
           'grid': [[idx[gid[g]] if g >= 0 else -1 for g in row] for row in idg.tolist()],
           'credit': '© OpenStreetMap contributors'}, open(f'{out}/reed_world_ascii_ids.json', 'w'))
print('ascii', nx, ny)

# ---------------- Tiled JSON: 16 px tiles, 4 m per tile ----------------
TM, TP = 4, 16
cls, idg = rasterize(TM, TM)
ny, nx = cls.shape
# tileset: one flat monochrome tile per class
INK, PAPER = (27, 26, 24), (244, 242, 236)
TILES = {
    'campus_building': lambda d: d.rectangle([0, 0, 15, 15], fill=INK),
    'neighborhood_building': lambda d: d.rectangle([0, 0, 15, 15], fill=(222, 219, 210)),
    'water': lambda d: (d.rectangle([0, 0, 15, 15], fill=(201, 198, 189)), [d.line([(2, y), (6, y - 2), (10, y), (14, y - 2)], fill=(110, 107, 99)) for y in (5, 12)]),
    'stream': lambda d: (d.rectangle([0, 0, 15, 15], fill=(201, 198, 189)), d.line([(0, 8), (15, 8)], fill=INK, width=2)),
    'path': lambda d: (d.rectangle([0, 0, 15, 15], fill=PAPER), d.rectangle([5, 5, 10, 10], fill=(85, 82, 75))),
    'road': lambda d: d.rectangle([0, 0, 15, 15], fill=(185, 181, 170)),
    'service_road': lambda d: d.rectangle([0, 0, 15, 15], fill=(207, 204, 195)),
    'parking': lambda d: (d.rectangle([0, 0, 15, 15], fill=PAPER), [d.line([(i, 15), (i + 15, 0)], fill=(190, 186, 176)) for i in range(-15, 16, 5)]),
    'field': lambda d: (d.rectangle([0, 0, 15, 15], fill=PAPER), d.rectangle([0, 0, 15, 15], outline=(160, 157, 148))),
    'woods': lambda d: (d.rectangle([0, 0, 15, 15], fill=(230, 227, 218)), [d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(125, 121, 111)) for x, y in ((4, 4), (12, 11))]),
    'green': lambda d: d.rectangle([0, 0, 15, 15], fill=(230, 227, 218)),
    'golf': lambda d: d.rectangle([0, 0, 15, 15], fill=(230, 227, 218)),
    'campus_ground': lambda d: d.rectangle([0, 0, 15, 15], fill=PAPER),
    'outside': lambda d: d.rectangle([0, 0, 15, 15], fill=(236, 233, 225)),
}
names = [c[0] for c in CLASSES]
ts = Image.new('RGB', (TP * len(names), TP), PAPER)
for i, n in enumerate(names):
    t = Image.new('RGB', (TP, TP), PAPER); TILES[n](ImageDraw.Draw(t)); ts.paste(t, (i * TP, 0))
ts.save(f'{out}/reed_tileset.png')
ground = [int(cls[r, c]) + 1 for r in range(ny) for c in range(nx)]
PPM = TP / TM  # pixels per metre
objs, oid = [], 1
def props(p):
    keep = [('osm_id', p['id']), ('kind', p['kind'])] + [(k, p[k]) for k in ('name', 'realm', 'map_no', 'category', 'use', 'changes', 'building:levels', 'height') if k in p]
    return [{'name': k, 'type': 'string', 'value': str(v)} for k, v in keep]
for f in feats:
    p = f['properties']; g = shape(f['geometry'])
    if p['kind'] == 'building':
        polys = [g] if g.geom_type == 'Polygon' else [q for q in getattr(g, 'geoms', []) if q.geom_type == 'Polygon']
        for q in polys:
            pts = [(x * PPM, (H - y) * PPM) for x, y in list(q.exterior.coords)[:-1]]
            ox, oy = pts[0]
            objs.append({'id': oid, 'name': p.get('name', ''), 'type': p['realm'] + '_building', 'x': round(ox, 2), 'y': round(oy, 2),
                         'rotation': 0, 'visible': True, 'width': 0, 'height': 0,
                         'polygon': [{'x': round(x - ox, 2), 'y': round(y - oy, 2)} for x, y in pts], 'properties': props(p)}); oid += 1
    elif g.geom_type == 'Point' and p['kind'] in ('landmark_tree', 'venue'):
        objs.append({'id': oid, 'name': p.get('name', ''), 'type': p['kind'], 'x': round(g.x * PPM, 2), 'y': round((H - g.y) * PPM, 2),
                     'point': True, 'rotation': 0, 'visible': True, 'width': 0, 'height': 0, 'properties': props(p)}); oid += 1
tmj = {
    'type': 'map', 'version': '1.10', 'tiledversion': '1.10.2', 'orientation': 'orthogonal', 'renderorder': 'right-down',
    'width': nx, 'height': ny, 'tilewidth': TP, 'tileheight': TP, 'infinite': False, 'nextlayerid': 3, 'nextobjectid': oid,
    'properties': [{'name': 'metres_per_tile', 'type': 'float', 'value': TM}, {'name': 'crs', 'type': 'string', 'value': d['meta']['crs']},
                   {'name': 'credit', 'type': 'string', 'value': '© OpenStreetMap contributors'}],
    'tilesets': [{'firstgid': 1, 'name': 'reed_world', 'image': 'reed_tileset.png', 'imagewidth': ts.width, 'imageheight': TP,
                  'tilewidth': TP, 'tileheight': TP, 'tilecount': len(names), 'columns': len(names), 'margin': 0, 'spacing': 0,
                  'tiles': [{'id': i, 'type': n, 'properties': [{'name': 'class', 'type': 'string', 'value': n}]} for i, n in enumerate(names)]}],
    'layers': [
        {'id': 1, 'name': 'ground', 'type': 'tilelayer', 'width': nx, 'height': ny, 'x': 0, 'y': 0, 'opacity': 1, 'visible': True, 'data': ground},
        {'id': 2, 'name': 'features', 'type': 'objectgroup', 'draworder': 'topdown', 'x': 0, 'y': 0, 'opacity': 1, 'visible': True, 'objects': objs},
    ],
}
json.dump(tmj, open(f'{out}/reed_world_tiled.tmj', 'w'))
# preview of the tile layer
prev = Image.new('RGB', (nx * TP // 4, ny * TP // 4))
small = [ts.crop((i * TP, 0, (i + 1) * TP, TP)).resize((4, 4)) for i in range(len(names))]
for r in range(ny):
    for c in range(nx):
        prev.paste(small[cls[r, c]], (c * 4, r * 4))
prev.save(f'{out}/../tiled_preview.png')
print('tiled', nx, ny, 'objects', oid - 1)
