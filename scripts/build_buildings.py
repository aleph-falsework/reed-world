#!/usr/bin/env python3
"""Procedural Reed College buildings: footprints + fitted roofs + style kit -> reed_buildings_detailed.glb

    python3 world/buildings/kit/make_kit.py      # textures (only needed once; this script runs it if missing)
    python3 scripts/build_buildings.py           # writes world/buildings/out/

Inputs
    world/3d/buildings.json       footprints, base_z, ground (scripts/export_3d.py)
    world/3d/signs.json           door positions
    world/3d/heightmap*           terrain, to keep windows above ground
    world/buildings/roofs.json    fitted roofs (type, eave/ridge, pitch, ridges, facets, features, confidence)
    world/buildings/styles.csv    style family + materials per building (optional; a name-based fallback is used)
    world/buildings/kit/          textures + materials.json (make_kit.py)

Output (world/buildings/out/)
    reed_buildings_detailed.glb   one node per building, named B_<osm type>_<osm id> exactly as reed_buildings.glb;
                                  material slots named by material (M_Brick_Gothic, M_Trim_Stone, M_Roof_Slate, ...);
                                  textures referenced as external PNGs in textures/
    textures/                     the kit textures the glb references
    buildings_detailed.csv        mesh, osm_id, name, realm, map_no, style, roof, triangles
    build_report.json             per-building roof model, wings, triangle counts

Coordinates: world metres (x east, y north, z NAVD88) written as glTF (x, z, -y); Unreal's glTF importer turns
that into UE X = east*100, Y = -north*100, Z = z*100 cm (same as scripts/export_unreal.py).

Roof model ("wing envelope", a robust stand-in for a straight skeleton)
    The footprint is rotated onto the fitted ridge azimuth and decomposed into overlapping rectangles (wings) by a
    greedy maximal-rectangle cover. Each wing gets planes on its sides (pitched or vertical per roof type and the
    aspects of the lidar facets): gable = 2 pitched sides, hip = 4, shed = 1, deck = any, capped at the fitted ridge
    height. A wing's roof is the min of its planes; the building's roof is the max over wings (straight valleys on L,
    T and U footprints), clipped to the footprint plus the eave overhang. Gaps fall back to a flat roof at the eave.
"""
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image
from scipy import ndimage
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union
import trimesh

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from export_unreal import GLB, gl, fix_winding  # noqa: E402

SRC = os.path.join(ROOT, 'world', '3d')
BLD = os.path.join(ROOT, 'world', 'buildings')
KIT = os.path.join(BLD, 'kit')
OUT = os.path.join(BLD, 'out')

EPS = 1e-6


def log(*a):
    print(*a, flush=True)


# =============================================================== styles
# floor_h: storey height; sill: sill above floor; win: (w, h); bay: window spacing; margin: from corners
STYLES = {
    'collegiate_gothic_brick': dict(wall='Brick_Gothic', roof='Roof_Slate', trim='Trim_Stone', flat='Roof_Flat',
                                    floor_h=4.0, plinth=0.7, sill=0.9, win=(1.5, 2.3), bay=3.1, margin=1.4,
                                    window='gothic', entry='gothic', overhang=0.2, chimney='Brick_Gothic',
                                    bands=True, parapet=1.0),
    'arts_crafts_house': dict(wall='Siding_Wood_Dark', roof='Roof_Shingle', trim='Paint_Trim', flat='Roof_Flat',
                              floor_h=3.0, plinth=0.6, sill=0.8, win=(1.0, 1.5), bay=2.6, margin=0.9,
                              window='double_hung', entry='house', overhang=0.55, chimney='Brick_Common',
                              bands=False, parapet=0.6),
    'midcentury_residence': dict(wall='Brick_Common', roof='Roof_Shingle', trim='Concrete', flat='Roof_Flat',
                                 floor_h=3.0, plinth=0.3, sill=0.9, win=(1.6, 1.3), bay=3.2, margin=1.0,
                                 window='punched', entry='modern', overhang=0.6, chimney='Brick_Common',
                                 bands=False, parapet=0.5),
    'midcentury_academic': dict(wall='Brick_Common', roof='Roof_Flat', trim='Concrete', flat='Roof_Flat',
                                floor_h=3.8, plinth=0.3, sill=0.9, win=(2.0, 1.8), bay=3.3, margin=1.2,
                                window='punched', entry='modern', overhang=0.4, chimney='Brick_Common',
                                bands=False, parapet=0.7),
    'modern_residence': dict(wall='Siding_Wood', roof='Roof_Metal', trim='Metal_Dark', flat='Roof_Flat',
                             floor_h=3.0, plinth=0.3, sill=0.8, win=(1.2, 1.6), bay=2.7, margin=0.9,
                             window='modern', entry='modern', overhang=0.4, chimney='Metal_Dark',
                             bands=False, parapet=0.6),
    'contemporary_academic': dict(wall='Concrete', roof='Roof_Metal', trim='Metal_Dark', flat='Roof_Flat',
                                  floor_h=4.2, plinth=0.0, sill=0.8, win=(1.5, 2.2), bay=1.5, margin=1.0,
                                  window='ribbon', entry='modern', overhang=0.5, chimney='Metal_Dark',
                                  bands=False, parapet=0.9, curtain_ground=True),
    'small_house': dict(wall='Siding_Wood', roof='Roof_Shingle', trim='Paint_Trim', flat='Roof_Flat',
                        floor_h=3.0, plinth=0.6, sill=0.8, win=(1.0, 1.5), bay=3.0, margin=1.0,
                        window='double_hung', entry='house', overhang=0.45, chimney='Brick_Common',
                        bands=False, parapet=0.5),
    'utility': dict(wall='Concrete', roof='Roof_Metal', trim='Metal_Dark', flat='Roof_Flat',
                    floor_h=4.5, plinth=0.0, sill=1.6, win=(1.8, 1.0), bay=7.0, margin=2.0,
                    window='punched', entry='modern', overhang=0.3, chimney='Metal_Dark',
                    bands=False, parapet=0.5),
    'unknown': dict(wall='Stucco', roof='Roof_Shingle', trim='Paint_Trim', flat='Roof_Flat',
                    floor_h=3.2, plinth=0.4, sill=0.9, win=(1.2, 1.5), bay=3.0, margin=1.0,
                    window='punched', entry='house', overhang=0.4, chimney='Brick_Common',
                    bands=False, parapet=0.6),
}
FAMILIES = list(STYLES)
# Map free-text material names from styles.csv onto kit materials
MAT_WORDS = [
    (('clinker', 'gothic brick', 'tapestry'), 'Brick_Gothic'), (('brick',), 'Brick_Common'),
    (('limestone', 'cast stone', 'cast-stone', 'stone'), 'Trim_Stone'), (('slate',), 'Roof_Slate'),
    (('standing', 'seam', 'metal roof', 'metal'), 'Roof_Metal'), (('shingle', 'asphalt', 'composition'), 'Roof_Shingle'),
    (('membrane', 'gravel', 'tar', 'built-up', 'flat'), 'Roof_Flat'), (('stucco', 'render', 'plaster'), 'Stucco'),
    (('concrete',), 'Concrete'), (('curtain', 'glass'), 'Glass_Curtain'),
    (('siding', 'clapboard', 'shingle siding', 'wood', 'timber', 'cedar', 'board'), 'Siding_Wood'),
    (('paint', 'white trim'), 'Paint_Trim'), (('aluminium', 'aluminum', 'steel', 'dark metal'), 'Metal_Dark'),
]
WALL_OK = {'Brick_Gothic', 'Brick_Common', 'Stucco', 'Concrete', 'Siding_Wood', 'Siding_Wood_Dark', 'Glass_Curtain',
           'Trim_Stone'}
ROOF_OK = {'Roof_Slate', 'Roof_Shingle', 'Roof_Metal', 'Roof_Flat'}
TRIM_OK = {'Trim_Stone', 'Paint_Trim', 'Concrete', 'Metal_Dark'}
WINDOW_WORDS = [('gothic', 'gothic'), ('mullion', 'gothic'), ('leaded', 'gothic'), ('casement', 'modern'),
                ('double', 'double_hung'), ('hung', 'double_hung'), ('sash', 'double_hung'),
                ('ribbon', 'ribbon'), ('band', 'ribbon'), ('curtain', 'ribbon'), ('strip', 'ribbon'),
                ('punched', 'punched'), ('picture', 'modern'), ('awning', 'modern'), ('slider', 'modern')]


def word_mat(text, allowed):
    t = (text or '').lower()
    if not t:
        return None
    for words, mat in MAT_WORDS:
        if any(w in t for w in words) and mat in allowed:
            return mat
    return None


def fallback_family(b):
    """Used only when styles.csv is missing or has no row for the building."""
    n = (b.get('name') or '').lower()
    if not b['neighbourhood']:
        if any(k in n for k in ('eliot', 'old dorm', 'library', 'anna mann', 'chemistry', 'student union')):
            return 'collegiate_gothic_brick'
        if any(k in n for k in ('trillium', 'sitka', 'sequoia', 'aspen', 'bidwell', 'naito', 'sullivan', 'bragdon',
                                'griffin', 'mckinley', 'chittick', 'woodbridge')):
            return 'modern_residence'
        if any(k in n for k in ('performing', 'psychology', 'vollum', 'kaul', 'educational')):
            return 'contemporary_academic'
        if any(k in n for k in ('physics', 'biology', 'scholz', 'foster', 'macnaughton', 'greenwood', 'gray',
                                'studio', 'sports', 'gym')):
            return 'midcentury_academic'
        if any(k in n for k in ('house', 'prexy', 'greywood')):
            return 'arts_crafts_house'
        if any(k in n for k in ('plant', 'warehouse', 'reactor')):
            return 'utility'
        return 'midcentury_academic' if b['area_m2'] > 300 else 'small_house'
    return 'small_house'


