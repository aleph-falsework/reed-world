#!/usr/bin/env python3
"""Reed World -> engine-neutral 3D export (world/3d/).

Reads the canonical world (world/lowfi/reed_world.geojson), the 1 m bare earth
(data/raw/lidar/bare_earth_utm10n_1m.tif) and the lidar layers (world/lidar/),
and writes terrain, ground texture, buildings, trees, water and signs in one
frame: metres, x east, y north, z = elevation above sea level (NAVD88, from the
DOGAMI bare earth). The three.js page and the Unreal agent both start from here.

Usage:  python3 scripts/export_3d.py
Writes: world/3d/{heightmap_16.png, heightmap.json, surface_albedo.png, buildings.json,
        trees.json, water.json, signs.json, MANIFEST.json}. MANIFEST.json is written last.
"""
import csv, json, math, os, time
import numpy as np
import rasterio
from rasterio import features as rfeatures
from affine import Affine
from scipy import ndimage
from shapely.geometry import shape, Point, LineString, Polygon, mapping
from shapely.geometry.polygon import orient
from shapely.ops import split, unary_union, nearest_points
from shapely.strtree import STRtree
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)
OUT = P('world', '3d')
H_PX, W_PX = 811, 1274            # 1 m rasters; row r covers y in [810-r, 811-r)
HM_STEP = 2                       # heightmap metres per pixel
TREE_CAP = 11000
DEFAULT_HEIGHT = 7.0
NODATA, BUILDING, WATER, ROAD, PATH, TREE, STEEP, PAVED, LAWN = range(9)
# Signs for features without a building. Text is what the feature is (Reed Canyon and Reed Lake have no CSV row);
# Eliot Circle has a CSV row (#36) and uses it verbatim.
LANDMARKS = {'way/787034614': dict(kind='landmark', use="Reed College's canyon."),
             'way/39516063': dict(kind='landmark', use="Reed College's lake.")}   # same text as gb_export.py
r2 = lambda v: round(float(v), 2)


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------ rasters
def read(path):
    with rasterio.open(path) as r:
        a = r.read(1).astype(np.float32)
        if r.nodata is not None and not np.isnan(r.nodata):
            a[a == r.nodata] = np.nan
    assert a.shape == (H_PX, W_PX), (path, a.shape)
    return a


