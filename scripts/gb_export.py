#!/usr/bin/env python3
"""Reed World -> GB Studio scene specs.

Cuts the world layer into scenes (zones), builds a metatile class grid from the
1 m lidar surface map, places buildings (roof + south facade + doors), signposts
and exits, renders a 4-shade background per scene and checks GB Studio limits.

Usage:  python3 scripts/gb_export.py            (from the repo root or anywhere)
        python3 scripts/gb_export.py --kit temp  (force the temporary tiles)

Writes world/gb/: zones.json, zones_overlay.png, <scene>/background.png,
<scene>/collision.png, <scene>/spec.json, project_spec.json, REPORT.md.
See docs/ZONES.md for the method and decisions.
"""
import json, os, re, sys, math, unicodedata, argparse
from collections import Counter, defaultdict, deque
import numpy as np
from scipy import ndimage
import rasterio
from rasterio import features as rfeatures
from affine import Affine
from shapely.geometry import shape, box, LineString, Point, mapping
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)
OUT = P('world', 'gb')

MT_M = 6                     # metres per 16x16 metatile (locked)
TILE_M = MT_M / 2            # metres per 8x8 GB tile
PAL = ['#071821', '#306850', '#86c06c', '#e0f8cf']   # shade 0 (dark) .. 3 (light)
PAL_RGB = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in PAL], np.uint8)
TILE_LIMIT, TILE_TARGET = 192, 180
MAX_ACTORS, MAX_TRIGGERS = 20, 30
TEXT_COLS, TEXT_LINES = 18, 6   # 6 lines = two full 3-line text boxes (4 left a lone line in box 2)
EXIT_CHUNK = 6               # max exit trigger length in tiles before splitting (grows if >30 triggers)

# ---------------------------------------------------------------- zones
# World metres, x east, y north. Every edge is a multiple of 6 m (one metatile).
# 'cells' = the OVERNIGHT.md proposal cells each zone is built from; see docs/ZONES.md.
ZONES = [
    dict(id='front_lawn', name='Front Lawn', x0=456, x1=816, y0=180, y1=354, cells=['C-S', 'EC-S (west)']),
    dict(id='old_campus', name='Old Campus', x0=456, x1=816, y0=354, y1=486, cells=['C-M', 'EC-M (west)']),
    dict(id='west_campus', name='West Campus', x0=216, x1=456, y0=108, y1=300, cells=['WC-S']),
    dict(id='performing_arts', name='Performing Arts & Sports', x0=216, x1=456, y0=300, y1=486, cells=['WC-M']),
    dict(id='science_quad', name='Science Quad', x0=816, x1=978, y0=330, y1=486, cells=['EC-M (east)']),
    dict(id='language_houses', name='Language Houses', x0=816, x1=978, y0=168, y1=330, cells=['EC-S (east)']),
    dict(id='studio_arts', name='Studio Arts', x0=978, x1=1140, y0=168, y1=366, cells=['E-S', 'E-M (south)']),
    dict(id='canyon_east', name='Reed Canyon East', x0=978, x1=1140, y0=366, y1=600, cells=['E-M (north)', 'E-N (south)']),
    dict(id='reed_lake', name='Reed Lake', x0=696, x1=978, y0=486, y1=600, cells=['EC-N (south)']),
    dict(id='cross_canyon', name='Cross Canyon', x0=468, x1=696, y0=486, y1=810, cells=['C-N']),
    dict(id='the_grove', name='The Grove', x0=216, x1=468, y0=486, y1=810, cells=['WC-N']),
    dict(id='northwest', name='Northwest Campus', x0=0, x1=216, y0=486, y1=810, cells=['W-N']),
]
PROPOSAL_COLS = [0, 215, 470, 700, 960, 1273]
PROPOSAL_ROWS = [60, 300, 480, 810]
START_LABEL = 'front_lawn'
MAIN_ENTRANCE_XY = (733, 212)   # where the main drive (way/158891925) leaves SE Woodstock Blvd

# ---------------------------------------------------------------- classes
NODATA, BUILDING, WATER, ROAD, PATH, TREE, STEEP, PAVED, LAWN = range(9)   # surface_1m.tif values
# metatile classes
C = dict(LAWN=0, PAVED=1, PATH=2, ROAD=3, WATER=4, TREE=5, FOREST=6, STEEP=7, BRIDGE=8,
         ROOF=9, FACADE=10, DOOR=11, LONETREE=12)
CN = {v: k for k, v in C.items()}
# TREE (forest edge) is solid too: it is drawn with the same canopy as the interior, so walking over it
# read as walking over trees. Paths and paved ground through woods are their own classes and stay open.
SOLID = {C['WATER'], C['TREE'], C['FOREST'], C['LONETREE'], C['STEEP'], C['ROOF'], C['FACADE']}
WALKABLE = set(C.values()) - SOLID
SLIVERS = []
PATH_LINKS = set()   # ((j,i),(j2,i2)) metatile pairs an OSM footway steps between (south-up)


def log(*a):
    print(*a, flush=True)


def ascii_text(s):
    s = s.replace('—', '-').replace('–', '-').replace('’', "'").replace('‘', "'")
    s = s.replace('“', '"').replace('”', '"').replace('&', 'and')
    return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()


# ================================================================ load
def load():
    g = json.load(open(P('world', 'lowfi', 'reed_world.geojson')))
    with rasterio.open(P('world', 'lidar', 'surface_1m.tif')) as r:
        surf_n = r.read(1)
    H = surf_n.shape[0]                       # 811; raster row r covers y in [H-1-r, H-r)
    surf = surf_n[::-1]                       # south-up: surf[k] covers y in [k, k+1)
    feats = g['features']
    return g, feats, surf, H


def rasterize_ids(geoms_ids, H, W):
    """Burn polygons -> int index raster, south-up, 1 m."""
    T = Affine(1, 0, 0, 0, -1, H)
    out = rfeatures.rasterize(((mapping(gm), i) for gm, i in geoms_ids), out_shape=(H, W), transform=T,
                              fill=0, dtype='int32')
    return out[::-1]


# ================================================================ zones
def validate_zones(campus_bldgs):
    problems = []
    for z in ZONES:
        for k in ('x0', 'x1', 'y0', 'y1'):
            if z[k] % MT_M:
                problems.append(f"{z['id']}.{k}={z[k]} is not a multiple of {MT_M}")
        z['w_mt'], z['h_mt'] = (z['x1'] - z['x0']) // MT_M, (z['y1'] - z['y0']) // MT_M
        z['w'], z['h'] = z['w_mt'] * 2, z['h_mt'] * 2             # 8x8 tiles
        z['w_px'], z['h_px'] = z['w'] * 8, z['h'] * 8
        if z['w_px'] > 2040 or z['h_px'] > 2040 or z['w_px'] * z['h_px'] > 1048320:
            problems.append(f"{z['id']} too large: {z['w_px']}x{z['h_px']}")
    for i, a in enumerate(ZONES):
        for b in ZONES[i + 1:]:
            if box(a['x0'], a['y0'], a['x1'], a['y1']).intersection(box(b['x0'], b['y0'], b['x1'], b['y1'])).area > 0:
                problems.append(f"{a['id']} overlaps {b['id']}")
    split, outside = [], []
    for bid, name, poly in campus_bldgs:
        parts = {z['id']: box(z['x0'], z['y0'], z['x1'], z['y1']).intersection(poly).area for z in ZONES}
        inside = [k for k, a in parts.items() if a > 1e-6]
        # a sliver under 5 m2 and 2 % of the footprint never reaches 40 % of a metatile, so it is not drawn
        real = [k for k in inside if parts[k] >= 5 or parts[k] >= 0.02 * poly.area]
        if len(real) > 1:
            split.append((bid, name, inside))
        elif len(inside) > 1:
            SLIVERS.append(f"{name or bid} ({bid}): {min(parts[k] for k in inside):.1f} m² sliver across a zone edge, not drawn")
        if not inside:
            outside.append((bid, name))
    return problems, split, outside


def zone_of_point(x, y):
    for z in ZONES:
        if z['x0'] <= x < z['x1'] and z['y0'] <= y < z['y1']:
            return z
    return None


def proposal_report(campus_bldgs):
    """For each proposed cut line, report whether a building-free position exists within +-40 m (6 m steps)."""
    lines = []
    def free(L):
        return not any(p.intersects(L) for _, _, p in campus_bldgs)
    for c in PROPOSAL_COLS[1:-1]:
        cands = [x for x in range(c - 40, c + 41) if x % MT_M == 0 and free(LineString([(x, 60), (x, 810)]))]
        lines.append(f"column x={c}: building-free 6 m positions within +-40 m over the full height: {cands or 'none'}")
    for r in PROPOSAL_ROWS[1:-1]:
        cands = [y for y in range(r - 40, r + 41) if y % MT_M == 0 and free(LineString([(0, y), (1273, y)]))]
        lines.append(f"row y={r}: building-free 6 m positions within +-40 m across the full width: {cands or 'none'}")
    return lines