def load_styles():
    p = os.path.join(BLD, 'styles.csv')
    rows = {}
    if os.path.isfile(p):
        with open(p, newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f):
                k = r.get('osm_id') or r.get('id')
                if k:
                    rows[k] = r
    return rows


def resolve_style(b, row):
    fam = None
    if row:
        fam = (row.get('style') or row.get('style_family') or row.get('family') or '').strip()
    if fam not in STYLES:
        fam = fallback_family(b)
    S = dict(STYLES[fam])
    S['family'] = fam
    S['source'] = 'styles.csv' if row else 'fallback'
    if row:
        w = word_mat(row.get('wall_material') or row.get('wall'), WALL_OK)
        r = word_mat(row.get('roof_material') or row.get('roof'), ROOF_OK)
        t = word_mat(row.get('trim_material') or row.get('trim'), TRIM_OK)
        if (row.get('trim') or '').strip().lower() in ('metal', 'aluminium', 'aluminum'):
            t = 'Metal_Dark'
        if w:
            if w == 'Brick_Common' and fam == 'collegiate_gothic_brick':
                w = 'Brick_Gothic'
            if w == 'Siding_Wood' and fam == 'arts_crafts_house':
                w = 'Siding_Wood_Dark'
            S['wall'] = w
        if r:
            S['roof'] = r
        if t:
            S['trim'] = t
        wt = (row.get('window_type') or row.get('window') or '').lower()
        for word, kind in WINDOW_WORDS:
            if word in wt:
                S['window'] = kind
                break
    # neighbourhood variety: rule-based rows (no researched source) get a stable per-building cladding pick
    rule = (not row) or (row.get('source') or '').strip() in ('rule', '')
    if b['neighbourhood'] and rule and fam in ('small_house', 'unknown', 'utility'):
        h = int(hashlib.md5(b['osm_id'].encode()).hexdigest()[:8], 16)
        S['wall'] = ['Siding_Wood', 'Siding_Wood', 'Siding_Wood_Dark', 'Stucco', 'Brick_Common'][h % 5]
        S['trim'] = 'Paint_Trim'
        if fam == 'utility':                      # mostly garages and sheds in the neighbourhood
            S['roof'] = 'Roof_Shingle'
    return S


# =============================================================== terrain
class Ground:
    def __init__(self):
        meta = json.load(open(os.path.join(SRC, 'heightmap.json')))
        raw = np.array(Image.open(os.path.join(SRC, meta['file']))).astype(np.float64)
        self.z = meta['min_m'] + raw / 65535.0 * (meta['max_m'] - meta['min_m'])
        self.step = float(meta['m_per_px'])
        self.x0 = float(meta['origin']['x'])
        self.ytop = float(meta['origin']['y'])

    def __call__(self, x, y):
        c = (np.asarray(x, dtype=np.float64) - self.x0) / self.step
        r = (self.ytop - np.asarray(y, dtype=np.float64)) / self.step
        return ndimage.map_coordinates(self.z, [np.ravel(r), np.ravel(c)], order=1, mode='nearest').reshape(np.shape(x))


# =============================================================== mesh accumulation
class Mesh:
    """Triangles per material in world metres (x east, y north, z up); UVs in metres until finalize()."""

    def __init__(self):
        self.d = {}

    def _m(self, mat):
        if mat not in self.d:
            self.d[mat] = dict(p=[], n=[], uv=[], f=[], k=0)
        return self.d[mat]

    def tris(self, mat, P, N, UV, F):
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        if len(P) == 0 or len(F) == 0:
            return
        m = self._m(mat)
        N = np.asarray(N, dtype=np.float64)
        if N.ndim == 1:
            N = np.tile(N, (len(P), 1))
        m['p'].append(P)
        m['n'].append(N)
        m['uv'].append(np.asarray(UV, dtype=np.float64).reshape(-1, 2))
        m['f'].append(np.asarray(F, dtype=np.int64).reshape(-1, 3) + m['k'])
        m['k'] += len(P)

    def poly(self, mat, P, N, UV):
        """Convex planar polygon, fan-triangulated."""
        n = len(P)
        if n < 3:
            return
        F = [(0, i, i + 1) for i in range(1, n - 1)]
        self.tris(mat, P, N, UV, F)

    def count(self):
        return sum(sum(len(f) for f in m['f']) for m in self.d.values())

    def finalize(self, tiles):
        """-> list of glTF prims; UV metres -> texture repeats using each material's tile size."""
        prims = []
        for mat in sorted(self.d):
            m = self.d[mat]
            if not m['f']:
                continue
            P = np.concatenate(m['p'])
            N = np.concatenate(m['n'])
            N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), EPS)
            UV = np.concatenate(m['uv'])
            F = np.concatenate(m['f'])
            # drop degenerate triangles
            a, b, c = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
            area = np.linalg.norm(np.cross(b - a, c - a), axis=1)
            F = F[area > 1e-7]
            if len(F) == 0:
                continue
            t = tiles.get(mat)
            if t:
                UV = UV / np.array([t[0], t[1]])
            pos, nrm = gl(P), gl(N)
            prims.append(dict(pos=pos, nrm=nrm, uv=UV, idx=fix_winding(pos, F, nrm), mat='M_' + mat))
        return prims


# ---- local frame on a wall: a = along the wall (to the right seen from outside), z = up, w = outward
class Frame:
    def __init__(self, ox, oy, tx, ty, nx, ny):
        self.o = np.array([ox, oy])
        self.t = np.array([tx, ty])
        self.n = np.array([nx, ny])

    def P(self, pts):
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
        xy = self.o + pts[:, :1] * self.t + pts[:, 2:3] * self.n
        return np.column_stack([xy, pts[:, 1]])

    def N(self, na, nz, nw):
        v = na * self.t + nw * self.n
        return np.array([v[0], v[1], nz])


def lquad(M, fr, mat, pts, nrm, uv=None):
    """Local quad/polygon (list of (a, z, w)); UV in metres picked from the two axes the face spans."""
    pts = np.asarray(pts, dtype=np.float64)
    if uv is None:
        na, nz, nw = np.abs(nrm)
        if nw >= na and nw >= nz:
            uv = np.column_stack([pts[:, 0], -pts[:, 1]])
        elif na >= nz:
            uv = np.column_stack([pts[:, 2] * np.sign(nrm[0] or 1), -pts[:, 1]])
        else:
            uv = np.column_stack([pts[:, 0], -pts[:, 2]])
    M.poly(mat, fr.P(pts), fr.N(*nrm), uv)


def lbox(M, fr, mat, a0, a1, z0, z1, w0, w1, faces='FLRTB'):
    """Axis box in the local frame. faces: F front(+w) L(-a) R(+a) T(+z) B(-z) K back(-w)."""
    if 'F' in faces:
        lquad(M, fr, mat, [(a0, z0, w1), (a1, z0, w1), (a1, z1, w1), (a0, z1, w1)], (0, 0, 1))
    if 'K' in faces:
        lquad(M, fr, mat, [(a0, z0, w0), (a1, z0, w0), (a1, z1, w0), (a0, z1, w0)], (0, 0, -1))
    if 'L' in faces:
        lquad(M, fr, mat, [(a0, z0, w0), (a0, z0, w1), (a0, z1, w1), (a0, z1, w0)], (-1, 0, 0))
    if 'R' in faces:
        lquad(M, fr, mat, [(a1, z0, w0), (a1, z0, w1), (a1, z1, w1), (a1, z1, w0)], (1, 0, 0))
    if 'T' in faces:
        lquad(M, fr, mat, [(a0, z1, w0), (a1, z1, w0), (a1, z1, w1), (a0, z1, w1)], (0, 1, 0))
    if 'B' in faces:
        lquad(M, fr, mat, [(a0, z0, w0), (a1, z0, w0), (a1, z0, w1), (a0, z0, w1)], (0, -1, 0))


def lframe_ring(M, fr, mat, a0, a1, z0, z1, fw, depth, reveal=True):
    """Picture-frame surround: outer (a0..a1, z0..z1), opening inset by fw; front at w=depth, reveals back to 0."""
    ia0, ia1, iz0, iz1 = a0 + fw, a1 - fw, z0 + fw, z1 - fw
    d = depth
    for q in (((a0, z0), (a1, z0), (ia1, iz0), (ia0, iz0)), ((a1, z0), (a1, z1), (ia1, iz1), (ia1, iz0)),
              ((a1, z1), (a0, z1), (ia0, iz1), (ia1, iz1)), ((a0, z1), (a0, z0), (ia0, iz0), (ia0, iz1))):
        lquad(M, fr, mat, [(p[0], p[1], d) for p in q], (0, 0, 1))
    if reveal:
        lquad(M, fr, mat, [(ia0, iz0, 0.01), (ia1, iz0, 0.01), (ia1, iz0, d), (ia0, iz0, d)], (0, 1, 0))
        lquad(M, fr, mat, [(ia0, iz1, 0.01), (ia1, iz1, 0.01), (ia1, iz1, d), (ia0, iz1, d)], (0, -1, 0))
        lquad(M, fr, mat, [(ia0, iz0, 0.01), (ia0, iz1, 0.01), (ia0, iz1, d), (ia0, iz0, d)], (1, 0, 0))
        lquad(M, fr, mat, [(ia1, iz0, 0.01), (ia1, iz1, 0.01), (ia1, iz1, d), (ia1, iz0, d)], (-1, 0, 0))
    return ia0, ia1, iz0, iz1


