#!/usr/bin/env python3
"""Pack world/3d/ (from export_3d.py) into compact files for the three.js page in world/3d/web/.

Usage:  python3 scripts/export_3d.py && python3 scripts/build_web3d.py
Writes world/3d/web/: terrain.bin, albedo.jpg, walk.bin, trees.bin, world.json (index.html is hand-written).
"""
import json, math, os
import numpy as np
from PIL import Image
from scipy import ndimage
from shapely.geometry import shape, Polygon, LineString, Point
from shapely.ops import unary_union
import rasterio
from rasterio import features as rfeatures
from affine import Affine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)
SRC, OUT = P('world', '3d'), P('world', '3d', 'web')
H_PX, W_PX = 811, 1274
STEEP_DEG = 32.0
NODATA, BUILDING, WATER, ROAD, PATH, TREE, STEEP, PAVED, LAWN = range(9)
r1 = lambda v: round(float(v), 1)


def flat(ring):
    return [r1(c) for xy in ring for c in xy]


def burn(geoms, all_touched=False):
    T = Affine(1, 0, 0, 0, -1, H_PX)
    if not geoms:
        return np.zeros((H_PX, W_PX), bool)
    return rfeatures.rasterize(((g, 1) for g in geoms), out_shape=(H_PX, W_PX), transform=T, fill=0,
                               dtype='uint8', all_touched=all_touched).astype(bool)