# ================================================================ metatile class grid (global, south-up)
def trace_lines(lines, MH, MW, links=None):
    """4-connected supercover of line features on the metatile grid (south-up).
    If `links` is a set, each pair of 4-adjacent cells a way steps between is added to it, so path
    tiles can connect along the way itself rather than to every adjacent path cell."""
    m = np.zeros((MH, MW), bool)
    inb = lambda c: 0 <= c[0] < MH and 0 <= c[1] < MW

    def link(a, b):
        if links is not None and a != b and inb(a) and inb(b):
            links.add((a, b)); links.add((b, a))
    for ln in lines:
        parts = getattr(ln, 'geoms', [ln])
        for part in parts:
            L = part.length
            if L == 0:
                continue
            prev = None
            for d in np.arange(0, L + 0.25, 0.25):
                pt = part.interpolate(min(d, L))
                cell = (int(pt.y // MT_M), int(pt.x // MT_M))
                if prev and cell != prev and cell[0] != prev[0] and cell[1] != prev[1]:
                    mid = (prev[0], cell[1])            # step through an orthogonal neighbour
                    if 0 <= mid[0] < MH and 0 <= mid[1] < MW:
                        m[mid] = True
                    link(prev, mid); link(mid, cell)
                elif prev and cell != prev:
                    link(prev, cell)
                if 0 <= cell[0] < MH and 0 <= cell[1] < MW:
                    m[cell] = True
                prev = cell
    return m


def class_grid(surf, bid_r, bridge_r, osm_ids, campus_idx, named_idx, lines=None):
    MH, MW = 810 // MT_M, 1272 // MT_M
    s = surf[:MH * MT_M, :MW * MT_M].reshape(MH, MT_M, MW, MT_M).transpose(0, 2, 1, 3).reshape(MH, MW, MT_M * MT_M)
    b = bid_r[:MH * MT_M, :MW * MT_M].reshape(MH, MT_M, MW, MT_M).transpose(0, 2, 1, 3).reshape(MH, MW, MT_M * MT_M)
    br = bridge_r[:MH * MT_M, :MW * MT_M].reshape(MH, MT_M, MW, MT_M).transpose(0, 2, 1, 3).reshape(MH, MW, -1)
    n = MT_M * MT_M
    frac = np.stack([(s == k).sum(-1) for k in range(9)], -1) / n
    bfrac = (b > 0).sum(-1) / n
    cls = np.full((MH, MW), C['LAWN'], np.int16)
    mid = np.zeros((MH, MW), np.int32)        # building index (into osm_ids), 0 = none
    # background majority among the non-thin classes
    bg_order = [WATER, TREE, STEEP, PAVED, LAWN, NODATA]
    bg = np.array(bg_order)[np.argmax(frac[..., bg_order], -1)]
    bgmap = {WATER: C['WATER'], TREE: C['TREE'], STEEP: C['STEEP'], PAVED: C['PAVED'], LAWN: C['LAWN'], NODATA: C['LAWN']}
    for k, v in bgmap.items():
        cls[bg == k] = v
    cls[frac[..., STEEP] >= 0.34] = C['STEEP']
    cls[frac[..., WATER] >= 0.30] = C['WATER']
    pr = np.maximum(frac[..., PATH], frac[..., ROAD])
    thin = pr >= 0.20
    cls[thin & (frac[..., PATH] >= frac[..., ROAD])] = C['PATH']
    cls[thin & (frac[..., PATH] < frac[..., ROAD])] = C['ROAD']
    cls[(cls == C['PATH']) & (br.sum(-1) >= 0.10 * n)] = C['BRIDGE']
    # keep thin features connected: every metatile an OSM way passes through (4-connected) is path/road,
    # unless it is mostly open water (then only a bridge may cross it)
    if lines:
        road_m = trace_lines(lines['road'], MH, MW)
        path_m = trace_lines(lines['path'], MH, MW, PATH_LINKS)
        bridge_m = trace_lines(lines['bridge'], MH, MW)
        wet = frac[..., WATER] >= 0.70
        cls[road_m & ~wet & (cls != C['PATH']) & (cls != C['BRIDGE'])] = C['ROAD']
        cls[path_m & ~wet & ~road_m] = C['PATH']
        # Lidar 'path' with no OSM way through it (hard-standing, shade, roof edges) drew as wide pale
        # blobs that merged with the real footpaths. Draw it as lawn: still walkable, so connectivity
        # is unchanged, but the drawn paths follow the mapped ways.
        cls[(cls == C['PATH']) & road_m & ~path_m & ~bridge_m] = C['ROAD']
        cls[(cls == C['PATH']) & ~path_m & ~bridge_m] = C['LAWN']
        cls[bridge_m] = C['BRIDGE']
        cls[(cls == C['PATH']) & (br.sum(-1) >= 0.10 * n)] = C['BRIDGE']
    bmask = bfrac >= 0.40
    cls[bmask] = C['ROOF']
    for j, i in zip(*np.nonzero(bmask)):
        v = b[j, i][b[j, i] > 0]
        mid[j, i] = Counter(v.tolist()).most_common(1)[0][0]
    # campus buildings that vanished at 40 %: force the metatile with the most coverage
    forced = []
    for idx in campus_idx:
        if (mid == idx).any():
            continue
        cov = (b == idx).sum(-1)
        if cov.max() == 0:
            continue
        j, i = np.unravel_index(np.argmax(cov), cov.shape)
        cls[j, i] = C['ROOF']; mid[j, i] = idx
        forced.append(osm_ids[idx])
    # forest = tree metatiles whose 8 neighbours are all tree (interior); others are forest-edge TREE (also solid)
    t = cls == C['TREE']
    tp = np.pad(t, 1, constant_values=True)
    interior = np.ones_like(t)
    for dj in (-1, 0, 1):
        for di in (-1, 0, 1):
            interior &= tp[1 + dj:1 + dj + MH, 1 + di:1 + di + MW]
    cls[interior] = C['FOREST']
    # a lone canopy cell (no tree on any side) is drawn as a single, solid tree
    t = np.isin(cls, [C['TREE'], C['FOREST']])
    tp = np.pad(t, 1, constant_values=False)
    nbrs = tp[2:, 1:-1].astype(int) + tp[:-2, 1:-1] + tp[1:-1, 2:] + tp[1:-1, :-2]
    cls[(cls == C['TREE']) & (nbrs == 0)] = C['LONETREE']
    return cls, mid, forced


def place_buildings(cls, mid, osm_ids, bprops, entrances, campus_idx, named_idx, edge):
    """Facade row on the south side of each building column; doors at OSM entrances or estimated."""
    MH, MW = cls.shape
    # south-up: (j-1) is the cell to the south
    for j in range(MH):
        for i in range(MW):
            if mid[j, i] and (j == 0 or mid[j - 1, i] != mid[j, i]):
                cls[j, i] = C['FACADE']
    walk = lambda c: c in WALKABLE
    cells_of = defaultdict(list)
    for j, i in zip(*np.nonzero(mid)):
        cells_of[int(mid[j, i])].append((j, i))
    doors = []                               # dict(idx, j, i, estimated, entrance ids, moved)
    by_bld = defaultdict(list)
    idx_of = {oid: k for k, oid in enumerate(osm_ids) if k}
    for e in entrances:
        bidx = idx_of.get(e['building_id'])
        if bidx is None or bidx not in cells_of:
            continue
        by_bld[bidx].append(e)
    for bidx, cells in cells_of.items():
        facades = [(j, i) for j, i in cells if cls[j, i] == C['FACADE']]
        if not facades:
            continue
        ents = by_bld.get(bidx, [])
        placed = {}
        for e in ents:
            ex, ey = e['xy']
            near = min(cells, key=lambda c: (c[1] * MT_M + 3 - ex) ** 2 + (c[0] * MT_M + 3 - ey) ** 2)
            moved = cls[near] != C['FACADE']
            if moved:
                near = min(facades, key=lambda c: (c[1] * MT_M + 3 - ex) ** 2 + (c[0] * MT_M + 3 - ey) ** 2)
            d = placed.setdefault(near, dict(idx=bidx, j=near[0], i=near[1], estimated=False, entrances=[],
                                             moved=True, main=False))
            d['entrances'].append(e['id'])
            d['moved'] = d['moved'] and bool(moved)
            d['main'] = d['main'] or e.get('entrance') == 'main'
        if not placed and (bidx in named_idx or bidx in campus_idx and bidx in named_idx):
            # estimated: facade cell nearest to a path/road (prefer ones with open ground to the south)
            pathy = np.isin(cls, [C['PATH'], C['ROAD'], C['BRIDGE']])
            pj, pi = np.nonzero(pathy)
            def score(c):
                j, i = c
                d2 = ((pj - j) ** 2 + (pi - i) ** 2).min() if len(pj) else 0
                south_ok = j > 0 and walk(cls[j - 1, i]) and not mid[j - 1, i]
                return (int(edge[j, i]), int(j > 0 and edge[j - 1, i]), 0 if south_ok else 1, d2, i)
            best = min(facades, key=score)
            placed[best] = dict(idx=bidx, j=best[0], i=best[1], estimated=True, entrances=[], moved=False, main=True)
        for d in placed.values():
            cls[d['j'], d['i']] = C['DOOR']
            # apron: make the cell south of the door walkable
            if d['j'] > 0 and not walk(cls[d['j'] - 1, d['i']]) and not mid[d['j'] - 1, d['i']]:
                cls[d['j'] - 1, d['i']] = C['PATH']
                d['apron'] = True
            doors.append(d)
    return doors


# ================================================================ tiles
def _t(rows):
    return np.array([[int(ch) for ch in r] for r in rows], np.uint8)


class TempKit:
    """Temporary flat 4-shade tiles (shade 0 = darkest). Drawn here, original."""
    name = 'temp'

    def __init__(self, lawn_variants=True, decor=True, edges=True):
        self.lawn_variants, self.decor, self.edges = lawn_variants, decor, edges
        self.LAWN = [_t(['22222222', '22222222', '22212222', '22121222', '22222222', '22222222', '22222212', '22222122']),
                     _t(['22222222', '21222222', '12122222', '22222222', '22222222', '22222122', '22221212', '22222222'])]
        self.LAWN_FLAT = np.full((8, 8), 2, np.uint8)
        self.PAVED = _t(['33333333', '33333333', '33233333', '33333333', '33333333', '33333333', '33333323', '33333333'])
        self.ROAD = _t(['11111111', '11111111', '11110111', '11111111', '11111111', '11111111', '11111111', '01111111'])
        self.WATER = _t(['11111111', '11122111', '11211211', '11111111', '11111111', '11111112', '21111121', '12111111'])
        self.FOREST = [_t(['01100110', '11110111', '11110111', '01100110', '00011001', '10111101', '10111101', '00011000']),
                       _t(['00011000', '10111101', '10111101', '00011001', '01100110', '11110111', '11110111', '01100110'])]
        self.STEEP = _t(['11111111', '00000000', '22222222', '22222222', '11111111', '00000000', '22222222', '22222222'])

    # --- one 16x16 metatile from its class and a neighbour lookup nb(dj, di) -> (cls, building idx)
    def metatile(self, c, nb, me_idx, h, style=None):
        return PAL_RGB[self._metatile(c, nb, me_idx, h % 2)]

    def _metatile(self, c, nb, me_idx, parity):
        T = np.zeros((16, 16), np.uint8)
        def fill(t):
            T[:8, :8] = t; T[:8, 8:] = t; T[8:, :8] = t; T[8:, 8:] = t
        same = lambda dj, di, group: nb(dj, di)[0] in group
        def border(group, shade, sides='nesw', width=1):
            if not self.edges:
                return
            if 'n' in sides and not same(1, 0, group): T[:width, :] = shade
            if 's' in sides and not same(-1, 0, group): T[16 - width:, :] = shade
            if 'w' in sides and not same(0, -1, group): T[:, :width] = shade
            if 'e' in sides and not same(0, 1, group): T[:, 16 - width:] = shade
        lawn = self.LAWN[parity] if self.lawn_variants else self.LAWN[0]
        if not self.decor:
            lawn = self.LAWN_FLAT
        if c == C['LAWN']:
            fill(lawn)
        elif c == C['PAVED']:
            fill(self.PAVED if self.decor else np.full((8, 8), 3, np.uint8))
        elif c == C['PATH']:
            fill(np.full((8, 8), 3, np.uint8))
            border({C['PATH'], C['BRIDGE'], C['DOOR'], C['ROAD'], C['PAVED']}, 1)
        elif c == C['ROAD']:
            fill(self.ROAD if self.decor else np.full((8, 8), 1, np.uint8))
        elif c == C['WATER']:
            fill(self.WATER if self.decor else np.full((8, 8), 1, np.uint8))
            border({C['WATER'], C['BRIDGE']}, 3)
        elif c == C['FOREST']:
            fill(self.FOREST[0])
        elif c in (C['TREE'], C['LONETREE']):
            T[:, :] = 2
            yy, xx = np.mgrid[0:16, 0:16]
            d = (yy - 6.5) ** 2 + (xx - 7.5) ** 2
            T[d < 42] = 1
            T[(d < 42) & (d > 26) & (yy > 6)] = 0
            T[(d < 12) & (yy < 6) & (xx < 8)] = 2 if self.decor else 1
            T[12:15, 7:9] = 0
        elif c == C['STEEP']:
            fill(self.STEEP)
        elif c == C['BRIDGE']:
            T[:, :] = 3
            if self.decor:
                T[::4, :] = 2
            horiz = same(0, -1, {C['WATER']}) or same(0, 1, {C['WATER']})
            if same(0, -1, {C['WATER']}) or same(0, 1, {C['WATER']}) and not (same(1, 0, {C['WATER']})):
                pass
            # rails on the sides that face water
            if same(0, -1, {C['WATER']}): T[:, 0] = 0
            if same(0, 1, {C['WATER']}): T[:, 15] = 0
            if same(1, 0, {C['WATER']}): T[0, :] = 0
            if same(-1, 0, {C['WATER']}): T[15, :] = 0
        elif c == C['ROOF']:
            T[:, :] = 1
            T[3::4, :] = 0 if self.decor else 1
            sameb = lambda dj, di: nb(dj, di)[1] == me_idx
            if not sameb(1, 0): T[0, :] = 0
            if not sameb(0, -1): T[:, 0] = 0
            if not sameb(0, 1): T[:, 15] = 0
        elif c in (C['FACADE'], C['DOOR']):
            T[:, :] = 3
            T[0, :] = 0; T[1, :] = 1; T[15, :] = 1
            sameb = lambda dj, di: nb(dj, di)[1] == me_idx
            if c == C['FACADE']:
                T[5:10, 2:6] = 0; T[5:10, 10:14] = 0
                if self.decor:
                    T[7, 2:6] = 1; T[7, 10:14] = 1
            else:
                T[4:16, 4:12] = 0
                if self.decor:
                    T[4, 4:12] = 1
            if not sameb(0, -1): T[:, 0] = 0
            if not sameb(0, 1): T[:, 15] = 0
        return T


class ArtKit:
    """Adapter for the Art agent's kit: art/tiles/kit.json + kit.png (see kit.json 'autotile' for the bitmask rules)."""
    name = 'art'
    ROOF_TUDOR_NAMES = {'Eliot Hall', 'Old Dorm Block', 'Anna Mann Hall', 'Eric V. Hauser Memorial Library'}

    def __init__(self, kit_dir, lawn_variants=True, decor=True, edges=True):
        self.lawn_variants, self.decor, self.edges = lawn_variants, decor, edges
        self.k = json.load(open(os.path.join(kit_dir, 'kit.json')))
        atlas = np.array(Image.open(os.path.join(kit_dir, self.k['atlas'])).convert('RGB'))
        cols = self.k['atlas_columns']
        self.tiles = [atlas[(t // cols) * 8:(t // cols) * 8 + 8, (t % cols) * 8:(t % cols) * 8 + 8]
                      for t in range(self.k['tile_count'])]
        self.cache = {}
        self.auto = self.k['autotile']

    def img(self, name):
        if name not in self.cache:
            q = [self.tiles[t] for t in self.k['metatiles'][name]]
            self.cache[name] = np.vstack([np.hstack(q[:2]), np.hstack(q[2:])])
        return self.cache[name]

    @staticmethod
    def canon(m):
        N, E, S, W_ = m & 1, m & 2, m & 4, m & 8
        out = m & 15
        if N and E and m & 16: out |= 16
        if S and E and m & 32: out |= 32
        if S and W_ and m & 64: out |= 64
        if N and W_ and m & 128: out |= 128
        return out

    def mask8(self, nb, group):
        bits = [(1, 0, 1), (0, 1, 2), (-1, 0, 4), (0, -1, 8), (1, 1, 16), (-1, 1, 32), (-1, -1, 64), (1, -1, 128)]
        m = 0
        for dj, di, bit in bits:
            if nb(dj, di)[0] in group:
                m |= bit
        if not self.edges:                          # simplified: no inner corners
            m = (m & 15) | 0xF0
        return self.canon(m)

    def blob(self, fam, m, h):
        if m == 255 and self.decor and self.lawn_variants and h % 5 == 0 and 'fill_variants' in self.auto[fam]:
            return self.auto[fam]['fill_variants'][1]
        return self.auto[fam]['masks'][str(m)]

    def metatile_name(self, c, nb, me_idx, h, roof_style):
        if c == C['LAWN']:
            if self.lawn_variants and self.decor:
                if h % 11 == 0: return 'lawn_b'
                if h % 13 == 0: return 'lawn_c'
            return 'lawn_a'
        if c == C['PAVED']:
            if roof_style == 'offcampus':          # yards and driveways: keep the neighbourhood calm
                return 'lawn_a'
            return 'paved_parking' if roof_style == 'parking' else 'paved_plaza'
        if c == C['PATH'] and getattr(self, 'pmask', None) is not None:
            return self.auto['path']['masks'][str(self.pmask)]
        if c == C['PATH']:
            grp = {C['PATH'], C['BRIDGE'], C['DOOR']}
            m = 0
            for dj, di, bit in [(1, 0, 1), (0, 1, 2), (-1, 0, 4), (0, -1, 8)]:
                if nb(dj, di)[0] in grp or (nb(dj, di)[0] == C['PAVED'] and nb(dj, di)[2] not in ('parking', 'offcampus')):
                    m |= bit
            return self.auto['path']['masks'][str(m)]
        if c == C['ROAD']:
            return self.blob('road', self.mask8(nb, {C['ROAD']}), h)
        if c == C['WATER']:
            return self.blob('water', self.mask8(nb, {C['WATER'], C['BRIDGE']}), h)
        if c in (C['FOREST'], C['TREE']):
            return self.blob('forest', self.mask8(nb, {C['FOREST'], C['TREE'], C['LONETREE']}), h)
        if c == C['LONETREE']:
            return 'tree'
        if c == C['STEEP']:
            l, r = nb(0, -1)[0] == C['STEEP'], nb(0, 1)[0] == C['STEEP']
            return 'ledge' if (l and r) or (not l and not r) else ('ledge_l' if not l else 'ledge_r')
        if c == C['BRIDGE']:
            ns_water = nb(1, 0)[0] == C['WATER'] or nb(-1, 0)[0] == C['WATER']
            ns_walk = nb(1, 0)[0] in (C['BRIDGE'], C['PATH']) or nb(-1, 0)[0] in (C['BRIDGE'], C['PATH'])
            return 'bridge_v' if ns_walk and not ns_water else 'bridge_h'
        same = lambda dj, di: nb(dj, di)[1] == me_idx
        style = 'tudor'
        if c == C['ROOF']:
            if style == 'tudor' and self.decor and nb(-1, 0)[0] == C['FACADE'] and same(-1, 0) and h % 4 == 1 \
                    and same(0, -1) and same(0, 1):
                return 'roof_tudor_gable'
            suf = ('n' if not same(1, 0) else '') + ('e' if not same(0, 1) else '') + ('w' if not same(0, -1) else '')
            return f'roof_{style}' + (f'_{suf}' if suf else '')
        if c == C['DOOR']:
            return 'facade_door'
        if c == C['FACADE']:
            fac = lambda dj, di: same(dj, di) and nb(dj, di)[0] in (C['FACADE'], C['DOOR'])
            if roof_style == 'modern':
                return 'facade_ribbon'
            l, r = fac(0, -1), fac(0, 1)
            if not l and r: return 'facade_window_l'
            if l and not r: return 'facade_window_r'
            return 'facade_window'
        return 'lawn_a'

    def metatile(self, c, nb, me_idx, h, roof_style):
        return self.img(self.metatile_name(c, nb, me_idx, h, roof_style))


def get_kit(which, **opts):
    kd = P('art', 'tiles')
    if which != 'temp' and os.path.exists(os.path.join(kd, 'kit.json')) and os.path.exists(os.path.join(kd, 'kit.png')):
        return ArtKit(kd, **opts)
    if which == 'art':
        raise SystemExit('art/tiles/kit.json not found')
    return TempKit(**opts)


GL = {}          # global south-up arrays: cls, mid, style (per-metatile roof style / 'parking')


def render(z, kit):
    """Render a zone; neighbours come from the global grid so edges match across scene borders."""
    cls, mid, style = GL['cls'], GL['mid'], GL['style']
    MH, MW = cls.shape
    h, w = z['h_mt'], z['w_mt']
    img = np.zeros((h * 16, w * 16, 3), np.uint8)
    names = []
    for r in range(h):
        row = []
        for c in range(w):
            j, i = z['y1'] // MT_M - 1 - r, z['x0'] // MT_M + c
            def nb(dj, di, j=j, i=i):
                jj, ii = j + dj, i + di
                if 0 <= jj < MH and 0 <= ii < MW:
                    return cls[jj, ii], mid[jj, ii], style[jj, ii]
                return cls[j, i], mid[j, i], style[j, i]
            hsh = (i * 73856093 ^ j * 19349663) & 0xFFFF
            kit.pmask = GL.get('pmask', {}).get((j, i))
            img[r * 16:r * 16 + 16, c * 16:c * 16 + 16] = kit.metatile(int(cls[j, i]), nb, mid[j, i], hsh, style[j, i])
            if isinstance(kit, ArtKit):
                row.append(kit.metatile_name(int(cls[j, i]), nb, mid[j, i], hsh, style[j, i]))
        names.append(row)
    return img, names


def unique_tiles(img):
    h, w = img.shape[:2]
    t = img.reshape(h // 8, 8, w // 8, 8, -1).transpose(0, 2, 1, 3, 4).reshape((h // 8) * (w // 8), -1)
    return len(np.unique(t, axis=0))


# ================================================================ text
def wrap(text, width=TEXT_COLS):
    lines, cur = [], ''
    for wd in text.split():
        while len(wd) > width:
            if cur:
                lines.append(cur); cur = ''
            lines.append(wd[:width - 1] + '-'); wd = wd[width - 1:]
        if not cur:
            cur = wd
        elif len(cur) + 1 + len(wd) <= width:
            cur += ' ' + wd
        else:
            lines.append(cur); cur = wd
    if cur:
        lines.append(cur)
    return lines


STOP = {'a', 'an', 'the', 'of', 'and', 'for', 'with', 'to', 'in', 'on', 'by', 'at', 'as', 'that', 'which',
        'where', 'near', 'from', 'its', 'their', 'or', 'plus', 'also', 'is', 'are', 'was', 'used', 'built', 'named'}


def dialogue(name, use):
    """Name in caps, then the CSV use text cut at clause boundaries to fit TEXT_LINES lines of TEXT_COLS chars."""
    title = wrap(ascii_text(name).upper())
    room = TEXT_LINES - len(title)
    if not use or room <= 0:
        return title[:TEXT_LINES]
    use = ascii_text(use).strip()
    # first sentence; a '. ' only ends a sentence after a word longer than 3 letters (keeps 'A.E. Doyle')
    m = re.search(r'(?<=[a-z0-9)]{4})\.\s', use)
    first = use[:m.start()] if m else use.rstrip('.')
    segs = [x.strip() for x in re.split(r'[:;]|\s\(|\)|,', first) if x.strip()]
    # drop a leading segment that just restates the building's name ("Eric V. Hauser Memorial Library: ...")
    if len(segs) > 1 and (ascii_text(name).lower() in segs[0].lower() or segs[0].lower() in ascii_text(name).lower()):
        segs = segs[1:]
    best = None
    for k in range(len(segs), 0, -1):              # longest run of whole clauses that fits
        text = ', '.join(segs[:k]) + '.'
        if len(wrap(text)) <= room:
            best = text
            break
    if best is None:                                # word-cut the first clause, ending on a content word
        words = segs[0].split()
        for k in range(len(words) - 1, 0, -1):
            ws = words[:k]
            while ws and ws[-1].lower().strip(',') in STOP:
                ws = ws[:-1]
            text = ' '.join(ws).rstrip(',') + '...'
            if ws and len(wrap(text)) <= room:
                best = text
                break
    best = best[0].upper() + best[1:] if best else None
    return title + (wrap(best) if best else [])


# ================================================================ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kit', default='auto', choices=['auto', 'temp', 'art'])
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    g, feats, surf, H = load()
    W = surf.shape[1]

    # ---- buildings, entrances, bridges
    osm_ids, bprops, polys = [None], [None], [None]
    campus_bldgs = []
    for f in feats:
        p = f['properties']
        if p['kind'] == 'building':
            poly = shape(f['geometry'])
            osm_ids.append(p['id']); bprops.append(p); polys.append(poly)
            if p.get('realm') == 'campus':
                campus_bldgs.append((p['id'], p.get('name'), poly))
    campus_idx = {k for k in range(1, len(osm_ids)) if bprops[k].get('realm') == 'campus'}
    named_idx = {k for k in campus_idx if bprops[k].get('name')}
    entrances = [dict(id=f['properties']['id'], building_id=f['properties'].get('building_id'),
                      entrance=f['properties'].get('entrance'), xy=tuple(f['geometry']['coordinates']))
                 for f in feats if f['properties']['kind'] == 'entrance']
    bridges = [shape(f['geometry']).buffer(1.5) for f in feats if f['properties'].get('bridge')]

    # ---- zones
    problems, split, outside = validate_zones(campus_bldgs)
    prop_lines = proposal_report(campus_bldgs)
    for l in prop_lines:
        log('proposal', l)
    if problems or split:
        for p_ in problems: log('ZONE PROBLEM', p_)
        for s_ in split: log('SPLIT', s_)
        sys.exit(1)
    log('campus buildings outside every zone:', outside)

    # ---- rasters (south-up, 1 m)
    bid_r = rasterize_ids([(polys[k], k) for k in range(1, len(polys)) if k not in campus_idx], H, W)
    bc = rasterize_ids([(polys[k], k) for k in campus_idx], H, W)       # campus wins overlaps
    bid_r = np.where(bc > 0, bc, bid_r)
    bridge_r = rasterize_ids([(b, 1) for b in bridges], H, W) if bridges else np.zeros_like(bid_r)

    lines = {'road': [], 'path': [], 'bridge': []}
    for f in feats:
        p = f['properties']
        if f['geometry']['type'] not in ('LineString', 'MultiLineString'):
            continue
        if p.get('bridge'):
            lines['bridge'].append(shape(f['geometry']))
        elif p['kind'] == 'road':
            lines['road'].append(shape(f['geometry']))
        elif p['kind'] in ('path', 'service_road'):
            lines['path'].append(shape(f['geometry']))
    cls, mid, forced = class_grid(surf, bid_r, bridge_r, osm_ids, campus_idx, named_idx, lines)
    edge = np.zeros(cls.shape, bool)          # metatiles on the outer ring of a zone (exits/border live there)
    for z in ZONES:
        j0, j1, i0, i1 = z['y0'] // MT_M, z['y1'] // MT_M, z['x0'] // MT_M, z['x1'] // MT_M
        edge[j0:j1, [i0, i1 - 1]] = True
        edge[[j0, j1 - 1], i0:i1] = True
    doors = place_buildings(cls, mid, osm_ids, bprops, entrances, campus_idx, named_idx, edge)
    log('forced (sub-40 %) campus buildings:', forced)
    MH, MW = cls.shape
    # per-metatile style: roof style for buildings, 'parking' for OSM parking lots
    park = [shape(f['geometry']) for f in feats if f['properties']['kind'] == 'parking']
    park_r = rasterize_ids([(pp, 1) for pp in park], H, W)[:MH * MT_M, :MW * MT_M]
    park_f = park_r.reshape(MH, MT_M, MW, MT_M).mean(axis=(1, 3))
    style = np.full((MH, MW), '', dtype=object)
    campus_poly = [shape(f['geometry']) for f in feats if f['properties']['kind'] == 'campus_boundary']
    camp_r = rasterize_ids([(pp, 1) for pp in campus_poly], H, W)[:MH * MT_M, :MW * MT_M]
    camp_f = camp_r.reshape(MH, MT_M, MW, MT_M).mean(axis=(1, 3))
    style[camp_f < 0.5] = 'offcampus'
    style[park_f >= 0.5] = 'parking'
    # The lidar 'paved' class also catches shade, roofs and hard-standing, and drawn as pale pavers it
    # blobbed into the footpaths. OSM has no plaza/pedestrian areas here, so paved ground is drawn only
    # where an OSM parking lot covers the metatile; the rest is lawn (still walkable).
    cls[(cls == C['PAVED']) & (park_f < 0.5)] = C['LAWN']
    inzone = np.zeros(cls.shape, bool)       # ground outside every scene connects nothing in the game
    for z in ZONES:
        inzone[z['y0'] // MT_M:z['y1'] // MT_M, z['x0'] // MT_M:z['x1'] // MT_M] = True
    carved = carve_pockets(cls, doors, osm_ids, bprops, inzone)
    for j, i in zip(*np.nonzero(mid)):
        p = bprops[mid[j, i]]
        # every roof uses the dark pitched kit roof (the light flat roof reads as a plaza at this scale);
        # houses and the 1912-30s Tudor Gothic core get windowed facades, other campus buildings ribbon facades
        house = p.get('realm') != 'campus' or 'House' in (p.get('name') or '')
        style[j, i] = 'tudor' if house or p.get('name') in ArtKit.ROOF_TUDOR_NAMES else 'modern'
    GL.update(cls=cls, mid=mid, style=style, pmask=path_masks(cls, style))

    # ---- per-zone grids (north-up)
    for z in ZONES:
        j0, j1, i0, i1 = z['y0'] // MT_M, z['y1'] // MT_M, z['x0'] // MT_M, z['x1'] // MT_M
        z['grid'] = cls[j0:j1, i0:i1][::-1].copy()
        z['bidx'] = mid[j0:j1, i0:i1][::-1].copy()
        z['log'], z['warnings'], z['notes'] = [], [], []

    def mt_local(z, j, i):            # global south-up metatile -> local (row, col) north-up
        return z['y1'] // MT_M - 1 - j, i - z['x0'] // MT_M

    def collision(z):
        g_ = z['grid']
        solid = np.isin(g_, list(SOLID)).astype(np.uint8)
        col = np.kron(solid, np.ones((2, 2), np.uint8))
        return col

    for z in ZONES:
        z['col'] = collision(z)

    # ---- exits (before signposts, so signposts never sit on an exit)
    build_exits()

    # ---- actors (signposts) and door records
    for z in ZONES:
        z['actors'], z['doors'] = [], []
    for d in doors:
        cx, cy = d['i'] * MT_M + 3, d['j'] * MT_M + 3
        z = zone_of_point(cx, cy)
        if not z:
            continue
        r, c = mt_local(z, d['j'], d['i'])
        p = bprops[d['idx']]
        z['doors'].append(dict(osm_id=osm_ids[d['idx']], name=p.get('name'), x=c * 2, y=r * 2, w=2, h=2,
                               door_estimated=d['estimated'], entrances=d['entrances'],
                               moved_to_facade=d['moved'] and not d['estimated'], apron=d.get('apron', False),
                               main=d['main']))
    venues = []
    for f in feats:
        p = f['properties']
        if (p['kind'] == 'venue' or (p['kind'] == 'green' and p.get('map_no'))) and p.get('name'):
            pt = shape(f['geometry']).representative_point() if f['geometry']['type'] != 'Point' else shape(f['geometry'])
            venues.append((p, pt))

    for z in ZONES:
        g_, col = z['grid'], z['col']
        occupied = set()
        # primary door per named building = main / first entrance / estimated
        per_b = defaultdict(list)
        for d in z['doors']:
            per_b[d['osm_id']].append(d)
        trig_cells = set()
        for t in z['triggers']:
            for yy in range(t['y'], t['y'] + t['h']):
                for xx in range(t['x'], t['x'] + t['w']):
                    trig_cells.add((yy // 2, xx // 2))
        # metatiles under an incoming exit's landing spot (the player's 16x8 box)
        land_cells = {(t['to_y'] // 2, xx // 2) for z2 in ZONES for t in z2['triggers'] if t['to'] == z['id']
                      for xx in (t['to_x'], t['to_x'] + 1)}
        open_mt = np.isin(g_, list(WALKABLE))

        def cuts_corridor(rr, cc):
            """A signpost is a solid 16x16 actor: refuse cells whose removal splits the walkable area
            (a sign on a one-metatile path or bridge would wall off everything beyond it)."""
            m = open_mt.copy()
            for o in occupied:
                m[o] = False
            n0 = ndimage.label(m)[1]
            m[rr, cc] = False
            return ndimage.label(m)[1] > n0

        def free_spot(r, c, prefer):
            for strict in (True, False):           # second pass allows the scene's edge metatiles
                for dr, dc in prefer:
                    rr, cc = r + dr, c + dc
                    lo = 1 if strict else 0
                    if lo <= rr < g_.shape[0] - lo and lo <= cc < g_.shape[1] - lo and g_[rr, cc] in WALKABLE \
                            and g_[rr, cc] != C['DOOR'] and (rr, cc) not in occupied and (rr, cc) not in trig_cells \
                            and (rr, cc) not in land_cells:
                        # never block a door apron (cell directly south of a door)
                        if rr > 0 and g_[rr - 1, cc] == C['DOOR']:
                            continue
                        if cuts_corridor(rr, cc):
                            continue
                        return rr, cc
            return None
        ring = [(1, -1), (1, 1), (1, -2), (1, 2), (0, -1), (0, 1), (2, -1), (2, 1), (2, 0), (0, -2), (0, 2), (2, -2), (2, 2),
                (1, -3), (1, 3), (-1, -1), (-1, 1), (3, 0), (3, -1), (3, 1)]
        for oid, ds in per_b.items():
            name = ds[0]['name']
            k = osm_ids.index(oid)
            if not name or k not in campus_idx:
                continue
            prim = sorted(ds, key=lambda d: (not d['main'], d['door_estimated'], -d['y'], d['x']))[0]
            r, c = prim['y'] // 2, prim['x'] // 2
            spot = free_spot(r, c, ring)
            extra = {}
            if not spot:
                # nowhere free beside the door: mount the sign on the facade next to the door (a solid cell),
                # so the door stays clear. Right beside the door it is read from the doorway (facing
                # east/west); further along, from open ground south of it.
                for dc in (-1, 1, -2, 2, -3, 3):
                    cc, rs = c + dc, r + 1
                    readable = abs(dc) == 1 or (rs < g_.shape[0] - 1 and g_[rs, cc] in WALKABLE
                                                and (rs, cc) not in trig_cells)
                    wall = (C['FACADE'], C['ROOF']) if abs(dc) == 1 else (C['FACADE'],)   # door in a notch
                    if 0 < cc < g_.shape[1] - 1 and g_[r, cc] in wall \
                            and z['bidx'][r, cc] == z['bidx'][r, c] and readable and (r, cc) not in occupied:
                        spot, extra = (r, cc), {'on_facade': True}
                        z['notes'].append(f'signpost of {name} ({oid}) mounted on the facade beside its door '
                                          f'(no free ground beside the door)')
                        break
            if not spot:
                # nowhere free beside the door (exits or solid all round): read the sign at the door itself
                spot, extra = (r, c), {'on_door': True}
                z['warnings'].append(f'signpost of {name} ({oid}) placed on its door: no free tile beside it')
            occupied.add(spot)
            z['actors'].append(dict(id=oid, name=name, sprite='signpost', x=spot[1] * 2, y=spot[0] * 2 + 1, **extra,
                                    dialogue=dialogue(name, bprops[k].get('use')),
                                    **({'door_estimated': True} if prim['door_estimated'] else {})))
        for p, pt in venues:
            if not (z['x0'] <= pt.x < z['x1'] and z['y0'] <= pt.y < z['y1']):
                continue
            r, c = mt_local(z, int(pt.y // MT_M), int(pt.x // MT_M))
            spot = free_spot(r, c, [(0, 0)] + ring)
            if spot:
                occupied.add(spot)
                z['actors'].append(dict(id=p['id'], name=p['name'], sprite='signpost', x=spot[1] * 2, y=spot[0] * 2 + 1,
                                        dialogue=dialogue(p['name'], p.get('use'))))
        if len(z['actors']) > MAX_ACTORS:
            z['warnings'].append(f"{len(z['actors'])} signposts > {MAX_ACTORS}; dropped the unnamed-use ones")
            z['actors'] = sorted(z['actors'], key=lambda a: len(a['dialogue']), reverse=True)[:MAX_ACTORS]
        # signposts are solid in GB Studio, keep the collision grid open under them (actor collision handles it)

    # ---- start
    zs = next(z for z in ZONES if z['id'] == START_LABEL)
    sx = int((MAIN_ENTRANCE_XY[0] - zs['x0']) // TILE_M)
    sy = int((zs['y1'] - MAIN_ENTRANCE_XY[1]) // TILE_M)
    sx, sy = nearest_walkable(zs, sx, sy)
    player = dict(sprite='art/sprites/player.png', scene=zs['id'], x=sx, y=sy, direction='up')

    # ---- render + budget
    for z in ZONES:
        budget(z, args.kit)

    write_all(player, prop_lines, forced, outside)


CARVED = []      # (osm id, name, metatiles turned from canopy into path)


def path_masks(cls, style):
    """4-neighbour mask (N1 E2 S4 W8) for every PATH metatile. Two path cells join only if an OSM way
    steps between them (PATH_LINKS), so parallel footways a metatile apart stay two lines instead of
    merging into a pale slab. Path cells with no way of their own (door aprons, carved gaps) join any
    path neighbour. Bridges, doors and plazas always join, as before."""
    MH, MW = cls.shape
    P_ = C['PATH']
    grp = lambda j, i: cls[j, i] in (P_, C['BRIDGE'], C['DOOR']) or \
        (cls[j, i] == C['PAVED'] and style[j, i] not in ('parking', 'offcampus'))
    dirs = [((1, 0), 1), ((0, 1), 2), ((-1, 0), 4), ((0, -1), 8)]
    inb = lambda j, i: 0 <= j < MH and 0 <= i < MW
    own = {}
    for j, i in zip(*np.nonzero(cls == P_)):
        own[(j, i)] = any(inb(j + dj, i + di) and grp(j + dj, i + di) and ((j, i), (j + dj, i + di)) in PATH_LINKS
                          for (dj, di), _ in dirs)
    out = {}
    for (j, i), linked in own.items():
        m = 0
        for (dj, di), bit in dirs:
            jj, ii = j + dj, i + di
            if not inb(jj, ii):
                continue
            if cls[jj, ii] == P_:
                if ((j, i), (jj, ii)) in PATH_LINKS or not linked or not own[(jj, ii)]:
                    m |= bit
            elif grp(jj, ii):
                m |= bit
        out[(int(j), int(i))] = m
    return out


def carve_pockets(cls, doors, osm_ids, bprops, inzone):
    """With canopy solid, some doors open onto a clearing walled in by trees. For each door whose
    walkable area is cut off from the main campus area (the component holding the main entrance),
    turn the fewest tree metatiles into path to join it up (0-1 BFS: open ground costs 0, canopy 1;
    water, buildings and steep ground are never crossed). Gameplay access, not survey data: every
    carve is listed in REPORT.md."""
    MH, MW = cls.shape
    tree = {C['TREE'], C['FOREST'], C['LONETREE']}
    s0 = (MAIN_ENTRANCE_XY[1] // MT_M, MAIN_ENTRANCE_XY[0] // MT_M)
    for d in sorted(doors, key=lambda d: (d['j'], d['i'])):
        lab, _ = ndimage.label(np.isin(cls, list(WALKABLE)) & inzone)
        main = lab[s0]
        if lab[d['j'], d['i']] == main or not main:
            continue
        pocket = lab == lab[d['j'], d['i']]
        dist = np.full(cls.shape, 1 << 30)
        prev = {}
        dq = deque()
        for j, i in zip(*np.nonzero(pocket)):
            dist[j, i] = 0
            dq.append((j, i))
        hit = None
        while dq:
            j, i = dq.popleft()
            if lab[j, i] == main:
                hit = (j, i)
                break
            for dj, di in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                jj, ii = j + dj, i + di
                if not (0 <= jj < MH and 0 <= ii < MW) or not inzone[jj, ii]:
                    continue
                c = int(cls[jj, ii])
                if c in WALKABLE:
                    w = 0
                elif c in tree:
                    w = 1
                else:
                    continue
                if dist[j, i] + w < dist[jj, ii]:
                    dist[jj, ii] = dist[j, i] + w
                    prev[(jj, ii)] = (j, i)
                    (dq.appendleft if w == 0 else dq.append)((jj, ii))
        if hit is None or dist[hit] > 12:
            continue
        cells, k = [], hit
        while k in prev:
            if cls[k] in tree:
                cells.append(k)
            k = prev[k]
        for k in cells:
            cls[k] = C['PATH']
        CARVED.append((osm_ids[d['idx']], bprops[d['idx']].get('name'), len(cells)))
    return CARVED


def nearest_walkable(z, x, y):
    col = z['col']
    best = None
    for yy in range(col.shape[0]):
        for xx in range(col.shape[1] - 1):
            if col[yy, xx] == 0 and col[yy, xx + 1] == 0:
                d = (xx - x) ** 2 + (yy - y) ** 2
                if best is None or d < best[0]:
                    best = (d, xx, yy)
    return best[1], best[2]


def build_exits():
    """Exit triggers on shared zone edges, in 8x8 tile units, merged into runs, mirrored on both sides."""
    for z in ZONES:
        z['triggers'] = []
        # No solid outer ring: GB Studio treats tiles beyond the scene as solid on every side
        # (tile_col_test_range_x/y in gbvm src/core/collision.c), and a ring cost the player half of
        # every edge metatile, which dropped exits wherever a path met the edge on a diagonal.
    T = lambda m: int(round(m / TILE_M))
    pairs = []
    for a in ZONES:
        for b in ZONES:
            if a is b:
                continue
            if a['y1'] == b['y0']:                       # b is north of a
                lo, hi = max(a['x0'], b['x0']), min(a['x1'], b['x1'])
                if hi > lo:
                    pairs.append((a, b, 'n', lo, hi))
            if a['x1'] == b['x0']:                       # b is east of a
                lo, hi = max(a['y0'], b['y0']), min(a['y1'], b['y1'])
                if hi > lo:
                    pairs.append((a, b, 'e', lo, hi))
    chunk = defaultdict(lambda: EXIT_CHUNK)
    for attempt in range(12):
        trig = defaultdict(list)
        for a, b, d, lo, hi in pairs:
            L = chunk[(a['id'], b['id'])]
            # walkable on both sides of the edge (row/col next to the edge, metatile class)
            cells = []
            for t in range(T(lo), T(hi)):
                if d == 'n':
                    ax, ay = t - T(a['x0']), 0
                    bx, by = t - T(b['x0']), b['h'] - 1
                    ok = a['grid'][0, ax // 2] in WALKABLE and b['grid'][-1, bx // 2] in WALKABLE
                else:
                    ay, ax = T(a['y1']) - 1 - t, a['w'] - 1
                    by, bx = T(b['y1']) - 1 - t, 0
                    ok = a['grid'][ay // 2, -1] in WALKABLE and b['grid'][by // 2, 0] in WALKABLE
                cells.append((t, ok))
            runs, cur = [], []
            for t, ok in cells:
                if ok:
                    cur.append(t)
                elif cur:
                    runs.append(cur); cur = []
            if cur:
                runs.append(cur)
            pieces = []
            for run in runs:
                if len(run) < 2:
                    continue
                n = math.ceil(len(run) / L)
                size = math.ceil(len(run) / n / 2) * 2
                for k in range(0, len(run), size):
                    pieces.append(run[k:k + size])
            for k, pc in enumerate(pieces):
                t0, t1 = pc[0], pc[-1] + 1
                mid = (t0 + t1) // 2
                if d == 'n':
                    # a: top row, going up into b's bottom
                    ta = dict(x=t0 - T(a['x0']), y=0, w=t1 - t0, h=1)
                    tb = dict(x=t0 - T(b['x0']), y=b['h'] - 1, w=t1 - t0, h=1)
                    da = dict(to=b['id'], to_x=0, to_y=0, direction='up',
                              _dest=('bottom', t0 - T(b['x0']), t1 - T(b['x0'])))
                    db = dict(to=a['id'], to_x=0, to_y=0, direction='down',
                              _dest=('top', t0 - T(a['x0']), t1 - T(a['x0'])))
                    names = (f"exit_n_{b['id']}_{k}", f"exit_s_{a['id']}_{k}")
                else:
                    # t counts tiles in world y (south-up); local rows are north-up
                    ya, yb = T(a['y1']) - t1, T(b['y1']) - t1
                    ta = dict(x=a['w'] - 1, y=ya, w=1, h=t1 - t0)
                    tb = dict(x=0, y=yb, w=1, h=t1 - t0)
                    da = dict(to=b['id'], to_x=0, to_y=0, direction='right', _dest=('left', yb, yb + t1 - t0))
                    db = dict(to=a['id'], to_x=0, to_y=0, direction='left', _dest=('right', ya, ya + t1 - t0))
                    names = (f"exit_e_{b['id']}_{k}", f"exit_w_{a['id']}_{k}")
                pair = f"{a['id']}|{b['id']}|{d}|{k}"
                trig[a['id']].append(dict(id=names[0], **ta, **da, _pair=pair))
                trig[b['id']].append(dict(id=names[1], **tb, **db, _pair=pair))
        over = [zid for zid, ts in trig.items() if len(ts) > MAX_TRIGGERS]
        if not over:
            break
        for a, b, d, lo, hi in pairs:
            if a['id'] in over or b['id'] in over:
                chunk[(a['id'], b['id'])] += 4
    zmap = {z['id']: z for z in ZONES}
    for z in ZONES:
        z['triggers'] = sorted(trig[z['id']], key=lambda t: t['id'])
        z['exit_chunk'] = max([chunk[k] for k in chunk if z['id'] in k] or [EXIT_CHUNK])
        for t in z['triggers']:
            z['col'][t['y']:t['y'] + t['h'], t['x']:t['x'] + t['w']] = 0
    dead = set()
    # landing spot: inside the matching run of the destination edge, clear of its exit trigger.
    # The player's collision box is row y, columns x and x+1 (tools/gbs/sprites.py BOUNDS_PLAYER).
    for z in ZONES:
        for t in z['triggers']:
            tz = zmap[t['to']]
            col = tz['col']
            edge, lo, hi = t.pop('_dest')
            tcells = {(yy, xx) for u in tz['triggers'] for yy in range(u['y'], u['y'] + u['h'])
                      for xx in range(u['x'], u['x'] + u['w'])}
            ok = lambda x, y: 0 <= y < col.shape[0] and 0 <= x < col.shape[1] - 1 and col[y, x] == 0 \
                and col[y, x + 1] == 0 and (y, x) not in tcells and (y, x + 1) not in tcells
            centre = (lo + hi) / 2
            spot = None
            if edge in ('bottom', 'top'):
                depths = [tz['h'] - 3, tz['h'] - 2] if edge == 'bottom' else [2, 1]
                xs = sorted(range(lo, hi - 1), key=lambda x: abs(x + 1 - centre))
                spot = next(((x, y) for y in depths for x in xs if ok(x, y)), None)
            else:
                xs = [2, 1] if edge == 'left' else [tz['w'] - 4, tz['w'] - 3]
                ys = sorted(range(lo, hi), key=lambda y: abs(y + 0.5 - centre))
                spot = next(((x, y) for x in xs for y in ys if ok(x, y)), None)
            if spot is None:
                # a path crossing the edge as a staircase leaves no clear spot inside the span: look a few
                # tiles around it, and accept a spot only if the player can walk from it back onto the
                # partner trigger (so nobody lands in a pocket)
                partner = next(u for u in tz['triggers'] if u['_pair'] == t['_pair'])
                H_, W_ = col.shape
                if edge in ('bottom', 'top'):
                    ys = range(H_ - 6, H_ - 1) if edge == 'bottom' else range(1, 6)
                    xs = range(lo - 4, hi + 3)
                else:
                    xs = range(1, 6) if edge == 'left' else range(W_ - 7, W_ - 2)
                    ys = range(lo - 4, hi + 4)
                cands = sorted(((x, y) for y in ys for x in xs if ok(x, y)),
                               key=lambda q: (q[0] + 1 - (partner['x'] + partner['w'] / 2)) ** 2
                               + (q[1] + 0.5 - (partner['y'] + partner['h'] / 2)) ** 2)
                spot = next((q for q in cands if walks_to(col, q, partner)), None)
            if spot is None:
                dead.add(t['_pair'])
                z['notes'].append(f"{t['id']}: no room for the 16 px player on the matching edge of {tz['id']} "
                                  f"(one-metatile pocket); exit dropped on both sides")
                continue
            t['to_x'], t['to_y'] = spot
    for z in ZONES:
        keep = []
        for t in z['triggers']:
            if t['_pair'] in dead:      # back to the cells' own collision
                base = np.kron(np.isin(z['grid'], list(SOLID)).astype(np.uint8), np.ones((2, 2), np.uint8))
                z['col'][t['y']:t['y'] + t['h'], t['x']:t['x'] + t['w']] = \
                    base[t['y']:t['y'] + t['h'], t['x']:t['x'] + t['w']]
            else:
                keep.append(t)
        for t in keep:
            t.pop('_pair')
        z['triggers'] = keep


def walks_to(col, start, trig, limit=600):
    """BFS over player positions (16x8 box: row y, columns x..x+1) from `start`: can the player reach
    a position whose box overlaps `trig`? Trigger cells count as open (the ring under them is 0)."""
    H_, W_ = col.shape
    hit = lambda x, y: trig['y'] <= y < trig['y'] + trig['h'] and trig['x'] <= x + 1 and x < trig['x'] + trig['w']
    seen, q = {start}, deque([start])
    while q and len(seen) < limit:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if (nx, ny) in seen or not (0 <= ny < H_ and 0 <= nx < W_ - 1) or col[ny, nx] or col[ny, nx + 1]:
                continue
            if hit(nx, ny):
                return True
            seen.add((nx, ny)); q.append((nx, ny))
    return False


def nearest_walkable_inner(z, x, y):
    col = z['col']
    best = None
    for yy in range(2, col.shape[0] - 2):
        for xx in range(2, col.shape[1] - 3):
            if col[yy, xx] == 0 and col[yy, xx + 1] == 0:
                d = (xx - x) ** 2 + (yy - y) ** 2
                if best is None or d < best[0]:
                    best = (d, xx, yy)
    return best[1], best[2]


STEPS = [('merge lawn variants', dict(lawn_variants=False)),
         ('drop decorative tiles', dict(lawn_variants=False, decor=False)),
         ('simplify autotile edges', dict(lawn_variants=False, decor=False, edges=False))]


def budget(z, which):
    kit = get_kit(which)
    img, names = render(z, kit)
    n = unique_tiles(img)
    z['log'].append(f'{kit.name} kit, full detail: {n} unique tiles')
    for label, o in STEPS:
        if n <= TILE_TARGET:
            break
        kit = get_kit(which, **o)
        img, names = render(z, kit)
        n = unique_tiles(img)
        z['log'].append(f'{label}: {n} unique tiles')
    if n > TILE_LIMIT:
        z['warnings'].append(f'{n} unique tiles > {TILE_LIMIT} after simplification; zone must shrink')
    z['img'], z['tiles'], z['kit'], z['names'] = img, n, kit.name, names


def write_all(player, prop_lines, forced, outside):
    zones_json = []
    scenes = []
    for z in ZONES:
        d = P('world', 'gb', z['id'])
        os.makedirs(d, exist_ok=True)
        Image.fromarray(z['img']).save(os.path.join(d, 'background.png'))
        # debug collision: background dimmed, solid tiles dark red, exits blue, doors yellow, signposts magenta
        rgb = z['img'].astype(np.float32)
        rgb = (rgb * 0.45 + 140).astype(np.uint8)
        col = z['col']
        for y in range(col.shape[0]):
            for x in range(col.shape[1]):
                if col[y, x]:
                    rgb[y * 8:y * 8 + 8, x * 8:x * 8 + 8] = (rgb[y * 8:y * 8 + 8, x * 8:x * 8 + 8] * 0.3 + [150, 20, 20]).astype(np.uint8)
        im = Image.fromarray(rgb); dr = ImageDraw.Draw(im)
        for t in z['triggers']:
            dr.rectangle([t['x'] * 8, t['y'] * 8, (t['x'] + t['w']) * 8 - 1, (t['y'] + t['h']) * 8 - 1], fill=(40, 90, 255))
        for dd in z['doors']:
            dr.rectangle([dd['x'] * 8, dd['y'] * 8, dd['x'] * 8 + 15, dd['y'] * 8 + 15],
                         outline=(255, 160, 0) if dd['door_estimated'] else (255, 230, 0), width=2)
        for a in z['actors']:
            dr.rectangle([a['x'] * 8, (a['y'] - 1) * 8, a['x'] * 8 + 15, a['y'] * 8 + 7], fill=(230, 0, 230))
        im.save(os.path.join(d, 'collision.png'))
        scene = dict(id=z['id'], name=z['name'], background=f"world/gb/{z['id']}/background.png",
                     width=z['w'], height=z['h'], collisions=z['col'].tolist(),
                     actors=[{k: v for k, v in a.items()} for a in z['actors']],
                     triggers=z['triggers'])
        meta = dict(world_box_m=[z['x0'], z['y0'], z['x1'], z['y1']], metres_per_tile=TILE_M,
                    tile_origin='top-left of the scene = world (x0, y1); tile x = (X - x0)/3, tile y = (y1 - Y)/3',
                    actor_position='x = left tile, y = bottom tile row of the 16x16 sprite',
                    unique_tiles=z['tiles'], kit=z['kit'], doors=z['doors'],
                    metatile_classes=[[CN[int(v)] for v in row] for row in z['grid']])
        json.dump(scene, open(os.path.join(d, 'spec.json'), 'w'), indent=1, default=int)
        json.dump(meta, open(os.path.join(d, 'meta.json'), 'w'), indent=1, default=int)
        scenes.append(scene)
        zones_json.append(dict(id=z['id'], name=z['name'], x0=z['x0'], x1=z['x1'], y0=z['y0'], y1=z['y1'],
                               width_px=z['w_px'], height_px=z['h_px'], width_tiles=z['w'], height_tiles=z['h'],
                               from_proposal_cells=z['cells']))
    json.dump(dict(units='world metres, x east, y north; edges are multiples of 6 m (one 16 px metatile)',
                   metres_per_metatile=MT_M, proposal=dict(columns=PROPOSAL_COLS, rows=PROPOSAL_ROWS),
                   proposal_check=prop_lines, zones=zones_json),
              open(P('world', 'gb', 'zones.json'), 'w'), indent=1)
    json.dump(dict(title='Reed World', player=player, scenes=scenes),
              open(P('world', 'gb', 'project_spec.json'), 'w'), default=int)
    overlay()
    report(player, forced, outside)


def overlay():
    with rasterio.open(P('world', 'lidar', 'surface_1m.tif')) as r:
        s = r.read(1)
    leg = json.load(open(P('world', 'lidar', 'surface_legend.json')))
    pal = np.zeros((256, 3), np.uint8)
    for c in leg['classes']:
        pal[c['value']] = [int(c['color'][i:i + 2], 16) for i in (1, 3, 5)]
    S = 2
    im = Image.fromarray((pal[s] * 0.6 + 100).astype(np.uint8)).resize((s.shape[1] * S, s.shape[0] * S), Image.NEAREST)
    d = ImageDraw.Draw(im)
    H = s.shape[0]
    for z in ZONES:
        d.rectangle([z['x0'] * S, (H - z['y1']) * S, z['x1'] * S, (H - z['y0']) * S], outline=(0, 0, 0), width=4)
        d.text((z['x0'] * S + 8, (H - z['y1']) * S + 8), f"{z['name']}\n{z['w_px']}x{z['h_px']} px, {z['tiles']} tiles",
               fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0), font_size=22)
    for x in PROPOSAL_COLS:
        d.line([(x * S, 0), (x * S, H * S)], fill=(255, 0, 255), width=1)
    for y in PROPOSAL_ROWS:
        d.line([(0, (H - y) * S), (s.shape[1] * S, (H - y) * S)], fill=(255, 0, 255), width=1)
    im.save(P('world', 'gb', 'zones_overlay.png'))


def report(player, forced, outside):
    L = ['# Reed World: GB scene export report', '',
         'Generated by `scripts/gb_export.py`. Limits: 192 unique 8×8 tiles (target ≤ 180), 20 actors, 30 triggers, '
         '2040 px per side, 1,048,320 px². Method: `docs/ZONES.md`.', '',
         f"Player start: `{player['scene']}` at tile ({player['x']}, {player['y']}), facing {player['direction']}.", '',
         '| Scene | Size px | Unique tiles | Actors | Triggers | Doors (est.) | Kit | Warnings |',
         '|---|---|---|---|---|---|---|---|']
    for z in ZONES:
        est = sum(d['door_estimated'] for d in z['doors'])
        L.append(f"| {z['name']} (`{z['id']}`) | {z['w_px']}×{z['h_px']} | {z['tiles']} | {len(z['actors'])} | "
                 f"{len(z['triggers'])} | {len(z['doors'])} ({est}) | {z['kit']} | {len(z['warnings'])} |")
    L += ['', '## Tile budget log', '']
    for z in ZONES:
        L.append(f"- **{z['id']}**: " + '; '.join(z['log']))
    L += ['', '## Doors', '', 'Real doors come from OSM `entrance` nodes. "moved" means the entrance is on a north, east or west '
          'wall, so the door piece sits on the nearest south-facade metatile instead. Estimated doors (no OSM entrance) '
          'face the nearest path and are flagged `door_estimated: true`.', '',
          '| Scene | Building | OSM id | Tile (x,y) | Source |', '|---|---|---|---|---|']
    for z in ZONES:
        for d in sorted(z['doors'], key=lambda d: (d['name'] or '', d['x'])):
            src = 'estimated' if d['door_estimated'] else ('OSM ' + ', '.join(d['entrances']) + (' (moved to facade)' if d['moved_to_facade'] else ''))
            L.append(f"| {z['id']} | {d['name'] or '(unnamed)'} | {d['osm_id']} | ({d['x']},{d['y']}) | {src} |")
    if CARVED:
        L += ['', '## Paths carved through canopy (estimated)', '',
              'Tree metatiles are solid, so these doors opened onto clearings walled in by trees. The fewest tree '
              'metatiles between each clearing and the main campus were redrawn as path (`carve_pockets`). '
              'These paths are a gameplay accommodation, not survey data.', '']
        for oid, name, n in CARVED:
            L.append(f'- {name or "(unnamed)"} ({oid}): {n} metatile(s)')
    L += ['', '## Notes', '']
    for z in ZONES:
        for n_ in z['notes']:
            L.append(f"- `{z['id']}`: {n_}")
    L += ['', '## Warnings', '']
    any_w = False
    for z in ZONES:
        for w in z['warnings']:
            L.append(f"- `{z['id']}`: {w}"); any_w = True
    if forced:
        L.append(f"- Campus buildings under 40 % of any metatile, forced onto their best-covered metatile: {', '.join(forced)}"); any_w = True
    for s_ in SLIVERS:
        L.append(f'- Zone edge: {s_}'); any_w = True
    if outside:
        L.append('- Campus buildings in no scene: ' + ', '.join(f'{n or "(unnamed)"} ({i})' for i, n in outside)); any_w = True
    if not any_w:
        L.append('None.')
    open(P('world', 'gb', 'REPORT.md'), 'w').write('\n'.join(L) + '\n')


if __name__ == '__main__':
    main()