# =============================================================== window + entry modules
def win_gothic(M, fr, a, z0, w, h, S, hero=True):
    """Tudor collegiate window: stone surround, stone mullion + transom, leaded glass, square drip-mould (label)."""
    st = S['trim']
    a0, a1 = a - w / 2, a + w / 2
    z1 = z0 + h
    ia0, ia1, iz0, iz1 = lframe_ring(M, fr, st, a0, a1, z0, z1, 0.16, 0.14)
    lquad(M, fr, 'Glass_Leaded', [(ia0, iz0, 0.01), (ia1, iz0, 0.01), (ia1, iz1, 0.01), (ia0, iz1, 0.01)], (0, 0, 1))
    lbox(M, fr, st, a - 0.06, a + 0.06, iz0, iz1, 0.01, 0.12, 'FLR')                    # mullion
    zt = iz0 + (iz1 - iz0) * 0.72
    lbox(M, fr, st, ia0, ia1, zt - 0.05, zt + 0.05, 0.01, 0.12, 'FTB')                  # transom
    if hero:
        # label mould: bar above the head with short returns, projecting 0.2
        lbox(M, fr, st, a0 - 0.14, a1 + 0.14, z1 + 0.04, z1 + 0.20, 0.0, 0.22, 'FLRTB')
        lbox(M, fr, st, a0 - 0.14, a0 - 0.02, z1 - 0.30, z1 + 0.04, 0.0, 0.18, 'FLRB')
        lbox(M, fr, st, a1 + 0.02, a1 + 0.14, z1 - 0.30, z1 + 0.04, 0.0, 0.18, 'FLRB')
    lbox(M, fr, st, a0 - 0.06, a1 + 0.06, z0 - 0.14, z0, 0.0, 0.2, 'FT')               # sill


def win_double_hung(M, fr, a, z0, w, h, S, hero=True):
    tr = S['trim'] if S['trim'] in ('Paint_Trim', 'Metal_Dark') else 'Paint_Trim'
    a0, a1, z1 = a - w / 2, a + w / 2, z0 + h
    if not hero:
        lquad(M, fr, 'Window_House', [(a0, z0, 0.02), (a1, z0, 0.02), (a1, z1, 0.02), (a0, z1, 0.02)], (0, 0, 1),
              uv=[(0, 1), (1, 1), (1, 0), (0, 0)])
        return
    ia0, ia1, iz0, iz1 = lframe_ring(M, fr, tr, a0, a1, z0, z1, 0.1, 0.06)
    lquad(M, fr, 'Glass', [(ia0, iz0, 0.01), (ia1, iz0, 0.01), (ia1, iz1, 0.01), (ia0, iz1, 0.01)], (0, 0, 1))
    zm = (iz0 + iz1) / 2
    lbox(M, fr, tr, ia0, ia1, zm - 0.03, zm + 0.03, 0.01, 0.05, 'FTB')                  # meeting rail
    lbox(M, fr, tr, a0 - 0.05, a1 + 0.05, z0 - 0.08, z0, 0.0, 0.1, 'FT')               # sill
    lbox(M, fr, tr, a0 - 0.05, a1 + 0.05, z1, z1 + 0.12, 0.0, 0.08, 'FT')              # head casing


def win_punched(M, fr, a, z0, w, h, S, hero=True):
    a0, a1, z1 = a - w / 2, a + w / 2, z0 + h
    if not hero:
        lquad(M, fr, 'Window_Dark', [(a0, z0, 0.02), (a1, z0, 0.02), (a1, z1, 0.02), (a0, z1, 0.02)], (0, 0, 1),
              uv=[(0, 1), (1, 1), (1, 0), (0, 0)])
        return
    ia0, ia1, iz0, iz1 = lframe_ring(M, fr, 'Metal_Dark', a0, a1, z0, z1, 0.06, 0.04)
    # deep reveal: the glass sits 0.15 behind the brick face (a sleeve of wall material)
    lquad(M, fr, 'Glass', [(ia0, iz0, 0.01), (ia1, iz0, 0.01), (ia1, iz1, 0.01), (ia0, iz1, 0.01)], (0, 0, 1))
    for k in (1, 2):
        am = a0 + w * k / 3
        lbox(M, fr, 'Metal_Dark', am - 0.025, am + 0.025, iz0, iz1, 0.01, 0.04, 'F')
    tr = S['trim'] if S['trim'] in ('Concrete', 'Trim_Stone') else 'Concrete'
    lbox(M, fr, tr, a0 - 0.08, a1 + 0.08, z0 - 0.12, z0, 0.0, 0.12, 'FT')              # cast sill


def win_modern(M, fr, a, z0, w, h, S, hero=True):
    a0, a1, z1 = a - w / 2, a + w / 2, z0 + h
    if not hero:
        lquad(M, fr, 'Window_Dark', [(a0, z0, 0.02), (a1, z0, 0.02), (a1, z1, 0.02), (a0, z1, 0.02)], (0, 0, 1),
              uv=[(0, 1), (1, 1), (1, 0), (0, 0)])
        return
    ia0, ia1, iz0, iz1 = lframe_ring(M, fr, 'Metal_Dark', a0, a1, z0, z1, 0.05, 0.08)
    lquad(M, fr, 'Glass', [(ia0, iz0, 0.01), (ia1, iz0, 0.01), (ia1, iz1, 0.01), (ia0, iz1, 0.01)], (0, 0, 1))
    lbox(M, fr, 'Metal_Dark', a - 0.02, a + 0.02, iz0, iz1, 0.01, 0.05, 'F')


WINDOWS = dict(gothic=win_gothic, double_hung=win_double_hung, punched=win_punched, modern=win_modern)


def ribbon(M, fr, a0, a1, z0, h, hero=True):
    """Continuous glazing band with mullions every 1.5 m and a projecting head/sill."""
    z1 = z0 + h
    lquad(M, fr, 'Glass_Curtain', [(a0, z0, 0.02), (a1, z0, 0.02), (a1, z1, 0.02), (a0, z1, 0.02)], (0, 0, 1),
          uv=np.array([(a0, -z0), (a1, -z0), (a1, -z1), (a0, -z1)]) * np.array([1, 1.5 / h * 1.5]))
    lbox(M, fr, 'Metal_Dark', a0, a1, z1, z1 + 0.18, 0.0, 0.16, 'FB' + ('LR' if hero else ''))
    lbox(M, fr, 'Metal_Dark', a0, a1, z0 - 0.1, z0, 0.0, 0.12, 'FT')


def entry_gothic(M, fr, a, zg, S):
    """Arched stone portal: four-centred (Tudor) arch in a projecting stone surround, oak doors, label, step."""
    st = S['trim']
    W, H, D = 3.2, 4.3, 0.45                 # surround
    ow, spring, apex = 1.9, 2.5, 3.25        # opening
    hw = ow / 2
    k = np.linspace(-1, 1, 11)
    arch = [(a + hw * t, zg + spring + (apex - spring) * (1 - abs(t) ** 1.7)) for t in k]
    opening = [(a - hw, zg)] + arch + [(a + hw, zg)]
    outer = Polygon([(a - W / 2, zg), (a + W / 2, zg), (a + W / 2, zg + H), (a - W / 2, zg + H)])
    face = outer.difference(Polygon(opening))
    for geom in getattr(face, 'geoms', [face]):
        v, f = trimesh.creation.triangulate_polygon(geom, engine='earcut')
        pts = np.column_stack([v[:, 0], v[:, 1], np.full(len(v), D)])
        M.tris(st, fr.P(pts), fr.N(0, 0, 1), np.column_stack([v[:, 0], -v[:, 1]]), f)
    # sides and top of the surround
    lbox(M, fr, st, a - W / 2, a + W / 2, zg, zg + H, 0, D, 'LRT')
    # reveal around the arch (opening edge from the face back to the door)
    for (p, q) in zip(opening[:-1], opening[1:]):
        if p[1] == zg and q[1] == zg:
            continue
        e = np.array([q[0] - p[0], q[1] - p[1]])
        nrm = np.array([e[1], -e[0]]) / (np.linalg.norm(e) + EPS)    # points into the opening
        L = np.linalg.norm(e)
        M.poly(st, fr.P([(p[0], p[1], 0.05), (q[0], q[1], 0.05), (q[0], q[1], D), (p[0], p[1], D)]),
               fr.N(-nrm[0], -nrm[1], 0), [(0, 0), (L, 0), (L, -D), (0, -D)])
    # doors: timber leaf filling the arch, 5 cm proud of the wall
    v, f = trimesh.creation.triangulate_polygon(Polygon(opening), engine='earcut')
    pts = np.column_stack([v[:, 0], v[:, 1], np.full(len(v), 0.05)])
    uv = np.column_stack([(v[:, 0] - (a - hw)) / ow * 2 % 1.0001, 1 - (v[:, 1] - zg) / apex])
    uv[:, 0] = (v[:, 0] - (a - hw)) / ow
    M.tris('Door_Wood', fr.P(pts), fr.N(0, 0, 1), uv, f)
    # label mould over the portal + a stone step
    lbox(M, fr, st, a - W / 2 - 0.15, a + W / 2 + 0.15, zg + H, zg + H + 0.22, 0, D + 0.12, 'FLRTB')
    lbox(M, fr, st, a - W / 2 - 0.3, a + W / 2 + 0.3, zg - 0.4, zg + 0.16, 0, 1.3, 'FLRT')