def main():
    os.makedirs(OUT, exist_ok=True)
    hmj = json.load(open(os.path.join(SRC, 'heightmap.json')))
    q = np.array(Image.open(os.path.join(SRC, 'heightmap_16.png'))).astype(np.float64)
    zmin, zmax = hmj['min_m'], hmj['max_m']
    hm = zmin + q / 65535 * (zmax - zmin)                       # (406, 637), row 0 north, vertex-registered 2 m
    water = json.load(open(os.path.join(SRC, 'water.json')))
    bj = json.load(open(os.path.join(SRC, 'buildings.json')))
    sj = json.load(open(os.path.join(SRC, 'signs.json')))
    tj = json.load(open(os.path.join(SRC, 'trees.json')))
    g = json.load(open(P('world', 'lowfi', 'reed_world.geojson')))
    with rasterio.open(P('world', 'lidar', 'surface_1m.tif')) as r:
        surf = r.read(1)

    # ---- carve lake beds and stream channels into the web terrain (display only; the export stays bare earth)
    rows, cols = hm.shape
    hm0 = hm.copy()                                              # uncarved, for bridge ends
    def h_at(x, y):
        return float(ndimage.map_coordinates(hm0, [[(810 - y) / 2], [x / 2]], order=1, mode='nearest')[0])
    bridges = []
    for f in g['features']:
        p = f['properties']
        if not p.get('bridge') or f['geometry']['type'] != 'LineString':
            continue
        cs = f['geometry']['coordinates']
        ln = LineString(cs)
        if ln.length < 2:
            continue
        za, zb = h_at(*cs[0]), h_at(*cs[-1])
        # deck: straight between the abutments, at least 0.8 m over any water it crosses
        zs = []
        for x, y in cs:
            t = ln.project(Point(x, y)) / ln.length
            zs.append(za + (zb - za) * t + 0.35)
        for lk in water['lakes']:
            if Polygon(lk['ring']).buffer(2).intersects(ln):
                zs = [max(z, lk['z'] + 0.8) for z in zs]
        bridges.append(dict(i=p['id'], n=p.get('name'), w=2.6 if p.get('highway') != 'footway' else 2.2,
                            l=[r1(v) for (x, y), z in zip(cs, zs) for v in (x, y, z)]))
    YY, XX = np.meshgrid(810 - 2 * np.arange(rows), 2 * np.arange(cols), indexing='ij')
    for lk in water['lakes']:
        poly = Polygon(lk['ring'], lk['holes']).buffer(0)
        minx, miny, maxx, maxy = poly.bounds
        sel = (XX >= minx - 4) & (XX <= maxx + 4) & (YY >= miny - 4) & (YY <= maxy + 4)
        for j, i in zip(*np.nonzero(sel)):
            pt = Point(XX[j, i], YY[j, i])
            if poly.contains(pt):
                d = poly.exterior.distance(pt)
                hm[j, i] = min(hm[j, i], lk['z'] - 0.4 - 1.6 * min(d / 8.0, 1.0))
            elif poly.distance(pt) < 2.5:
                hm[j, i] = max(min(hm[j, i], lk['z'] + 0.25), lk['z'] - 0.2)
    for st in water['streams']:
        ln = LineString([(a, b) for a, b, _ in st['line']])
        zs = np.array([c for _, _, c in st['line']])
        minx, miny, maxx, maxy = ln.bounds
        sel = (XX >= minx - 4) & (XX <= maxx + 4) & (YY >= miny - 4) & (YY <= maxy + 4)
        for j, i in zip(*np.nonzero(sel)):
            pt = Point(XX[j, i], YY[j, i])
            d = ln.distance(pt)
            if d < 3.0:
                zl = float(np.interp(ln.project(pt), np.linspace(0, ln.length, len(zs)), zs))
                hm[j, i] = min(hm[j, i], zl - 0.35 + 0.12 * d)
    hq = np.round((hm - zmin) / (zmax - zmin) * 65535).clip(0, 65535).astype('<u2')
    hq.tofile(os.path.join(OUT, 'terrain.bin'))

    # ---- ground texture
    Image.open(os.path.join(SRC, 'surface_albedo.png')).convert('RGB').save(
        os.path.join(OUT, 'albedo.jpg'), quality=90, subsampling=0, optimize=True)

    # ---- walk mask: 1 m, 1 = blocked (water, slopes > STEEP_DEG off paths). Buildings are exact polygons in JS.
    be = ndimage.zoom(hm, 2, order=1)[:H_PX, :W_PX]               # ~1 m from the carved 2 m terrain
    gy, gx = np.gradient(ndimage.gaussian_filter(be, 1.0))
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    lakes = burn([Polygon(lk['ring'], lk['holes']) for lk in water['lakes']])
    streams = burn([LineString([(a, b) for a, b, _ in st['line']]).buffer(1.0) for st in water['streams']])
    bridge_r = burn([shape(f['geometry']).buffer(1.8) for f in g['features'] if f['properties'].get('bridge')])
    paths = np.isin(surf, [ROAD, PATH])
    paths = ndimage.binary_dilation(paths, iterations=1)
    blocked = (lakes | streams | ((slope > STEEP_DEG) & ~paths)) & ~bridge_r
    bits = np.packbits(blocked.astype(np.uint8), axis=None)      # row-major, row 0 north, MSB first
    bits.tofile(os.path.join(OUT, 'walk.bin'))

    # ---- trees: uint16 x10 (x, y, z-zmin, h, r) and form flag in the low bit of r
    T = tj['trees']
    arr = np.zeros((len(T), 5), '<u2')
    for k, t in enumerate(T):
        arr[k] = [round(t['x'] * 10), round(t['y'] * 10), round((t['z'] - zmin) * 100),
                  round(t['h'] * 10), (round(t['r'] * 10) << 1) | (1 if t['form'] == 'conifer' else 0)]
    arr.tofile(os.path.join(OUT, 'trees.bin'))

    # ---- world.json
    B = []
    for b in bj['buildings']:
        k = 'n' if b['neighbourhood'] else ('c' if b['name'] else 'u')
        o = dict(i=b['osm_id'], k=k, r=flat(b['ring']), b=r1(b['base_z']), t=r1(b['top_z']), rf=b['roof']['type'])
        if b.get('holes'):
            o['h'] = [flat(h) for h in b['holes']]
        if b['roof']['type'] == 'gable':
            o['rt'] = [r1(z) for z in b['ring_top_z']]
            o['p'] = [dict(r=flat(pt['ring']), pl=pt['plane']) for pt in b['roof']['parts']]
        if b['name']:
            o['n'] = b['name']
        if b.get('map_no') and b['map_no'] != '—':
            o['m'] = b['map_no']
        if b['category'] and not b['neighbourhood']:
            o['c'] = b['category']
        B.append(o)
    S = []
    for s in sj['signs']:
        S.append(dict(i=s['osm_id'], n=s['name'], m=s['map_no'], c=s['category'], u=s['use'], ch=s['changes'],
                      kd=s['kind'], est=s['door_estimated'], x=s['x'], y=s['y'], z=s['z'], f=s['facing_deg'],
                      ent=s['entrances'], d=s['door']))
    areas = []
    for f in g['features']:
        p = f['properties']
        if p['kind'] == 'campus_boundary' or p.get('name') in ('Reed Canyon',) or \
                (p['kind'] == 'woods' and p.get('name')) or (p['kind'] in ('green', 'field') and p.get('name') and
                                                             f['geometry']['type'] == 'Polygon'):
            geom = shape(f['geometry'])
            if geom.geom_type != 'Polygon':
                continue
            areas.append(dict(i=p['id'], n=p.get('name') or 'Reed College campus', k=p['kind'],
                              r=flat(list(geom.simplify(1.0).exterior.coords)[:-1])))
    lakes_o = [dict(i=lk['osm_id'], n=lk['name'], z=lk['z'], r=flat(lk['ring']), h=[flat(h) for h in lk['holes']])
               for lk in water['lakes']]
    streams_o = [dict(i=st['osm_id'], n=st['name'], l=[r1(c) for p3 in st['line'] for c in p3]) for st in water['streams']]
    # start: just south of Eliot Circle, facing Eliot Hall
    ec = next(shape(f['geometry']) for f in g['features']
              if f['properties'].get('name') == 'Eliot Circle' and f['geometry']['type'] == 'Polygon')
    eh = next(b for b in bj['buildings'] if b['name'] == 'Eliot Hall')
    ehc = np.mean(np.array(eh['ring']), 0)
    sx, sy = ec.centroid.x, ec.bounds[1] - 9.0
    world = dict(meta=dict(zmin=zmin, zmax=zmax, hm_w=cols, hm_h=rows, hm_step=2, walk_w=W_PX, walk_h=H_PX,
                           size=[1274, 811], steep_deg=STEEP_DEG,
                           credit='© OpenStreetMap contributors · lidar: Oregon DOGAMI 2014',
                           trees=len(T)),
                 start=dict(x=r1(sx), y=r1(sy), face=[r1(ehc[0]), r1(ehc[1])]),
                 buildings=B, signs=S, bridges=bridges, lakes=lakes_o, streams=streams_o, areas=areas)
    json.dump(world, open(os.path.join(OUT, 'world.json'), 'w'), separators=(',', ':'), ensure_ascii=False)
    tot = 0
    for fn in sorted(os.listdir(OUT)):
        sz = os.path.getsize(os.path.join(OUT, fn)); tot += sz
        print(f'{fn:14s} {sz / 1024:8.1f} KB')
    print(f'total {tot / 1024 / 1024:.2f} MB; blocked {blocked.mean() * 100:.1f} % of cells; trees {len(T)}')


if __name__ == '__main__':
    main()