def fill_nan(a, iters=40):
    """Pyramid fill: unknown cells take smoothed coarse averages, then relax (Laplace) against the known cells.
    East of the survey edge this continues the terrain as a smooth surface instead of streaks."""
    known = ~np.isnan(a)
    if known.all():
        return a.copy()
    if min(a.shape) <= 8:
        idx = ndimage.distance_transform_edt(~known, return_distances=False, return_indices=True)
        return a[tuple(idx)]
    h, w = a.shape
    ph, pw = h + h % 2, w + w % 2
    pad = np.full((ph, pw), np.nan, np.float32); pad[:h, :w] = a
    blk = pad.reshape(ph // 2, 2, pw // 2, 2)
    with np.errstate(invalid='ignore'):
        cnt = (~np.isnan(blk)).sum((1, 3))
        coarse = np.where(cnt > 0, np.nansum(blk, (1, 3)) / np.maximum(cnt, 1), np.nan).astype(np.float32)
    cf = fill_nan(coarse, iters)
    up = ndimage.zoom(cf, 2, order=1)[:h, :w]
    out = np.where(known, a, up).astype(np.float32)
    for _ in range(iters):
        sm = ndimage.uniform_filter(out, 3, mode='nearest')
        out[~known] = sm[~known]
    return out


def burn(geoms, value=1, dtype='uint8', all_touched=False):
    T = Affine(1, 0, 0, 0, -1, H_PX)          # world y = 811 - row at the top edge of row 0 (grid of lidar README)
    if not geoms:
        return np.zeros((H_PX, W_PX), dtype)
    return rfeatures.rasterize(((mapping(g), value) for g in geoms), out_shape=(H_PX, W_PX), transform=T,
                               fill=0, dtype=dtype, all_touched=all_touched)


def poly_mask(poly, all_touched=False):
    return burn([poly], 1, all_touched=all_touched).astype(bool)


def sample(a, x, y, order=1):
    """Value of a 1 m raster at world (x, y); pixel centres are at (c+0.5, 810.5-r)."""
    x, y = np.atleast_1d(x), np.atleast_1d(y)
    return ndimage.map_coordinates(a, [810.5 - y, x - 0.5], order=order, mode='nearest')


# ------------------------------------------------------------------ helpers
def ring_xy(ring):
    c = list(ring.coords)
    if c[0] == c[-1]:
        c = c[:-1]
    return [[r2(x), r2(y)] for x, y in c]


def load_csv():
    rows = {}
    with open(P('data', 'raw', 'reed_college_buildings.csv'), newline='', encoding='utf-8') as fh:
        for row in csv.DictReader(fh):
            rows[row['Map #'].strip()] = row
    return rows


def obb_axes(poly):
    """Long axis direction, centre, half-length and half-width of the minimum rotated rectangle."""
    rect = poly.minimum_rotated_rectangle
    c = np.array(rect.exterior.coords)[:4]
    e1, e2 = c[1] - c[0], c[2] - c[1]
    l1, l2 = np.linalg.norm(e1), np.linalg.norm(e2)
    if l1 >= l2:
        u, L, Wd = e1 / l1, l1, l2
    else:
        u, L, Wd = e2 / l2, l2, l1
    ctr = c.mean(0)
    return u, ctr, L / 2, Wd / 2, rect.area


# ------------------------------------------------------------------ main
def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    g = json.load(open(P('world', 'lowfi', 'reed_world.geojson')))
    feats = g['features']
    csv_rows = load_csv()

    be_raw = read(P('data', 'raw', 'lidar', 'bare_earth_utm10n_1m.tif'))
    ndsm = read(P('world', 'lidar', 'ndsm.tif'))
    canopy = read(P('world', 'lidar', 'canopy.tif'))
    surf = read(P('world', 'lidar', 'surface_1m.tif'))
    lidar_ok = ~np.isnan(be_raw)
    log(f'bare earth nodata: {(~lidar_ok).mean() * 100:.1f} %; filling')
    be = fill_nan(be_raw)
    ndsm0 = np.nan_to_num(ndsm, nan=0.0)
    canopy = np.nan_to_num(canopy, nan=0).astype(bool)
    surf = np.nan_to_num(surf, nan=0).astype(np.uint8)

    # ============================================================ heightmap (vertex-registered, 2 m)
    xs = np.arange(0, 1272 + 1, HM_STEP, dtype=np.float32)          # 637 columns, x = 2*col
    ys = np.arange(810, -1, -HM_STEP, dtype=np.float32)              # 406 rows, y = 810 - 2*row (row 0 north)
    YY, XX = np.meshgrid(ys, xs, indexing='ij')
    sm = ndimage.gaussian_filter(be, 0.7)                            # mild anti-alias before decimation
    hm = sample(sm, XX.ravel(), YY.ravel()).reshape(XX.shape)
    zmin, zmax = float(np.floor(hm.min())), float(np.ceil(hm.max()))
    q = np.round((hm - zmin) / (zmax - zmin) * 65535).astype(np.uint16)
    Image.fromarray(q).save(os.path.join(OUT, 'heightmap_16.png'))
    filled = ~sample(lidar_ok.astype(np.float32), XX.ravel(), YY.ravel(), order=0).reshape(XX.shape).astype(bool)
    hm_meta = dict(file='heightmap_16.png', width=int(q.shape[1]), height=int(q.shape[0]), m_per_px=HM_STEP,
                   registration='vertex', origin=dict(x=0.0, y=810.0, corner='north-west (row 0, col 0)'),
                   x_of_col='x = col * 2', y_of_row='y = 810 - row * 2', extent_m=dict(x=[0, 1272], y=[0, 810]),
                   min_m=zmin, max_m=zmax, decode='z = min_m + value / 65535 * (max_m - min_m)',
                   precision_m=r2((zmax - zmin) / 65535 * 1000) / 1000,
                   source='DOGAMI 2014 OLC Metro lidar bare earth, 1 m, gaussian sigma 0.7 m, bilinear to 2 m',
                   nodata_fill='pyramid average + Laplace relaxation', filled_fraction=r2(filled.mean()),
                   note='East of x ~ 1136 m the survey has no data; that strip is a smooth continuation, not measured.')
    json.dump(hm_meta, open(os.path.join(OUT, 'heightmap.json'), 'w'), indent=1)
    log(f'heightmap {q.shape[1]}x{q.shape[0]}, z {zmin}..{zmax} m, filled {filled.mean() * 100:.1f} %')

    # ============================================================ buildings
    bfeats = [f for f in feats if f['properties']['kind'] == 'building']
    bpolys = [shape(f['geometry']).buffer(0) for f in bfeats]
    btree = STRtree(bpolys)
    water_feats = [f for f in feats if f['properties']['kind'] == 'water']
    water_polys = [shape(f['geometry']).buffer(0) for f in water_feats]
    water_union = unary_union(water_polys)
    pathy = [shape(f['geometry']) for f in feats
             if f['properties']['kind'] in ('path', 'service_road', 'road') and f['geometry']['type'] == 'LineString']
    footpaths = [shape(f['geometry']) for f in feats
                 if f['properties']['kind'] in ('path',) and f['geometry']['type'] == 'LineString']
    path_union = unary_union(pathy)
    foot_union = unary_union(footpaths)
    slope = read(P('world', 'lidar', 'slope_deg.tif'))

    buildings = []
    n_src = {}
    n_pitched = 0
    for f, poly in zip(bfeats, bpolys):
        p = f['properties']
        if poly.geom_type != 'Polygon':
            poly = max(poly.geoms, key=lambda q_: q_.area)
        poly = orient(poly, 1.0)                                    # exterior CCW
        m = poly_mask(poly, all_touched=poly.area < 40)
        if not m.any():
            m = poly_mask(poly, all_touched=True)
        if not m.any():
            continue
        ground = be[m]
        base_z, ground_mean = float(ground.min()), float(ground.mean())
        lid = p.get('height_lidar_m')
        in_lidar = lidar_ok[m].mean() > 0.5
        osm_h = None
        try:
            if p.get('height'):
                osm_h = float(str(p['height']).replace('m', '').strip())
        except ValueError:
            pass
        if osm_h is None and p.get('building:levels'):
            try:
                osm_h = float(p['building:levels']) * 3.5
            except ValueError:
                pass
        if lid is not None and in_lidar and not p.get('lidar_2014_absent') and lid >= 2.5:
            h, src = float(lid), 'lidar_p90'
        elif osm_h:
            h, src = osm_h, ('osm_height' if p.get('height') else 'osm_levels')
        else:
            h, src = DEFAULT_HEIGHT, 'default_7m'
        n_src[src] = n_src.get(src, 0) + 1
        # roof shape from the lidar: spread of the nDSM over the footprint interior
        inner = poly.buffer(-1.0)
        mi = poly_mask(inner) if not inner.is_empty and inner.area > 4 else m
        if not mi.any():
            mi = m
        nd = ndsm0[mi]
        spread = float(np.percentile(nd, 90) - np.percentile(nd, 15)) if src == 'lidar_p90' and nd.size > 8 else None
        u, ctr, hl, hw, obb_area = obb_axes(poly)
        rectish = poly.area / max(obb_area, 1e-6) > 0.78 and len(poly.interiors) == 0
        if src == 'lidar_p90':
            pitched = spread is not None and spread > 1.6 and spread > 0.18 * h
        else:
            pitched = p.get('realm') != 'campus' and poly.area < 400
        roof_type = 'gable' if (pitched and rectish and poly.area < 3500 and hw >= 2.0) else \
                    ('pitched_complex' if pitched else 'flat')
        top_z = ground_mean + h
        rec = dict(osm_id=p['id'], name=p.get('name'), map_no=p.get('map_no'), category=p.get('category'),
                   use=p.get('use'), changes=p.get('changes'), realm=p.get('realm'),
                   neighbourhood=p.get('realm') != 'campus', building=p.get('building'),
                   area_m2=round(poly.area, 1), base_z=r2(base_z), ground_mean_z=r2(ground_mean),
                   height_m=r2(h), height_source=src, top_z=r2(top_z),
                   lidar_2014_absent=bool(p.get('lidar_2014_absent', False)),
                   roof_spread_m=r2(spread) if spread is not None else None)
        ring = ring_xy(poly.exterior)
        holes = [ring_xy(orient(Polygon(h_), 1.0).exterior)[::-1] for h_ in poly.interiors]
        if roof_type == 'gable':
            n_pitched += 1
            rise = float(np.clip(min(spread if spread else hw * 0.7, hw * 0.75), 1.0, 0.5 * h))
            eave_z = top_z - rise
            nperp = np.array([-u[1], u[0]])
            ridge = LineString([tuple(ctr - u * (hl + 5)), tuple(ctr + u * (hl + 5))])
            # insert the ridge crossings into the outer ring so wall tops follow the gables exactly
            ring_ln = LineString(list(poly.exterior.coords))
            pts = list(poly.exterior.coords)[:-1]
            newring = []
            for i in range(len(pts)):
                a, b = np.array(pts[i]), np.array(pts[(i + 1) % len(pts)])
                newring.append(tuple(a))
                da, db = (a - ctr) @ nperp, (b - ctr) @ nperp
                if da * db < 0:
                    t = da / (da - db)
                    newring.append(tuple(a + (b - a) * t))
            zt = lambda x, y: top_z - rise / hw * abs((np.array([x, y]) - ctr) @ nperp)
            rec['ring_top_z'] = [r2(zt(x, y)) for x, y in newring]
            ring = [[r2(x), r2(y)] for x, y in newring]
            parts = []
            for half in split(poly, ridge).geoms:
                hc = np.array(half.representative_point().coords[0])
                side = 1.0 if (hc - ctr) @ nperp >= 0 else -1.0
                # z = top - side*rise/hw * ((x,y)-ctr).nperp  ->  a x + b y + c
                k = -side * rise / hw
                a_, b_ = k * nperp[0], k * nperp[1]
                c_ = top_z - k * (ctr @ nperp)
                parts.append(dict(ring=ring_xy(orient(half, 1.0).exterior), plane=[round(a_, 5), round(b_, 5), r2(c_)]))
            rec['roof'] = dict(type='gable', ridge=[[r2(v) for v in ctr - u * hl], [r2(v) for v in ctr + u * hl]],
                               eave_z=r2(eave_z), ridge_z=r2(top_z), parts=parts,
                               note='plane: z = a*x + b*y + c over each part; ring_top_z = wall top per ring vertex')
        else:
            rec['roof'] = dict(type=roof_type, z=r2(top_z),
                               note=('lidar shows a pitched roof but the footprint is not a simple rectangle; '
                                     'render flat or hip' if roof_type == 'pitched_complex' else 'flat'))
        rec['ring'] = ring
        if holes:
            rec['holes'] = holes
        buildings.append(rec)
    log(f'buildings {len(buildings)} (gable {n_pitched}); height sources {n_src}')

    # ============================================================ signs (doors)
    entrances = {}
    for f in feats:
        p = f['properties']
        if p['kind'] == 'entrance' and p.get('building_id'):
            entrances.setdefault(p['building_id'], []).append(
                dict(id=p['id'], entrance=p.get('entrance'), xy=tuple(f['geometry']['coordinates'])))
    bpoly_of = {f['properties']['id']: orient(bp if bp.geom_type == 'Polygon' else max(bp.geoms, key=lambda q_: q_.area), 1.0)
                for f, bp in zip(bfeats, bpolys)}

    def blocked(pt):
        P_ = Point(pt)
        if water_union.contains(P_):
            return True
        for k in btree.query(P_):
            if bpolys[k].contains(P_):
                return True
        return False

    def edge_normal(poly, pt):
        """Outward normal of the exterior edge nearest pt (exterior is CCW, so outward = (dy, -dx))."""
        c = np.array(poly.exterior.coords)
        best, bn, bq = 1e9, None, None
        for a, b in zip(c[:-1], c[1:]):
            ab = b - a
            L2 = ab @ ab
            if L2 < 1e-9:
                continue
            t = np.clip((np.array(pt) - a) @ ab / L2, 0, 1)
            qpt = a + ab * t
            d = np.linalg.norm(qpt - pt)
            if d < best:
                best, bq = d, qpt
                n = np.array([ab[1], -ab[0]]) / math.sqrt(L2)
                bn = n
        return bq, bn

    def estimate_door(poly):
        """gb_export's rule in metres: a south-facing wall, the point whose doorstep is nearest a path or road,
        with open ground (no building, no water) in front of it."""
        c = np.array(poly.exterior.coords)
        best = None
        for a, b in zip(c[:-1], c[1:]):
            ab = b - a
            L = float(np.linalg.norm(ab))
            if L < 1.5:
                continue
            n = np.array([ab[1], -ab[0]]) / L
            for t in np.arange(0.75, L - 0.74, 1.0) if L > 1.5 else [L / 2]:
                pt = a + ab * (t / L)
                step = pt + n * 1.5
                if blocked(step):
                    continue
                d = path_union.distance(Point(step))
                south = n[1] < -0.35
                score = d + (0 if south else 20.0)
                if best is None or score < best[0]:
                    best = (score, pt, n)
        if best is None:
            pt = np.array(poly.exterior.interpolate(0).coords[0])
            q_, n = edge_normal(poly, pt)
            return q_, n
        return best[1], best[2]

    def sign_spot(door, n):
        t = np.array([-n[1], n[0]])
        for off in ([2.6, 1.8], [2.6, -1.8], [3.2, 0.0], [4.5, 2.5], [4.5, -2.5], [2.0, 0.0]):
            s = door + n * off[0] + t * off[1]
            if not blocked(s):
                return s
        return door + n * 2.0

    signs = []
    for b in buildings:
        if b['neighbourhood'] or not b['name']:
            continue
        poly = bpoly_of[b['osm_id']]
        ents = entrances.get(b['osm_id'], [])
        if ents:
            e = sorted(ents, key=lambda e: (e['entrance'] != 'main', e['id']))[0]
            door, n = edge_normal(poly, np.array(e['xy']))
            est, ent_ids = False, [x['id'] for x in ents]
        else:
            door, n = estimate_door(poly)
            est, ent_ids = True, []
        s = sign_spot(door, n)
        row = csv_rows.get(str(b['map_no']).strip()) if b['map_no'] else None
        signs.append(dict(osm_id=b['osm_id'], kind='building', name=b['name'], map_no=b['map_no'],
                          category=b['category'], use=b['use'], changes=b['changes'],
                          csv=row, door=[r2(door[0]), r2(door[1])], door_estimated=est, entrances=ent_ids,
                          x=r2(s[0]), y=r2(s[1]), z=r2(sample(be, s[0], s[1])[0]),
                          facing_deg=r2(math.degrees(math.atan2(n[1], n[0]))),
                          facing_note='board normal, degrees counter-clockwise from +x (east)'))
    # landmarks: Reed Canyon, Reed Lake, Eliot Circle
    for f in feats:
        p = f['properties']
        oid = p['id']
        if oid not in LANDMARKS and p.get('name') != 'Eliot Circle':
            continue
        if f['geometry']['type'] not in ('Polygon', 'MultiPolygon'):
            continue
        poly = shape(f['geometry']).buffer(0)
        if p.get('name') == 'Eliot Circle':
            # at the south edge of the lawn, facing south (towards the drive and the start point)
            minx, miny, maxx, maxy = poly.bounds
            cx = poly.centroid.x
            ln = LineString([(cx, miny - 5), (cx, maxy + 5)]).intersection(poly)
            y0 = min(c[1] for c in ln.coords) if not ln.is_empty else miny
            s = np.array([cx + 3.0, y0 + 2.0]); n = np.array([0.0, -1.0])
            use, est = p.get('use'), False
        else:
            if oid == 'way/39516063':                                # Reed Lake: on the footpath nearest the lake
                target = poly
                cand = foot_union.intersection(poly.buffer(30)).difference(poly.buffer(3))
            else:   # Reed Canyon: footpath inside it nearest the GB signpost (canyon_east, ~x 1032 y 531)
                target = Point(1032, 531)
                cand = foot_union.intersection(poly).difference(water_union.buffer(4))
            if cand.is_empty:
                cand = path_union
            pa, pb = nearest_points(cand, target if oid != 'way/39516063' else poly.centroid)
            s0 = np.array(pa.coords[0])
            # step 2 m off the path, away from the water
            wq = nearest_points(water_union, Point(s0))[0]
            away = s0 - np.array(wq.coords[0]); away /= (np.linalg.norm(away) + 1e-9)
            s = s0 + away * 2.0
            if blocked(s):
                s = s0
            n = -away
            use, est = LANDMARKS[oid]['use'], False
        row = csv_rows.get(str(p.get('map_no')).strip()) if p.get('map_no') else None
        signs.append(dict(osm_id=oid, kind='landmark', name=p.get('name'), map_no=p.get('map_no'),
                          category=p.get('category') or 'Landmark', use=use, changes=p.get('changes'), csv=row,
                          door=None, door_estimated=False, entrances=[],
                          x=r2(s[0]), y=r2(s[1]), z=r2(sample(be, s[0], s[1])[0]),
                          facing_deg=r2(math.degrees(math.atan2(n[1], n[0]))),
                          text_source='CSV' if row else 'name only (no CSV row)'))
    log(f'signs {len(signs)} (estimated doors {sum(s["door_estimated"] for s in signs)})')

    # ============================================================ trees
    nds = ndimage.gaussian_filter(ndsm0, 1.0)
    canm = canopy & lidar_ok
    mx = ndimage.maximum_filter(nds, size=5)
    peaks = canm & (nds >= mx - 1e-4) & (nds >= 3.0)
    rr, cc = np.nonzero(peaks)
    hh = nds[rr, cc]
    order = np.argsort(-hh)
    rr, cc, hh = rr[order], cc[order], hh[order]
    n_peaks = len(rr)
    # fill candidates: the rest of the canopy (every other pixel, random order), so merged crowns that have no
    # separate 5x5 maximum still get instances; peaks always win because they are tried first
    fill = canm & (nds >= 3.0) & ~peaks
    fill[1::2, :] = False; fill[:, 1::2] = False
    fr, fc = np.nonzero(fill)
    perm = np.random.default_rng(11).permutation(len(fr))
    fr, fc = fr[perm], fc[perm]
    rr = np.concatenate([rr, fr]); cc = np.concatenate([cc, fc]); hh = np.concatenate([hh, nds[fr, fc]])
    edt = ndimage.distance_transform_edt(canm)

    def poisson(factor):
        cell = 4.0
        grid = {}
        keep = []
        for k in range(len(rr)):
            x, y, h = cc[k] + 0.5, 810.5 - rr[k], float(hh[k])
            dmin = max(3.0, factor * h)
            gx, gy = int(x // cell), int(y // cell)
            rad = int(math.ceil(dmin / cell))
            ok = True
            for ix in range(gx - rad, gx + rad + 1):
                for iy in range(gy - rad, gy + rad + 1):
                    for (x2, y2) in grid.get((ix, iy), ()):
                        if (x - x2) ** 2 + (y - y2) ** 2 < dmin * dmin:
                            ok = False; break
                    if not ok: break
                if not ok: break
            if ok:
                grid.setdefault((gx, gy), []).append((x, y))
                keep.append(k)
        return keep
    factor = 0.28
    keep = poisson(factor)
    while len(keep) > TREE_CAP:
        factor *= 1.12
        keep = poisson(factor)
    log(f'tree peaks {n_peaks}, candidates {len(rr)}, kept {len(keep)} (spacing {factor:.2f} x height)')
    trees = []
    ang = np.linspace(0, 2 * np.pi, 8, endpoint=False)
    for k in keep:
        r_, c_, h = rr[k], cc[k], float(hh[k])
        x, y = c_ + 0.5, 810.5 - r_
        crown = float(np.clip(min(0.3 * h + 1.5, edt[r_, c_] + 1.5), 1.5, 9.0))
        # crown shape from the lidar: how fast the surface falls away 3 m from the apex
        ring = sample(nds, x + 3 * np.cos(ang), y + 3 * np.sin(ang))
        fall = float(ring.mean() / max(h, 1e-3))
        form = 'conifer' if ((h >= 18 and fall < 0.92) or (12 <= h < 18 and fall < 0.84)) else 'broadleaf'
        if form == 'conifer':                      # Douglas fir / cedar crowns are narrow: ~0.18 h + 1 m
            crown = float(np.clip(min(crown, 0.18 * h + 1.0), 1.5, 6.5))
        trees.append(dict(x=r2(x), y=r2(y), z=r2(sample(be, x, y)[0]), h=r2(h), r=r2(crown), form=form, src='lidar'))
    # fill instances (not a 5x5 maximum) have no apex to measure: take the form of the nearest measured peak
    from scipy.spatial import cKDTree
    pk = [i for i, k in enumerate(keep) if k < n_peaks]
    if pk:
        kd = cKDTree([(trees[i]['x'], trees[i]['y']) for i in pk])
        for i, k in enumerate(keep):
            if k >= n_peaks:
                d, j = kd.query((trees[i]['x'], trees[i]['y']))
                trees[i]['form'] = trees[pk[j]]['form'] if d < 15 else 'broadleaf'
                if trees[i]['form'] == 'conifer':
                    trees[i]['r'] = r2(np.clip(min(trees[i]['r'], 0.18 * trees[i]['h'] + 1.0), 1.5, 6.5))
    # OSM trees: tag the nearest lidar tree, or add one
    tx = np.array([t['x'] for t in trees]); ty = np.array([t['y'] for t in trees])
    n_osm_new = 0
    for f in feats:
        p = f['properties']
        if p['kind'] not in ('tree', 'landmark_tree'):
            continue
        x, y = f['geometry']['coordinates']
        d2 = (tx - x) ** 2 + (ty - y) ** 2 if len(tx) else np.array([1e9])
        k = int(np.argmin(d2))
        if d2[k] < 9.0:
            trees[k]['osm_id'] = p['id']
            if p.get('name'): trees[k]['name'] = p['name']
            continue
        c_ = int(np.clip(x, 0, W_PX - 1)); r_ = int(np.clip(810.5 - y, 0, H_PX - 1))
        hmax = float(ndsm0[max(r_ - 3, 0):r_ + 4, max(c_ - 3, 0):c_ + 4].max()) if lidar_ok[r_, c_] else 0
        h = hmax if hmax >= 3 else 9.0
        trees.append(dict(x=r2(x), y=r2(y), z=r2(sample(be, x, y)[0]), h=r2(h), r=r2(np.clip(0.3 * h + 1.5, 1.5, 7)),
                          form='broadleaf', src='osm' + ('' if hmax >= 3 else '_default_height'), osm_id=p['id'],
                          **({'name': p['name']} if p.get('name') else {})))
        n_osm_new += 1
    log(f'trees {len(trees)} (+{n_osm_new} OSM-only); conifers {sum(t["form"] == "conifer" for t in trees)}')

    # ============================================================ water
    water = []
    for f, wp in zip(water_feats, water_polys):
        p = f['properties']
        polys = [wp] if wp.geom_type == 'Polygon' else list(wp.geoms)
        for q_ in polys:
            m = poly_mask(q_, all_touched=True)
            zs = be[m]
            z = float(np.percentile(zs, 10)) if zs.size else float(sample(be, q_.centroid.x, q_.centroid.y)[0])
            q_ = orient(q_, 1.0)
            water.append(dict(osm_id=p['id'], name=p.get('name'), kind='lake', z=r2(z), ring=ring_xy(q_.exterior),
                              holes=[ring_xy(orient(Polygon(h_), 1.0).exterior)[::-1] for h_ in q_.interiors],
                              area_m2=round(q_.area, 1), z_source='p10 of bare earth inside the polygon'))
    streams = []
    for f in feats:
        p = f['properties']
        if p['kind'] != 'stream' or f['geometry']['type'] != 'LineString':
            continue
        ln = shape(f['geometry'])
        n = max(2, int(ln.length // 2) + 1)
        pts = [ln.interpolate(d) for d in np.linspace(0, ln.length, n)]
        xs_ = np.array([q_.x for q_ in pts]); ys_ = np.array([q_.y for q_ in pts])
        zl = ndimage.minimum_filter(be, size=5)
        zs = sample(zl, xs_, ys_)
        zs = np.minimum.accumulate(zs) if zs[0] > zs[-1] else np.minimum.accumulate(zs[::-1])[::-1]
        streams.append(dict(osm_id=p['id'], name=p.get('name'), kind='stream', width_m=2.0,
                            line=[[r2(a), r2(b), r2(c)] for a, b, c in zip(xs_, ys_, zs)],
                            z_source='5 m local minimum of bare earth, made monotonic downstream'))
    log(f'water polygons {len(water)}, streams {len(streams)}')

    # ============================================================ albedo
    albedo = make_albedo(surf, be, lidar_ok, slope, bpolys, ndsm0)
    Image.fromarray(albedo).save(os.path.join(OUT, 'surface_albedo.png'), optimize=True)

    # ============================================================ write
    frame = dict(units='metres', x='east', y='north', z='elevation above sea level (m, NAVD88 via DOGAMI bare earth)',
                 crs=g['meta']['crs'], origin_utm=g['meta']['origin_utm'], size_m=[1274, 811],
                 raster_rule='1 m rasters: row 0 is north; pixel (r, c) covers x in [c, c+1), y in [810-r, 811-r)')
    credit = '© OpenStreetMap contributors (ODbL) · lidar: Oregon DOGAMI 2014 (OLC Metro)'
    dump = lambda name, obj: json.dump(obj, open(os.path.join(OUT, name), 'w'), separators=(',', ':'), ensure_ascii=False)
    dump('buildings.json', dict(frame=frame, credit=credit, count=len(buildings),
                                height_rule='lidar p90 nDSM over the footprint (2014); else OSM height; else '
                                            'building:levels x 3.5 m; else 7 m. Measured from ground_mean_z: '
                                            'top_z = ground_mean_z + height_m. Extrude walls from base_z (lowest '
                                            'ground under the footprint) to top_z.',
                                roof_rule='gable when the lidar nDSM spread over the interior (p90-p15) > 1.6 m and '
                                          '> 18 % of height and the footprint fills > 78 % of its oriented box; '
                                          'without lidar, neighbourhood houses < 400 m2 are gabled. Ridge along the '
                                          'long axis of the oriented box.',
                                buildings=buildings))
    dump('trees.json', dict(frame=frame, credit=credit, count=len(trees),
                            method=f'local maxima (5x5) of the nDSM (gaussian 1 m) inside canopy.tif, >= 3 m, then the rest of the canopy (2 m stride, random order) as fill candidates, '
                                   f'Poisson-disk thinned tallest first with spacing max(3 m, {factor:.2f} x height); '
                                   'crown radius = clamp(min(0.3h + 1.5, distance to canopy edge + 1.5), 1.5, 9); '
                                   'form = conifer when the smoothed surface 3 m from the apex is < 92 % of the apex height '
                                   '(h >= 18 m) or < 84 % (12-18 m); a heuristic, not a species map. Conifer crown '
                                   'radius is capped at 0.18 h + 1 m. Fill instances copy the nearest peak within 15 m. No lidar east of x ~ 1136 m, so that strip has OSM trees only.',
                            fields=dict(x='m', y='m', z='ground elevation m', h='height above ground m',
                                        r='crown radius m', form='conifer|broadleaf', src='lidar|osm'),
                            trees=trees))
    dump('water.json', dict(frame=frame, credit=credit, lakes=water, streams=streams))
    dump('signs.json', dict(frame=frame, credit=credit, count=len(signs),
                            door_rule='OSM entrance node (main first) snapped to the footprint; otherwise estimated '
                                      'as in scripts/gb_export.py: a south-facing wall point whose doorstep is open '
                                      'and nearest a path or road (door_estimated: true). The sign stands 2.6 m out '
                                      'from the door and 1.8 m to the side.',
                            text_rule='use/changes/category are the full CSV text (data/raw/reed_college_buildings.csv); '
                                      'csv holds the matched row verbatim. Reed Canyon and Reed Lake have no CSV row.',
                            signs=signs))
    files = {}
    for fn in ('heightmap_16.png', 'heightmap.json', 'surface_albedo.png', 'buildings.json', 'trees.json',
               'water.json', 'signs.json'):
        files[fn] = dict(bytes=os.path.getsize(os.path.join(OUT, fn)))
    files['heightmap_16.png'].update(desc='16-bit grayscale bare earth, 2 m, vertex-registered; see heightmap.json')
    files['heightmap.json'].update(desc='decode + georeference for heightmap_16.png')
    files['surface_albedo.png'].update(desc='RGB ground colour, 1 m, 1274x811, row 0 north; pixel (r,c) covers '
                                            'x [c,c+1), y [810-r, 811-r). Stylised palette (not photoreal).',
                                       palette=PALETTE_DOC)
    files['buildings.json'].update(desc=f'{len(buildings)} footprints (CCW rings), heights, roofs', count=len(buildings))
    files['trees.json'].update(desc=f'{len(trees)} tree instances', count=len(trees))
    files['water.json'].update(desc=f'{len(water)} lake polygons at surface elevation, {len(streams)} stream lines with z')
    files['signs.json'].update(desc=f'{len(signs)} signs at doors / landmarks', count=len(signs))
    manifest = dict(name='Reed World 3D export', generator='scripts/export_3d.py',
                    generated=time.strftime('%Y-%m-%dT%H:%M:%S%z'), frame=frame, credit=credit, files=files,
                    up_axis='z (engine mapping is up to the consumer; three.js page uses X=x, Y=z, Z=-y)',
                    status='complete')
    dump('MANIFEST.json', manifest)                                   # last: the Unreal agent polls for this
    log(f'done in {time.time() - t0:.1f} s -> {OUT}')


# ------------------------------------------------------------------ albedo
PAL = {  # deliberate, muted Pacific-Northwest palette: sage lawns, cream paths, slate roads, teal water
    'lawn': (138, 163, 101), 'lawn2': (124, 150, 92), 'yard': (133, 152, 104), 'tree_ground': (88, 104, 66),
    'paved': (196, 189, 173), 'path': (234, 220, 186), 'path_edge': (196, 178, 136), 'road': (104, 108, 114),
    'road_edge': (160, 156, 146), 'water': (58, 104, 112), 'steep': (139, 118, 84), 'building': (120, 112, 100),
}
PALETTE_DOC = {k: '#%02x%02x%02x' % v for k, v in PAL.items()}


def make_albedo(surf, be, lidar_ok, slope, bpolys, ndsm0):
    rgb = np.zeros(surf.shape + (3,), np.float32)
    put = lambda m, c: rgb.__setitem__(m, np.array(c, np.float32))
    # low-frequency lawn variation so big lawns are not flat
    rng = np.random.default_rng(7)
    noise = ndimage.gaussian_filter(rng.standard_normal(surf.shape).astype(np.float32), 6)
    noise /= noise.std() + 1e-6
    fine = ndimage.gaussian_filter(rng.standard_normal(surf.shape).astype(np.float32), 0.8)
    fine /= fine.std() + 1e-6
    lawn = np.isin(surf, [LAWN])
    t = np.clip(noise * 0.35 + 0.5, 0, 1)[..., None]
    rgb[:] = np.array(PAL['yard'], np.float32)
    rgb[lawn] = (np.array(PAL['lawn']) * (1 - t) + np.array(PAL['lawn2']) * t)[lawn]
    nd_ = surf == NODATA
    rgb[nd_] = (np.array(PAL['yard']) * (1 - t) + np.array(PAL['lawn2']) * t)[nd_]
    put(surf == PAVED, PAL['paved'])
    put(surf == TREE, PAL['tree_ground'])
    put(surf == STEEP, PAL['steep'])
    # steep ground hidden under canopy (see lidar README): tint it toward earth so canyon walls read
    hid = (surf == TREE) & (np.nan_to_num(slope) > 25)
    rgb[hid] = rgb[hid] * 0.55 + np.array(PAL['steep']) * 0.45
    put(surf == WATER, PAL['water'])
    put(surf == ROAD, PAL['road'])
    put(surf == PATH, PAL['path'])
    put(surf == BUILDING, PAL['building'])
    # outlines: paths get a darker edge, roads a pale kerb
    pm = surf == PATH
    edge = pm & ~ndimage.binary_erosion(pm, iterations=1)
    rgb[edge] = rgb[edge] * 0.35 + np.array(PAL['path_edge']) * 0.65
    rm = surf == ROAD
    kerb = rm & ~ndimage.binary_erosion(rm, iterations=1)
    rgb[kerb] = np.array(PAL['road_edge'])
    # contact shadow around buildings (ambient-occlusion stand-in)
    bm = surf == BUILDING
    d = ndimage.distance_transform_edt(~bm)
    ao = np.clip(d / 3.0, 0, 1) * 0.22 + 0.78
    ao[bm] = 1.0
    # soft hillshade from the bare earth (lighting in-engine adds the rest; keep this mild)
    zs = ndimage.gaussian_filter(be, 1.5)
    gy, gx = np.gradient(zs)                       # rows run south, so d/dy(world) = -gy
    nx, ny = -gx, gy
    az, alt = math.radians(315), math.radians(45)
    lx, ly, lz = math.cos(alt) * math.sin(az), math.cos(alt) * math.cos(az), math.sin(alt)
    shade = (lx * nx + ly * ny + lz) / np.sqrt(nx ** 2 + ny ** 2 + 1)
    shade = 0.82 + 0.28 * (shade - shade.mean()) / (shade.std() * 3 + 1e-6) + 0.18
    shade = np.clip(shade, 0.72, 1.12)
    grain = 1.0 + 0.03 * fine
    grain[pm | rm] = 1.0 + 0.012 * fine[pm | rm]
    out = rgb * (shade * ao * grain)[..., None]
    # canopy floor: darker where the trees are tall (deep shade in the canyon)
    tall = np.clip(ndimage.gaussian_filter(ndsm0, 3) / 30.0, 0, 1)
    out[surf == TREE] *= (1 - 0.25 * tall[surf == TREE])[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


if __name__ == '__main__':
    main()