def entry_modern(M, fr, a, zg, S):
    """Glass entry: storefront doors with sidelights in a dark frame, under a thin projecting canopy."""
    W, H = 3.2, 2.8
    ia0, ia1, iz0, iz1 = lframe_ring(M, fr, 'Metal_Dark', a - W / 2, a + W / 2, zg, zg + H, 0.08, 0.1)
    lquad(M, fr, 'Glass', [(ia0, iz0, 0.03), (ia1, iz0, 0.03), (ia1, iz1, 0.03), (ia0, iz1, 0.03)], (0, 0, 1))
    for am in (a - 0.9, a, a + 0.9):
        lbox(M, fr, 'Metal_Dark', am - 0.03, am + 0.03, iz0, iz1, 0.03, 0.08, 'FLR')
    lbox(M, fr, 'Metal_Dark', ia0, ia1, zg + 2.2, zg + 2.26, 0.03, 0.08, 'FB')
    lbox(M, fr, 'Concrete', a - W / 2 - 0.8, a + W / 2 + 0.8, zg + H + 0.35, zg + H + 0.6, 0, 2.0, 'FLRTB')
    lbox(M, fr, 'Concrete', a - W / 2 - 0.4, a + W / 2 + 0.4, zg - 0.4, zg + 0.08, 0, 1.6, 'FLRT')


def entry_house(M, fr, a, zg, S):
    """Panelled door with painted casing, a stoop and a small shed porch roof on brackets."""
    tr = 'Paint_Trim'
    zf = zg + 0.5
    lquad(M, fr, 'Door_Wood', [(a - 0.5, zf, 0.03), (a + 0.5, zf, 0.03), (a + 0.5, zf + 2.15, 0.03),
                               (a - 0.5, zf + 2.15, 0.03)], (0, 0, 1), uv=[(0, 1), (1, 1), (1, 0), (0, 0)])
    lframe_ring(M, fr, tr, a - 0.62, a + 0.62, zf, zf + 2.27, 0.12, 0.06, reveal=False)
    lbox(M, fr, 'Concrete', a - 1.0, a + 1.0, zg - 0.3, zf, 0, 1.4, 'FLRT')
    # porch roof: sloping slab
    roof = S['roof'] if S['roof'] != 'Roof_Flat' else 'Roof_Shingle'
    z0, z1 = zf + 2.55, zf + 2.95
    lquad(M, fr, roof, [(a - 1.1, z0, 1.3), (a + 1.1, z0, 1.3), (a + 1.1, z1, 0), (a - 1.1, z1, 0)],
          (0, 0.95, 0.3), uv=[(a - 1.1, 0), (a + 1.1, 0), (a + 1.1, -1.36), (a - 1.1, -1.36)])
    lquad(M, fr, tr, [(a - 1.1, z0 - 0.12, 1.3), (a + 1.1, z0 - 0.12, 1.3), (a + 1.1, z0, 1.3), (a - 1.1, z0, 1.3)],
          (0, 0, 1))
    lquad(M, fr, tr, [(a - 1.1, z0 - 0.12, 1.3), (a + 1.1, z0 - 0.12, 1.3), (a + 1.1, z1 - 0.12, 0),
                      (a - 1.1, z1 - 0.12, 0)], (0, -0.95, -0.3))


ENTRIES = dict(gothic=entry_gothic, modern=entry_modern, house=entry_house)


# =============================================================== roof engine
def plane_eval(pl, x, y):
    return pl[0] * x + pl[1] * y + pl[2]


def halfplane(a, b, c, bbox):
    """Polygon {a*x + b*y + c >= 0} within bbox (Sutherland-Hodgman on the box)."""
    x0, y0, x1, y1 = bbox
    ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    out = []
    for i in range(4):
        p, q = ring[i], ring[(i + 1) % 4]
        fp, fq = a * p[0] + b * p[1] + c, a * q[0] + b * q[1] + c
        if fp >= 0:
            out.append(p)
        if (fp >= 0) != (fq >= 0):
            t = fp / (fp - fq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    if len(out) < 3:
        return Polygon()
    return Polygon(out)


def ge_region(p1, p2, bbox, bias=0.0):
    """Region where plane p1 >= plane p2 + bias."""
    a, b, c = p1[0] - p2[0], p1[1] - p2[1], p1[2] - p2[2] - bias
    if abs(a) < 1e-9 and abs(b) < 1e-9:
        return box(*bbox) if c >= 0 else Polygon()
    return halfplane(a, b, c, bbox)


def polys(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == 'Polygon':
        return [g]
    if hasattr(g, 'geoms'):
        out = []
        for h in g.geoms:
            out += polys(h)
        return out
    return []


def clean(g, min_area=0.05):
    ps = [p for p in polys(g.buffer(0)) if p.area > min_area]
    return unary_union(ps) if ps else Polygon()


def rect_cover(P, theta):
    """Greedy maximal-rectangle cover of footprint P in a frame rotated by theta (radians, ridge along +x').
    Returns list of (cx, cy, ux, uy, L, W) in world coords (L = half length along u, W = half width)."""
    c = P.centroid
    R = affinity.rotate(P, -theta, origin=c, use_radians=True)
    for tol in (0.25, 0.6, 1.2):
        Rs = R.simplify(tol, preserve_topology=True)
        if Rs.geom_type != 'Polygon':
            Rs = R
        xs = sorted(set(round(v, 2) for v in np.asarray(Rs.exterior.coords)[:, 0]))
        ys = sorted(set(round(v, 2) for v in np.asarray(Rs.exterior.coords)[:, 1]))

        def merge(vals, g=0.7):
            out = [[vals[0]]]
            for v in vals[1:]:
                if v - out[-1][-1] < g:
                    out[-1].append(v)
                else:
                    out.append([v])
            res = [float(np.mean(o)) for o in out]
            res[0], res[-1] = vals[0], vals[-1]
            return res
        X, Y = merge(xs), merge(ys)
        if len(X) <= 22 and len(Y) <= 22:
            break
    nx, ny = len(X) - 1, len(Y) - 1
    if nx < 1 or ny < 1:
        return None
    occ = np.zeros((nx, ny), dtype=bool)
    cell_area = np.zeros((nx, ny))
    for i in range(nx):
        for j in range(ny):
            cb = box(X[i], Y[j], X[i + 1], Y[j + 1])
            cell_area[i, j] = cb.area
            occ[i, j] = R.intersection(cb).area > 0.55 * cb.area
    if not occ.any():
        return None
    S = np.zeros((nx + 1, ny + 1), dtype=int)
    S[1:, 1:] = np.cumsum(np.cumsum(occ, 0), 1)
    rects = []
    for i0 in range(nx):
        for i1 in range(i0 + 1, nx + 1):
            for j0 in range(ny):
                for j1 in range(j0 + 1, ny + 1):
                    n = S[i1, j1] - S[i0, j1] - S[i1, j0] + S[i0, j0]
                    if n == (i1 - i0) * (j1 - j0):
                        w, h = X[i1] - X[i0], Y[j1] - Y[j0]
                        if min(w, h) >= 1.8:
                            rects.append((i0, i1, j0, j1))
    covered = np.zeros_like(occ)
    total = (cell_area * occ).sum()
    chosen = []
    while len(chosen) < 8 and rects:
        best, gain = None, 0.0
        for r in rects:
            i0, i1, j0, j1 = r
            g = (cell_area[i0:i1, j0:j1] * ~covered[i0:i1, j0:j1]).sum()
            # prefer large rects among equal gains (full wings, overlapping is fine)
            g2 = g + 1e-3 * cell_area[i0:i1, j0:j1].sum()
            if g2 > gain:
                best, gain = r, g2
        if best is None or gain < max(5.0, 0.03 * total):
            break
        i0, i1, j0, j1 = best
        covered[i0:i1, j0:j1] = True
        chosen.append(best)
    cov = (cell_area * covered).sum() / max(total, EPS)
    if not chosen or cov < 0.7:
        return None
    out = []
    ct, st = math.cos(theta), math.sin(theta)
    for (i0, i1, j0, j1) in chosen:
        mx, my = (X[i0] + X[i1]) / 2, (Y[j0] + Y[j1]) / 2
        L, W = (X[i1] - X[i0]) / 2, (Y[j1] - Y[j0]) / 2
        # rotate centre back
        dx, dy = mx - c.x, my - c.y
        wx, wy = c.x + dx * ct - dy * st, c.y + dx * st + dy * ct
        out.append((wx, wy, ct, st, L, W))
    return out


def obb_wing(P):
    r = P.minimum_rotated_rectangle
    co = np.asarray(r.exterior.coords)[:4]
    e1, e2 = co[1] - co[0], co[2] - co[1]
    if np.linalg.norm(e1) < np.linalg.norm(e2):
        e1, e2 = e2, e1
    u = e1 / np.linalg.norm(e1)
    cx, cy = co.mean(0)
    return (cx, cy, u[0], u[1], np.linalg.norm(e1) / 2, np.linalg.norm(e2) / 2)


def compass_to_vec(az):
    r = math.radians(az)
    return math.sin(r), math.cos(r)


def vec_to_compass(x, y):
    return math.degrees(math.atan2(x, y)) % 360


class Roof:
    """Visible roof pieces [(polygon, plane, kind)] with kind 'pitched' / 'flat'; plus parapet info."""

    def __init__(self):
        self.pieces = []
        self.parapet = 0.0
        self.eave_z = None
        self.model = ''
        self.wings = []
        self.overhang = 0.0
        self.outline = None

    def z_at(self, x, y, default):
        from shapely import contains_xy
        x, y = np.atleast_1d(x), np.atleast_1d(y)
        z = np.full(len(x), np.nan)
        for poly, pl, _ in self.pieces:
            m = contains_xy(poly.buffer(0.02), x, y) & np.isnan(z)
            if m.any():
                z[m] = plane_eval(pl, x[m], y[m])
        z[np.isnan(z)] = default
        return z


def build_roof(b, r, S, P):
    """b: buildings.json record; r: roofs.json record (or None); S: style; P: CCW footprint polygon."""
    R = Roof()
    ground = (r or {}).get('ground_z', b['ground_mean_z'])
    top = b['top_z']
    rtype = (r or {}).get('type', 'flat' if b['roof']['type'] == 'flat' else 'gabled')
    conf = (r or {}).get('confidence', 'low')
    src = (r or {}).get('source', 'none')
    eave = (r or {}).get('eave_z', top)
    ridge = (r or {}).get('ridge_z', top)
    pitch = (r or {}).get('pitch_deg') or 30.0
    small_res = b['neighbourhood'] or (b['area_m2'] < 400 and S['family'] in ('small_house', 'arts_crafts_house'))
    low = conf == 'low' and src == 'lidar_2014' and not small_res
    if r is None or low or rtype == 'flat' or ridge - eave < 0.6 or pitch < 6:
        flat_z = eave if r else top
        if low and r:
            # lidar fit is unreliable (often trees): use the lower of the two height estimates
            flat_z = ground + min(r.get('eave_h_m', 99), b['height_m'])
        R.model = 'flat' + ('_lowconf' if low else '')
        R.eave_z = flat_z
        R.parapet = S['parapet']
        regions = []
        if r and not low:
            for f in r.get('facets', []):
                if f.get('kind') != 'flat' or f.get('n_px', 0) < 40 or not f.get('outline'):
                    continue
                zf = ground + f['h_med_m']
                if abs(zf - flat_z) < 1.2:
                    continue
                g = unary_union([Polygon(o[0], o[1:]) for o in [f['outline']]] if isinstance(f['outline'][0][0], list)
                                else [Polygon(f['outline'])])
                g = g.buffer(0).buffer(-1.0, join_style=2).buffer(1.0, join_style=2).intersection(P)
                g = g.simplify(0.5)
                g = clean(g, 15.0)
                if not g.is_empty:
                    regions.append((g, zf))
        rest = P
        for g, zf in sorted(regions, key=lambda t: t[1]):
            rest = rest.difference(g)
        if rest.area > 0.5:
            R.pieces.append((clean(rest), (0.0, 0.0, flat_z), 'flat'))
        for g, zf in regions:
            g2 = g
            for g3, z3 in regions:
                if z3 > zf:
                    g2 = g2.difference(g3)
            g2 = clean(g2, 5.0)
            if not g2.is_empty:
                R.pieces.append((g2, (0.0, 0.0, zf), 'flat'))
        R.outline = P
        return R

    # ---------------- pitched: wings
    slope = math.tan(math.radians(min(max(pitch, 12.0), 62.0)))
    ov = S['overhang']
    az = (r or {}).get('ridge_azimuth_deg')
    if az is None:
        w = obb_wing(P)
        theta = math.atan2(w[3], w[2])
    else:
        ux, uy = compass_to_vec(az)
        theta = math.atan2(uy, ux)
    wings = rect_cover(P, theta) or [obb_wing(P)]
    # pitched directions from the lidar facets (compass aspect of downslope)
    aspects = []
    if r and r.get('facets'):
        pf = [f for f in r['facets'] if f.get('kind') == 'pitched' and f.get('aspect_deg') is not None]
        tot = sum(f['n_px'] for f in pf) or 1
        aspects = [f['aspect_deg'] for f in pf if f['n_px'] >= 0.08 * tot]
    main = max(wings, key=lambda w: w[4] * w[5])
    # fit the slope so the main wing's ridge lands on the fitted ridge height (gable/hip/estimate)
    if rtype in ('gabled', 'hipped') or src == 'estimate':
        half = min(main[4], main[5])
        if half > 1.0:
            s2 = (ridge - eave) / half
            if 0.2 < s2 < 2.0:
                slope = s2
    cap = ridge + 0.02
    bb = P.buffer(30).bounds
    raw = []          # (poly, plane, wing index)
    for wi, (cx, cy, ux, uy, L, W) in enumerate(wings):
        # ridge along the longer side of the wing
        if W > L:
            ux, uy, L, W = -uy, ux, W, L
        vx, vy = -uy, ux
        sides = [((vx, vy), W), ((-vx, -vy), W), ((ux, uy), L), ((-ux, -uy), L)]   # outward normal, half extent
        pitched = []
        for k, ((nx, ny), d) in enumerate(sides):
            is_long = k < 2
            if rtype == 'hipped':
                p = True
            elif rtype == 'shed':
                asp = aspects[0] if aspects else vec_to_compass(vx, vy)
                p = is_long and abs(((vec_to_compass(nx, ny) - asp + 180) % 360) - 180) < 50
            elif rtype == 'complex' and aspects:
                p = any(abs(((vec_to_compass(nx, ny) - a + 180) % 360) - 180) < 35 for a in aspects)
                if not is_long and p and d < 3.0:
                    p = False              # narrow wing ends stay gabled
            else:                          # gabled, estimate, complex without facets
                p = is_long
            pitched.append(p)
        if not any(pitched):
            pitched[0] = pitched[1] = True
        R.wings.append(dict(c=[round(cx, 2), round(cy, 2)], u=[round(ux, 3), round(uy, 3)], L=round(L, 2),
                            W=round(W, 2), pitched=pitched))
        dom = Polygon([(cx + sx * (L + ov) * ux + sy * (W + ov) * vx, cy + sx * (L + ov) * uy + sy * (W + ov) * vy)
                       for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
        planes = []
        for ((nx, ny), d), p in zip(sides, pitched):
            if p:
                C = eave + slope * (d + nx * cx + ny * cy)
                planes.append((-slope * nx, -slope * ny, C))
        capp = (0.0, 0.0, cap)
        for i, pl in enumerate(planes):
            reg = dom
            for j, q in enumerate(planes):
                if i != j:
                    reg = reg.intersection(ge_region(q, pl, bb))
            reg = reg.intersection(ge_region(capp, pl, bb))
            if not reg.is_empty:
                raw.append((reg, pl, wi))
        creg = dom
        for q in planes:
            creg = creg.intersection(ge_region(q, capp, bb))
        if not creg.is_empty and creg.area > 0.01:
            raw.append((creg, capp, wi))
    outline = P.buffer(ov, join_style=2) if ov > 0 else P
    vis = []
    for k, (reg, pl, wi) in enumerate(raw):
        v = reg.intersection(outline)
        if v.is_empty:
            continue
        for (reg2, pl2, wj) in raw:
            if wj == wi:
                continue
            bias = -1e-4 if wj < wi else 1e-4
            occl = reg2.intersection(ge_region(pl2, pl, bb, bias))
            if not occl.is_empty:
                v = v.difference(occl)
                if v.is_empty:
                    break
        v = clean(v, 0.02)
        if not v.is_empty:
            kind = 'flat' if abs(pl[0]) + abs(pl[1]) < 0.02 else 'pitched'
            vis.append((v, pl, kind))
    covered = unary_union([v for v, _, _ in vis]) if vis else Polygon()
    gap = clean(P.difference(covered), 0.3)
    if not gap.is_empty:
        vis.append((gap, (0.0, 0.0, eave), 'flat'))
    R.pieces = vis
    R.eave_z = eave
    R.overhang = ov
    R.outline = outline
    R.model = f'{rtype}/{src}/{len(wings)} wings'
    return R


# =============================================================== building assembly
def ring_ccw(P):
    co = list(P.exterior.coords)[:-1]
    if not P.exterior.is_ccw:
        co = co[::-1]
    return co


def add_roof(M, R, S):
    for poly, pl, kind in R.pieces:
        a, b_, c = pl
        g = math.hypot(a, b_)
        mat = S['roof'] if kind == 'pitched' else S['flat']
        if mat == 'Roof_Flat' and kind == 'pitched':
            mat = 'Roof_Shingle'
        n = np.array([-a, -b_, 1.0])
        n /= np.linalg.norm(n)
        for p in polys(poly):
            try:
                v, f = trimesh.creation.triangulate_polygon(p, engine='earcut')
            except Exception:
                continue
            if len(f) == 0:
                continue
            z = plane_eval(pl, v[:, 0], v[:, 1])
            if g > 1e-6:
                d = np.array([a, b_]) / g                      # up-slope
                u = v @ np.array([d[1], -d[0]])
                vv = -(v @ d) * math.sqrt(1 + g * g)
            else:
                u, vv = v[:, 0], -v[:, 1]
            M.tris(mat, np.column_stack([v, z]), n, np.column_stack([u, vv]), f)
            # soffit under the overhang (seen from below) and fascia along the free roof edge
            if R.overhang > 0:
                fasc = S['trim'] if S['trim'] != 'Trim_Stone' else 'Trim_Stone'
                ext = p.exterior
                co = np.asarray(ext.coords)
                if not ext.is_ccw:
                    co = co[::-1]
                for i in range(len(co) - 1):
                    p0, p1 = co[i], co[i + 1]
                    mid = (p0 + p1) / 2
                    if R.outline.exterior.distance(Point(mid)) > 0.03:
                        continue
                    L = np.linalg.norm(p1 - p0)
                    if L < 0.05:
                        continue
                    on = np.array([p1[1] - p0[1], -(p1[0] - p0[0])]) / L
                    z0, z1 = plane_eval(pl, p0[0], p0[1]), plane_eval(pl, p1[0], p1[1])
                    M.poly(fasc, [(p0[0], p0[1], z0 - 0.22), (p1[0], p1[1], z1 - 0.22), (p1[0], p1[1], z1),
                                  (p0[0], p0[1], z0)], (on[0], on[1], 0), [(0, z0), (L, z1), (L, z1 - 0.22), (0, z0 - 0.22)])
    if R.overhang > 0:
        # soffits: overhang ring under each piece, 0.22 below the roof plane, facing down
        footprint = None
    return


def add_soffits(M, R, P, S):
    if R.overhang <= 0:
        return
    ring = R.outline.difference(P)
    for poly, pl, kind in R.pieces:
        s = poly.intersection(ring)
        for p in polys(s):
            if p.area < 0.01:
                continue
            try:
                v, f = trimesh.creation.triangulate_polygon(p, engine='earcut')
            except Exception:
                continue
            z = plane_eval(pl, v[:, 0], v[:, 1]) - 0.22
            M.tris(S['trim'] if S['trim'] in ('Paint_Trim', 'Concrete', 'Metal_Dark') else 'Paint_Trim',
                   np.column_stack([v, z]), np.array([0, 0, -1.0]), np.column_stack([v[:, 0], -v[:, 1]]), f)


def edge_profile(R, p0, p1, inward, default):
    """[(t0, t1, plane)] covering [0, 1] along the edge, from the visible roof pieces."""
    L = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    off = 0.04
    q0 = (p0[0] + inward[0] * off, p0[1] + inward[1] * off)
    q1 = (p1[0] + inward[0] * off, p1[1] + inward[1] * off)
    line = LineString([q0, q1])
    segs = []
    for poly, pl, _ in R.pieces:
        if not poly.intersects(line):
            continue
        g = poly.intersection(line)
        for s in ([g] if g.geom_type == 'LineString' else [h for h in getattr(g, 'geoms', []) if h.geom_type == 'LineString']):
            if s.length < 0.02:
                continue
            ts = sorted(line.project(Point(c)) / L for c in s.coords)
            segs.append((ts[0], ts[-1], pl))
    segs.sort(key=lambda s: s[0])
    out, t = [], 0.0
    for t0, t1, pl in segs:
        if t1 <= t + 1e-4:
            continue
        if t0 > t + 1e-3:
            out.append((t, t0, (0.0, 0.0, default)))
        out.append((max(t0, t), t1, pl))
        t = t1
    if t < 1 - 1e-3:
        out.append((t, 1.0, (0.0, 0.0, default)))
    return out


def add_walls(M, R, ring, base, S, extra_top=0.0):
    """Walls from base up to the roof along each edge (gable ends follow the roof), UV metres along the perimeter."""
    s0 = 0.0
    edges = []
    n = len(ring)
    for i in range(n):
        p0, p1 = ring[i], ring[(i + 1) % n]
        L = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        if L < 1e-3:
            continue
        tx, ty = (p1[0] - p0[0]) / L, (p1[1] - p0[1]) / L
        nx, ny = ty, -tx
        prof = edge_profile(R, p0, p1, (-nx, -ny), R.eave_z)
        for t0, t1, pl in prof:
            a = (p0[0] + tx * L * t0, p0[1] + ty * L * t0)
            b_ = (p0[0] + tx * L * t1, p0[1] + ty * L * t1)
            za, zb = plane_eval(pl, *a) + extra_top, plane_eval(pl, *b_) + extra_top
            if za - base < 0.05 and zb - base < 0.05:
                continue
            M.poly(S['wall'], [(a[0], a[1], base), (b_[0], b_[1], base), (b_[0], b_[1], zb), (a[0], a[1], za)],
                   (nx, ny, 0), [(s0 + L * t0, -base), (s0 + L * t1, -base), (s0 + L * t1, -zb), (s0 + L * t0, -za)])
        edges.append(dict(p0=p0, p1=p1, L=L, t=(tx, ty), n=(nx, ny), s0=s0, prof=prof))
        s0 += L
    return edges


def add_step_walls(M, R, S):
    """Vertical walls where the roof steps inside the footprint (raised flat levels, gable ends above lower wings)."""
    for poly, pl, kind in R.pieces:
        for p in polys(poly):
            co = np.asarray(p.exterior.coords)
            if not p.exterior.is_ccw:
                co = co[::-1]
            for i in range(len(co) - 1):
                a, c = co[i], co[i + 1]
                L = np.linalg.norm(c - a)
                if L < 0.1:
                    continue
                nrm = np.array([c[1] - a[1], -(c[0] - a[0])]) / L
                k = max(1, int(math.ceil(L / 2.0)))
                for j in range(k):
                    q0 = a + (c - a) * j / k
                    q1 = a + (c - a) * (j + 1) / k
                    mid = (q0 + q1) / 2 + nrm * 0.05
                    if not R.outline.buffer(-0.02).contains(Point(mid)):
                        continue
                    zo = R.z_at(np.array([mid[0]]), np.array([mid[1]]), R.eave_z)[0]
                    z0, z1 = plane_eval(pl, *q0), plane_eval(pl, *q1)
                    zm = (z0 + z1) / 2
                    if zm - zo < 0.15:
                        continue
                    ll = np.linalg.norm(q1 - q0)
                    M.poly(S['wall'], [(q0[0], q0[1], zo), (q1[0], q1[1], zo), (q1[0], q1[1], z1), (q0[0], q0[1], z0)],
                           (nrm[0], nrm[1], 0), [(0, -zo), (ll, -zo), (ll, -z1), (0, -z0)])


def add_parapet(M, R, P, S):
    """Flat roofs: inner face of the parapet and its coping (outer walls already rise by R.parapet)."""
    h = R.parapet
    th = 0.3
    inner = P.buffer(-th, join_style=2)
    if inner.is_empty:
        return
    cap = S['trim'] if S['trim'] in ('Trim_Stone', 'Concrete', 'Metal_Dark') else 'Concrete'
    for poly, pl, kind in R.pieces:
        zr = pl[2]
        # coping ring on top of the parapet over this piece
        ringp = poly.difference(inner)
        for p in polys(ringp):
            v, f = trimesh.creation.triangulate_polygon(p, engine='earcut')
            M.tris(cap, np.column_stack([v, np.full(len(v), zr + h)]), np.array([0, 0, 1.0]),
                   np.column_stack([v[:, 0], -v[:, 1]]), f)
        # inner face along the inset outline within this piece
        for ip in polys(inner):
            for ring_ in [ip.exterior] + list(ip.interiors):
                co = np.asarray(ring_.coords)
                line = LineString(co)
                seg = line.intersection(poly)
                for s in ([seg] if seg.geom_type == 'LineString' else [g for g in getattr(seg, 'geoms', []) if g.geom_type == 'LineString']):
                    sc = np.asarray(s.coords)
                    for i in range(len(sc) - 1):
                        a, c = sc[i], sc[i + 1]
                        L = np.linalg.norm(c - a)
                        if L < 0.05:
                            continue
                        mid = (a + c) / 2
                        nrm = np.array([c[1] - a[1], -(c[0] - a[0])]) / L
                        if inner.contains(Point(mid + nrm * 0.05)):     # face the roof (inward)
                            pass
                        else:
                            nrm = -nrm
                        M.poly(S['wall'], [(a[0], a[1], zr), (c[0], c[1], zr), (c[0], c[1], zr + h), (a[0], a[1], zr + h)],
                               (nrm[0], nrm[1], 0), [(0, -zr), (L, -zr), (L, -zr - h), (0, -zr - h)])
    # replace the roof surface by the inset (so it does not z-fight the coping)
    R.pieces = [(clean(poly.intersection(inner), 0.05), pl, kind) for poly, pl, kind in R.pieces]
    R.pieces = [(p, pl, k) for p, pl, k in R.pieces if not p.is_empty]


def add_band(M, P, z0, z1, d, mat, bottom=True):
    """Projecting horizontal band (plinth, string course, coping) around footprint P."""
    if z1 - z0 < 0.02:
        return
    outer = P.buffer(d, join_style=2)
    for p in polys(outer):
        co = np.asarray(p.exterior.coords)
        if not p.exterior.is_ccw:
            co = co[::-1]
        s = 0.0
        for i in range(len(co) - 1):
            a, c = co[i], co[i + 1]
            L = np.linalg.norm(c - a)
            if L < 0.02:
                continue
            nrm = np.array([c[1] - a[1], -(c[0] - a[0])]) / L
            M.poly(mat, [(a[0], a[1], z0), (c[0], c[1], z0), (c[0], c[1], z1), (a[0], a[1], z1)], (nrm[0], nrm[1], 0),
                   [(s, -z0), (s + L, -z0), (s + L, -z1), (s, -z1)])
            s += L
    ringp = outer.difference(P)
    for p in polys(ringp):
        try:
            v, f = trimesh.creation.triangulate_polygon(p, engine='earcut')
        except Exception:
            continue
        M.tris(mat, np.column_stack([v, np.full(len(v), z1)]), np.array([0, 0, 1.0]), np.column_stack([v[:, 0], -v[:, 1]]), f)
        if bottom:
            M.tris(mat, np.column_stack([v, np.full(len(v), z0)]), np.array([0, 0, -1.0]),
                   np.column_stack([v[:, 0], -v[:, 1]]), f)


def wall_top_min(edge, a0, a1, extra=0.0):
    """Lowest wall top over the span [a0, a1] (metres along the edge)."""
    L = edge['L']
    p0 = edge['p0']
    tx, ty = edge['t']
    zs = []
    for t0, t1, pl in edge['prof']:
        lo, hi = max(t0 * L, a0), min(t1 * L, a1)
        if hi < lo:
            continue
        for s in (lo, hi):
            zs.append(plane_eval(pl, p0[0] + tx * s, p0[1] + ty * s))
    return (min(zs) + extra) if zs else -1e9


def add_windows(M, edges, S, ground_fn, ground_ref, R, hero, door, flat_extra):
    fh, sill, (ww, wh), bay, margin = S['floor_h'], S['sill'], S['win'], S['bay'], S['margin']
    kind = S['window']
    floor0 = ground_ref + S['plinth']
    nwin = 0
    for e in edges:
        L = e['L']
        if L < ww + 2 * margin * 0.6:
            continue
        fr = Frame(e['p0'][0], e['p0'][1], e['t'][0], e['t'][1], e['n'][0], e['n'][1])
        usable = L - 2 * margin
        if kind == 'ribbon':
            n = 1
            pos = [L / 2]
        else:
            n = max(1, int(usable // bay) + 1) if usable >= ww else (1 if L >= ww + 1.0 else 0)
            span = (n - 1) * bay
            pos = [L / 2 - span / 2 + k * bay for k in range(n)]
        mids = np.array([[e['p0'][0] + e['t'][0] * a, e['p0'][1] + e['t'][1] * a] for a in pos]) if pos else np.zeros((0, 2))
        gz = ground_fn(mids[:, 0], mids[:, 1]) if len(mids) else []
        # at the door: no ground-floor windows within the entry module's width
        dskip = None
        if door is not None and door['edge'] is e:
            dskip = (door['a'] - door['w'] / 2 - 0.6, door['a'] + door['w'] / 2 + 0.6)
        f = 0
        while True:
            z0 = floor0 + f * fh + sill
            if kind == 'ribbon':
                a0, a1 = margin, L - margin
                if a1 - a0 < 2.0:
                    break
                top = wall_top_min(e, a0, a1, 0.0)
                if z0 + wh > top - 0.35:
                    break
                g = float(np.max(ground_fn(np.array([e['p0'][0], e['p0'][0] + e['t'][0] * L]),
                                           np.array([e['p0'][1], e['p0'][1] + e['t'][1] * L]))))
                if f == 0 and S.get('curtain_ground'):
                    # storefront glazing on the ground floor
                    zg = max(g, ground_ref) + 0.1
                    segs = [(a0, a1)]
                    if dskip:
                        segs = [(a0, dskip[0]), (dskip[1], a1)]
                    for s0_, s1_ in segs:
                        if s1_ - s0_ > 1.5:
                            ribbon(M, fr, s0_, s1_, zg, fh - 1.0, hero)
                            nwin += 1
                elif z0 > g + 0.3:
                    segs = [(a0, a1)]
                    if dskip and f == 0:
                        segs = [(a0, dskip[0]), (dskip[1], a1)]
                    for s0_, s1_ in segs:
                        if s1_ - s0_ > 1.5:
                            ribbon(M, fr, s0_, s1_, z0, wh, hero)
                            nwin += 1
                f += 1
                continue
            top_any = False
            for a, g in zip(pos, gz):
                top = wall_top_min(e, a - ww / 2, a + ww / 2, 0.0)
                if z0 + wh > top - 0.3:
                    continue
                top_any = True
                if z0 < g + 0.25:
                    continue
                if f == 0 and dskip and dskip[0] < a < dskip[1]:
                    continue
                WINDOWS[kind](M, fr, a, z0, ww, wh, S, hero)
                nwin += 1
            if not top_any or f > 30:
                break
            f += 1
        # attic / gable window: where the wall rises well above the eave (gable end)
        if kind != 'ribbon' and R.parapet == 0:
            zmid = wall_top_min(e, L / 2 - 0.05, L / 2 + 0.05)
            zeave = R.eave_z
            if zmid - zeave > 2.6 and L > 4:
                aw, ah = min(ww, 1.1), min(wh, 1.3)
                z0 = zeave + 0.3
                if wall_top_min(e, L / 2 - aw / 2, L / 2 + aw / 2) > z0 + ah + 0.4:
                    WINDOWS[kind](M, fr, L / 2, z0, aw, ah, S, hero)
                    nwin += 1
    return nwin


def add_box(M, cx, cy, ux, uy, hw, hd, z0, z1, mat, top=True):
    vx, vy = -uy, ux
    co = [(cx + sx * hw * ux + sy * hd * vx, cy + sx * hw * uy + sy * hd * vy) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    s = 0
    for i in range(4):
        a, c = co[i], co[(i + 1) % 4]
        L = math.hypot(c[0] - a[0], c[1] - a[1])
        n = ((c[1] - a[1]) / L, -(c[0] - a[0]) / L)
        M.poly(mat, [(a[0], a[1], z0), (c[0], c[1], z0), (c[0], c[1], z1), (a[0], a[1], z1)], (n[0], n[1], 0),
               [(s, -z0), (s + L, -z0), (s + L, -z1), (s, -z1)])
        s += L
    if top:
        M.poly(mat, [(p[0], p[1], z1) for p in co], (0, 0, 1), [(p[0], -p[1]) for p in co])


def add_features(M, R, r, S, P, ground, u):
    """Chimneys, dormers, towers and rooftop plant from roofs.json features."""
    out = dict(chimney=0, dormer=0, tower=0, mechanical=0)
    if not r:
        return out
    ux, uy = u
    for f in r.get('features', []):
        x, y = f['x'], f['y']
        if not P.buffer(-0.3).contains(Point(x, y)):
            continue
        zr = R.z_at(np.array([x]), np.array([y]), R.eave_z)[0]
        ztop = ground + f['top_h_m']
        t = f['type']
        if t == 'chimney':
            s = float(np.clip(math.sqrt(f.get('area_m2', 1)) * 0.45, 0.6, 1.3))
            ztop = max(ztop, zr + 1.0)
            add_box(M, x, y, ux, uy, s / 2, s / 2 * 0.8, zr - 0.5, ztop, S['chimney'])
            add_box(M, x, y, ux, uy, s / 2 + 0.06, s / 2 * 0.8 + 0.06, ztop - 0.15, ztop + 0.05,
                    'Trim_Stone' if S['chimney'] == 'Brick_Gothic' else 'Concrete')
            out['chimney'] += 1
        elif t in ('mechanical', 'raised_block'):
            if R.parapet == 0:
                continue
            s = float(np.clip(math.sqrt(f.get('area_m2', 4)), 1.0, 4.5))
            h = float(np.clip(f.get('above_roof_m', 1.0), 0.8, 2.5))
            add_box(M, x, y, ux, uy, s / 2, s / 2 * 0.7, zr, zr + h, 'Metal_Dark')
            out['mechanical'] += 1
        elif t == 'tower':
            s = float(np.clip(math.sqrt(f.get('area_m2', 9)), 2.5, 6.0))
            if ztop < zr + 1.0:
                continue
            add_box(M, x, y, ux, uy, s / 2, s / 2, zr - 0.5, ztop, S['wall'], top=False)
            if S['family'] == 'collegiate_gothic_brick':
                add_box(M, x, y, ux, uy, s / 2 + 0.12, s / 2 + 0.12, ztop - 0.35, ztop, 'Trim_Stone')
            else:
                add_box(M, x, y, ux, uy, s / 2, s / 2, ztop - 0.05, ztop, S['flat'])
            out['tower'] += 1
        elif t == 'dormer':
            # find the roof piece under the dormer: build a small gabled dormer facing downslope
            pl = None
            for poly, p, kind in R.pieces:
                if kind == 'pitched' and poly.contains(Point(x, y)):
                    pl = p
            if pl is None:
                continue
            g = math.hypot(pl[0], pl[1])
            dx, dy = -pl[0] / g, -pl[1] / g                 # downslope (outward) direction
            w = float(np.clip(math.sqrt(f.get('area_m2', 4)) * 0.7, 1.5, 3.0))
            zt = min(ztop, zr + 2.8)
            ze = zt - w / 2 * 0.9
            z0 = zr
            if ze - z0 < 0.8:
                ze = z0 + 0.8
                zt = ze + w / 2 * 0.9
            fr = Frame(x, y, dy, -dx, dx, dy)               # a along the slope contour, w outward (downslope)
            # front wall (rect + gable) at w=0
            front = [(-w / 2, z0, 0), (w / 2, z0, 0), (w / 2, ze, 0), (0, zt, 0), (-w / 2, ze, 0)]
            dm = S['wall'] if not S['wall'].startswith('Brick') else (S['trim'] if S['family'] == 'collegiate_gothic_brick' else S['wall'])
            lquad(M, fr, dm, front, (0, 0, 1))
            # depth to where the cheeks meet the roof
            De = (ze - z0) / g
            Dt = (zt - z0) / g
            lquad(M, fr, dm, [(-w / 2, z0, 0), (-w / 2, ze, 0), (-w / 2, ze, -De)], (-1, 0, 0))
            lquad(M, fr, dm, [(w / 2, z0, 0), (w / 2, ze, -De), (w / 2, ze, 0)], (1, 0, 0))
            ov = 0.15
            for sgn in (-1, 1):
                pts = [(sgn * (w / 2 + ov), ze - 0.1, ov), (0, zt + 0.05, ov), (0, zt + 0.05, -Dt), (sgn * (w / 2 + ov), ze - 0.1, -De)]
                nn = np.array([sgn * (zt - ze), w / 2, 0.0])
                nn /= np.linalg.norm(nn)
                lquad(M, fr, S['roof'] if S['roof'] != 'Roof_Flat' else 'Roof_Shingle', pts, tuple(nn),
                      uv=[(p[2], -p[0] * sgn * 1.2) for p in pts])
            kind_w = S['window'] if S['window'] in WINDOWS else 'double_hung'
            WINDOWS[kind_w](M, fr, 0, z0 + 0.25, min(w * 0.55, 1.2), min(ze - z0 - 0.4, 1.4) if ze - z0 > 0.9 else 0.5, S, True)
            out['dormer'] += 1
    return out


def find_door(edges, sign, P):
    if not sign or not sign.get('door'):
        return None
    dx, dy = sign['door']
    best = None
    for e in edges:
        ls = LineString([e['p0'], e['p1']])
        d = ls.distance(Point(dx, dy))
        if best is None or d < best[0]:
            best = (d, e, ls.project(Point(dx, dy)))
    if best is None or best[0] > 6.0:
        return None
    d, e, a = best
    return dict(edge=e, a=float(np.clip(a, 2.0, e['L'] - 2.0)) if e['L'] > 4.5 else e['L'] / 2)


def build_building(b, r, S, sign, ground_fn):
    P = Polygon(b['ring']).buffer(0)
    if P.geom_type == 'MultiPolygon':
        P = max(P.geoms, key=lambda g: g.area)
    simplify = b['neighbourhood']
    if simplify:
        Ps = P.simplify(0.4, preserve_topology=True)
        if Ps.is_valid and Ps.area > 0.5 * P.area:
            P = Ps
    P = Polygon(ring_ccw(P))
    hero = (not b['neighbourhood']) and (b.get('name') is not None or P.area > 300)
    M = Mesh()
    R = build_roof(b, r, S, P)
    base = b['base_z']
    ground_ref = (r or {}).get('ground_z', b['ground_mean_z'])
    ring = ring_ccw(P)
    is_flat = R.parapet > 0
    extra = R.parapet if is_flat else 0.0
    edges = add_walls(M, R, ring, base, S, extra_top=extra)
    door = find_door(edges, sign, P)
    if door:
        door['w'] = 3.2
    nwin = add_windows(M, edges, S, ground_fn, ground_ref, R, hero, door, extra)
    if door:
        e = door['edge']
        fr = Frame(e['p0'][0], e['p0'][1], e['t'][0], e['t'][1], e['n'][0], e['n'][1])
        px, py = e['p0'][0] + e['t'][0] * door['a'], e['p0'][1] + e['t'][1] * door['a']
        zg = float(max(ground_fn(np.array([px]), np.array([py]))[0], base + 0.05))
        ENTRIES[S['entry']](M, fr, door['a'], zg, S)
    # stone trim (Gothic): plinth, string courses at each floor, coping at the eave
    if S['bands'] and hero:
        add_band(M, P, base - 0.1, ground_ref + S['plinth'], 0.06, S['trim'], bottom=False)
        zf = ground_ref + S['plinth'] + S['floor_h']
        while zf < R.eave_z - 1.0:
            add_band(M, P, zf - 0.12, zf + 0.12, 0.06, S['trim'])
            zf += S['floor_h']
        if not is_flat:
            add_band(M, P, R.eave_z - 0.45, R.eave_z - 0.05, 0.09, S['trim'])
    elif hero and S['family'] in ('midcentury_academic', 'contemporary_academic') and not is_flat:
        pass
    if is_flat:
        add_parapet(M, R, P, S)
    add_roof(M, R, S)
    add_soffits(M, R, P, S)
    add_step_walls(M, R, S)
    u = (1.0, 0.0)
    if R.wings:
        u = tuple(R.wings[0]['u'])
    elif r and r.get('ridge_azimuth_deg') is not None:
        u = compass_to_vec(r['ridge_azimuth_deg'])
    else:
        w = obb_wing(P)
        u = (w[2], w[3])
    feats = add_features(M, R, r, S, P, ground_ref, u)
    return M, R, dict(windows=nwin, door=bool(door), hero=hero, **feats)


# =============================================================== glb with textures
class KitGLB(GLB):
    def __init__(self):
        super().__init__(generator='scripts/build_buildings.py')
        self.tex_index = {}

    def _tex(self, uri):
        if uri in self.tex_index:
            return self.tex_index[uri]
        self.j.setdefault('images', []).append(dict(uri=uri, name=os.path.splitext(os.path.basename(uri))[0]))
        if 'samplers' not in self.j:
            self.j['samplers'] = [dict(magFilter=9729, minFilter=9987, wrapS=10497, wrapT=10497)]
        self.j.setdefault('textures', []).append(dict(source=len(self.j['images']) - 1, sampler=0))
        self.tex_index[uri] = len(self.j['textures']) - 1
        return self.tex_index[uri]

    def kit_material(self, slot, info, metal=0.0):
        if slot in self.mat_index:
            return
        pbr = dict(baseColorTexture=dict(index=self._tex('textures/' + info['base_color'])),
                   metallicRoughnessTexture=dict(index=self._tex('textures/' + info['orm'])),
                   metallicFactor=1.0, roughnessFactor=1.0)
        m = dict(name=slot, pbrMetallicRoughness=pbr, normalTexture=dict(index=self._tex('textures/' + info['normal'])))
        self.j['materials'].append(m)
        self.mat_index[slot] = len(self.j['materials']) - 1


# =============================================================== main
def main():
    argv = sys.argv[1:]
    only = None
    if '--only' in argv:
        only = set(argv[argv.index('--only') + 1].split(','))
    if not os.path.isfile(os.path.join(KIT, 'materials.json')):
        subprocess.check_call([sys.executable, os.path.join(KIT, 'make_kit.py')])
    mats = json.load(open(os.path.join(KIT, 'materials.json')))['materials']
    tiles = {k: v['tile_m'] for k, v in mats.items() if v.get('tile_m')}
    blds = json.load(open(os.path.join(SRC, 'buildings.json')))['buildings']
    roofs = {}
    rp = os.path.join(BLD, 'roofs.json')
    if os.path.isfile(rp):
        roofs = {r['osm_id']: r for r in json.load(open(rp))['roofs']}
    ready = os.path.isfile(os.path.join(BLD, 'ROOFS_READY.json'))
    styles = load_styles()
    signs = {s['osm_id']: s for s in json.load(open(os.path.join(SRC, 'signs.json')))['signs'] if s['kind'] == 'building'}
    log(f'{len(blds)} buildings, {len(roofs)} roofs (ROOFS_READY={ready}), {len(styles)} style rows')
    ground = Ground()
    os.makedirs(OUT, exist_ok=True)
    g = KitGLB()
    rows, report = [], {}
    tri_total = 0
    for b in blds:
        if only and b['osm_id'] not in only and (b.get('name') or '') not in only:
            continue
        otype, oid = b['osm_id'].split('/')
        name = f'B_{otype}_{oid}'
        r = roofs.get(b['osm_id'])
        S = resolve_style(b, styles.get(b['osm_id']))
        try:
            M, R, info = build_building(b, r, S, signs.get(b['osm_id']), ground)
        except Exception as ex:                       # never lose a building: fall back to a plain flat box
            log(f'  ! {name} {b.get("name")}: {type(ex).__name__}: {ex}; flat fallback')
            M, R, info = build_building(b, None, S, None, ground)
        prims = M.finalize(tiles)
        for p in prims:
            g.kit_material(p['mat'], mats[p['mat'][2:]])
        ntri = sum(len(p['idx']) for p in prims)
        tri_total += ntri
        realm = 'neighbourhood' if b['neighbourhood'] else 'campus'
        extras = dict(osm_id=b['osm_id'], name=b.get('name'), map_no=b.get('map_no'), realm=b['realm'],
                      neighbourhood=b['neighbourhood'], height_m=b['height_m'], base_z_m=b['base_z'],
                      top_z_m=b['top_z'], roof=R.model, style=S['family'], style_source=S['source'],
                      roof_confidence=(r or {}).get('confidence'), triangles=ntri)
        if g.mesh(name, prims, extras=extras):
            rows.append(dict(mesh=name, osm_id=b['osm_id'], name=b.get('name') or '', realm=realm,
                             map_no=b.get('map_no') or '', style=S['family'], roof=R.model, triangles=ntri))
        report[name] = dict(name=b.get('name'), style=S['family'], style_source=S['source'], roof=R.model,
                            wings=R.wings, triangles=ntri, **info)
    path = os.path.join(OUT, 'reed_buildings_detailed.glb' if not only else 'reed_buildings_subset.glb')
    sz = g.save(path)
    # textures next to the glb
    td = os.path.join(OUT, 'textures')
    os.makedirs(td, exist_ok=True)
    used = set(im['uri'] for im in g.j.get('images', []))
    for u in used:
        shutil.copy2(os.path.join(KIT, u), os.path.join(td, os.path.basename(u)))
    if not only:
        with open(os.path.join(OUT, 'buildings_detailed.csv'), 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
            w.writeheader()
            w.writerows(rows)
        json.dump(dict(roofs_ready=ready, style_rows=len(styles), triangles=tri_total, glb_bytes=sz,
                       buildings=report), open(os.path.join(OUT, 'build_report.json'), 'w'), indent=1)
    log(f'wrote {path} {sz / 1e6:.2f} MB, {len(rows)} buildings, {tri_total} triangles, {len(used)} textures')


if __name__ == '__main__':
    main()
