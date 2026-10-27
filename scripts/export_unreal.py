#!/usr/bin/env python3
"""Export the Reed College 3D world (world/3d/, written by scripts/export_3d.py) as an Unreal Engine import kit.

    python3 scripts/export_unreal.py            # reads world/3d/, writes world/unreal/

Coordinate mapping (used everywhere in the kit)
    world frame:  metres, x east, y north, z = elevation above sea level (NAVD88), right-handed
    Unreal:       centimetres, left-handed, Z up
                  UE.X = x * 100        (east)
                  UE.Y = -y * 100       (south)
                  UE.Z = z * 100        (elevation; no datum shift, so the campus sits at Z ~ 1600..6500 cm)
    glTF files:   metres, Y up, right-handed (the glTF spec), written as
                  gltf = (x, z, -y)     i.e. +X east, +Y up, -Z north (the three.js convention)
                  Unreal's glTF importer converts glTF (X, Y, Z) m -> UE (X, Z, Y) * 100 cm, which lands
                  exactly on the mapping above. Every mesh vertex is written in world position with
                  identity node transforms, so every imported mesh is placed at the UE origin.

UE yaw: rotation about +Z from +X toward +Y. A world heading theta (degrees counter-clockwise from east)
is UE yaw = -theta. (East = 0, south = 90, north = -90.)

Requires: numpy, scipy, shapely, pillow, trimesh + mapbox_earcut (triangulation).
"""
import csv
import json
import math
import os
import struct
import sys

import numpy as np
from PIL import Image
from scipy import ndimage
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import unary_union
import trimesh

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'world', '3d')
OUT = os.path.join(ROOT, 'world', 'unreal')
GEOJSON = os.path.join(ROOT, 'world', 'lowfi', 'reed_world.geojson')

TILE_GRID = (4, 3)            # terrain mesh tiles, columns x rows
LAKE_DEPTH_M = 1.5            # terrain carved under lakes so the flat water plane is never pierced
LAKE_RAMP_M = 4.0             # depth ramps from 0 at the shore to LAKE_DEPTH_M over this distance
STREAM_CARVE_M = 0.3          # stream bed lowered this far below the stream line z
WATER_LIFT_M = 0.03           # water surface above the lake z in water.json
NBHD_SIMPLIFY_M = 0.4         # neighbourhood footprint simplification tolerance
EDGE_WALL_H_M = 30.0          # invisible walls at the terrain edge, above the local ground


def log(*a):
    print(*a, flush=True)


def need(path):
    if not os.path.exists(path):
        sys.exit(f'missing {path}: run scripts/export_3d.py first (export_unreal.py reads world/3d/)')
    return path


# =============================================================== coordinates
def ue(x, y, z):
    """world metres -> Unreal centimetres."""
    return x * 100.0, -y * 100.0, z * 100.0


def ue_yaw(theta_deg):
    """World heading (deg CCW from east) -> UE yaw (deg)."""
    y = -theta_deg
    return (y + 180.0) % 360.0 - 180.0


def gl(xyz):
    """world (N,3) metres -> glTF (x, z, -y) metres."""
    a = np.asarray(xyz, dtype=np.float64)
    return np.stack([a[:, 0], a[:, 2], -a[:, 1]], axis=1)


# =============================================================== glb writer
class GLB:
    """Minimal glTF 2.0 binary writer: meshes with several primitives (= UE material slots), node extras,
    PBR materials and one optional embedded PNG texture."""

    def __init__(self, generator='scripts/export_unreal.py'):
        self.bin = bytearray()
        self.j = dict(asset=dict(version='2.0', generator=generator), scene=0, scenes=[dict(nodes=[])],
                      nodes=[], meshes=[], materials=[], accessors=[], bufferViews=[], buffers=[])
        self.mat_index = {}

    def _view(self, data, target=None):
        while len(self.bin) % 4:
            self.bin.append(0)
        off = len(self.bin)
        self.bin += data
        v = dict(buffer=0, byteOffset=off, byteLength=len(data))
        if target:
            v['target'] = target
        self.j['bufferViews'].append(v)
        return len(self.j['bufferViews']) - 1

    def _acc(self, arr, kind, target, minmax=False):
        if kind == 'SCALAR':
            arr = np.ascontiguousarray(arr.reshape(-1), dtype=np.uint32)
            ctype, count = 5125, arr.size
        else:
            arr = np.ascontiguousarray(arr, dtype=np.float32)
            ctype, count = 5126, arr.shape[0]
        a = dict(bufferView=self._view(arr.tobytes(), target), componentType=ctype, count=int(count), type=kind)
        if minmax:
            a['min'] = [float(v) for v in arr.min(axis=0)]
            a['max'] = [float(v) for v in arr.max(axis=0)]
        self.j['accessors'].append(a)
        return len(self.j['accessors']) - 1

    def material(self, name, rgb, rough=0.9, metal=0.0, texture_png=None, alpha=None):
        if name in self.mat_index:
            return self.mat_index[name]
        pbr = dict(baseColorFactor=[rgb[0], rgb[1], rgb[2], 1.0 if alpha is None else alpha],
                   metallicFactor=metal, roughnessFactor=rough)
        m = dict(name=name, pbrMetallicRoughness=pbr)
        if alpha is not None:
            m['alphaMode'] = 'BLEND'
        if texture_png is not None:
            iv = self._view(texture_png)
            self.j.setdefault('images', []).append(dict(bufferView=iv, mimeType='image/png', name=name + '_albedo'))
            self.j.setdefault('samplers', []).append(dict(magFilter=9729, minFilter=9987, wrapS=33071, wrapT=33071))
            self.j.setdefault('textures', []).append(dict(source=len(self.j['images']) - 1,
                                                          sampler=len(self.j['samplers']) - 1))
            pbr['baseColorTexture'] = dict(index=len(self.j['textures']) - 1)
            pbr['baseColorFactor'] = [1, 1, 1, 1]
        self.j['materials'].append(m)
        self.mat_index[name] = len(self.j['materials']) - 1
        return self.mat_index[name]

    def mesh(self, name, prims, extras=None):
        """prims: list of dict(pos (N,3) glTF m, nrm (N,3), idx (M,3), mat name, uv optional)."""
        P = []
        for p in prims:
            if len(p['idx']) == 0:
                continue
            attrs = dict(POSITION=self._acc(p['pos'], 'VEC3', 34962, minmax=True),
                         NORMAL=self._acc(p['nrm'], 'VEC3', 34962))
            if p.get('uv') is not None:
                attrs['TEXCOORD_0'] = self._acc(p['uv'], 'VEC2', 34962)
            P.append(dict(attributes=attrs, indices=self._acc(p['idx'], 'SCALAR', 34963),
                          material=self.mat_index[p['mat']], mode=4))
        if not P:
            return None
        self.j['meshes'].append(dict(name=name, primitives=P))
        node = dict(name=name, mesh=len(self.j['meshes']) - 1)
        if extras:
            node['extras'] = extras
            self.j['meshes'][-1]['extras'] = extras
        self.j['nodes'].append(node)
        self.j['scenes'][0]['nodes'].append(len(self.j['nodes']) - 1)
        return node

    def save(self, path):
        while len(self.bin) % 4:
            self.bin.append(0)
        self.j['buffers'] = [dict(byteLength=len(self.bin))]
        js = json.dumps(self.j, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        js += b' ' * ((4 - len(js) % 4) % 4)
        total = 12 + 8 + len(js) + 8 + len(self.bin)
        with open(path, 'wb') as f:
            f.write(struct.pack('<III', 0x46546C67, 2, total))
            f.write(struct.pack('<II', len(js), 0x4E4F534A) + js)
            f.write(struct.pack('<II', len(self.bin), 0x004E4942) + bytes(self.bin))
        return os.path.getsize(path)


def fix_winding(pos, idx, nrm):
    """Make every triangle counter-clockwise seen from the side its vertex normals point to (glTF front face)."""
    idx = np.asarray(idx, dtype=np.int64).reshape(-1, 3)
    if len(idx) == 0:
        return idx
    a, b, c = pos[idx[:, 0]], pos[idx[:, 1]], pos[idx[:, 2]]
    fn = np.cross(b - a, c - a)
    want = nrm[idx[:, 0]] + nrm[idx[:, 1]] + nrm[idx[:, 2]]
    flip = (fn * want).sum(axis=1) < 0
    idx[flip] = idx[flip][:, [0, 2, 1]]
    return idx


def triangulate(ring_xy):
    """2D ring (list of [x, y]) -> (verts (K,2), faces (F,3)) using earcut through trimesh."""
    poly = Polygon(ring_xy)
    if not poly.is_valid:
        poly = poly.buffer(0)
        if poly.geom_type == 'MultiPolygon':
            poly = max(poly.geoms, key=lambda g: g.area)
    if poly.is_empty or poly.area < 1e-6:
        return np.zeros((0, 2)), np.zeros((0, 3), dtype=np.int64)
    v, f = trimesh.creation.triangulate_polygon(poly, engine='earcut')
    return np.asarray(v, dtype=np.float64), np.asarray(f, dtype=np.int64)


# =============================================================== terrain
class Terrain:
    """The 2 m vertex-registered heightmap from world/3d, with lakes and streams carved."""

    def __init__(self, water):
        meta = json.load(open(need(os.path.join(SRC, 'heightmap.json'))))
        raw = np.array(Image.open(need(os.path.join(SRC, meta['file'])))).astype(np.float64)
        assert raw.shape == (meta['height'], meta['width']), raw.shape
        self.meta = meta
        self.step = float(meta['m_per_px'])
        self.x0 = float(meta['origin']['x'])
        self.ytop = float(meta['origin']['y'])
        self.z_raw = meta['min_m'] + raw / 65535.0 * (meta['max_m'] - meta['min_m'])
        self.h, self.w = self.z_raw.shape
        self.xs = self.x0 + np.arange(self.w) * self.step
        self.ys = self.ytop - np.arange(self.h) * self.step
        self.z = self.z_raw.copy()
        self._carve(water)

    def _carve(self, water):
        X, Y = np.meshgrid(self.xs, self.ys)
        pts = np.stack([X.ravel(), Y.ravel()], axis=1)
        from shapely import contains_xy, distance
        self.carved = np.zeros_like(self.z, dtype=bool)
        for lake in water['lakes']:
            poly = Polygon(lake['ring'], lake.get('holes') or [])
            inside = contains_xy(poly, pts[:, 0], pts[:, 1]).reshape(self.z.shape)
            if not inside.any():
                continue
            d = distance(poly.exterior, [Point(p) for p in pts[inside.ravel()]])
            depth = LAKE_DEPTH_M * np.clip(d / LAKE_RAMP_M, 0.15, 1.0)
            zi = self.z[inside]
            self.z[inside] = np.minimum(zi, lake['z'] - depth)
            self.carved |= inside
        for st in water['streams']:
            line3 = np.asarray(st['line'], dtype=np.float64)
            ls = LineString(line3[:, :2])
            half = max(st.get('width_m', 2.0) / 2.0, 0.8) + 0.5
            x0, y0, x1, y1 = ls.buffer(half).bounds
            cm = (self.xs >= x0) & (self.xs <= x1)
            rm = (self.ys >= y0) & (self.ys <= y1)
            if not cm.any() or not rm.any():
                continue
            seg = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(line3[:, :2], axis=0).T))])
            for r in np.nonzero(rm)[0]:
                for c in np.nonzero(cm)[0]:
                    p = Point(self.xs[c], self.ys[r])
                    if ls.distance(p) <= half:
                        zl = np.interp(ls.project(p), seg, line3[:, 2])
                        if self.z[r, c] > zl - STREAM_CARVE_M:
                            self.z[r, c] = zl - STREAM_CARVE_M
                            self.carved[r, c] = True

    def sample(self, x, y):
        """Bilinear elevation at world (x, y); outside the grid the edge values continue."""
        c = (np.asarray(x, dtype=np.float64) - self.x0) / self.step
        r = (self.ytop - np.asarray(y, dtype=np.float64)) / self.step
        return ndimage.map_coordinates(self.z, [np.ravel(r), np.ravel(c)], order=1, mode='nearest').reshape(np.shape(x))

    def normals(self):
        gy, gx = np.gradient(self.z, self.step)        # rows go south, so d/dy(north) = -gy
        n = np.stack([-gx, gy, np.ones_like(self.z)], axis=-1)   # world (east, north, up)
        return n / np.linalg.norm(n, axis=-1, keepdims=True)


# =============================================================== landscape heightmaps
def landscape(terrain, size, spacing_cm):
    """Resample into a UE-recommended square landscape (size x size vertices) at spacing_cm, campus centred.
    Returns the PNG array and the numbers to type into Landscape > Import."""
    S = spacing_cm / 100.0
    ext = (size - 1) * S
    cx = terrain.x0 + (terrain.w - 1) * terrain.step / 2
    cy = terrain.ytop - (terrain.h - 1) * terrain.step / 2
    x0, ytop = cx - ext / 2, cy + ext / 2
    xs = x0 + np.arange(size) * S
    ys = ytop - np.arange(size) * S
    X, Y = np.meshgrid(xs, ys)
    Z = terrain.sample(X, Y)                       # mode='nearest' extends the edges into the padding
    zmin, zmax = float(Z.min()), float(Z.max())
    zmid = round((zmin + zmax) / 2, 2)
    half_cm = (zmax - zmin) / 2 * 100 * 1.15        # 15 % margin
    zscale = max(5.0, math.ceil(half_cm * 128 / 32767 / 5) * 5)   # multiple of 5
    v = np.round(32768 + (Z - zmid) * 100 * 128 / zscale)
    assert v.min() >= 0 and v.max() <= 65535
    v = v.astype(np.uint16)
    # verify: decode exactly as UE does and compare
    dec = zmid * 100 + (v.astype(np.float64) - 32768) * zscale / 128
    err = float(np.abs(dec - Z * 100).max())
    # The Landscape tool's Location field is the CENTRE of the new landscape: the editor offsets the actor by
    # half the quad count (FLandscapeEditorDetailCustomization_NewLandscape), so the actor ends up at the corner.
    corner = dict(x=round(x0 * 100, 2), y=round(-ytop * 100, 2), z=round(zmid * 100, 2))
    centre = dict(x=round((x0 + ext / 2) * 100, 2), y=round(-(ytop - ext / 2) * 100, 2), z=round(zmid * 100, 2))
    comps = {1009: '63 quads/section, 2x2 sections/component, 8x8 components (or 1x1 sections, 16x16 components)',
             2017: '63 quads/section, 2x2 sections/component, 16x16 components'}[size]
    info = dict(file=f'heightmap_landscape_{size}.png', size=[size, size], spacing_cm=spacing_cm,
                ui_location_cm=centre, actor_location_cm=corner, rotation_deg=[0, 0, 0], scale=dict(x=spacing_cm, y=spacing_cm, z=zscale),
                component_layout=comps,
                covers_world_m=dict(x=[round(x0, 2), round(x0 + ext, 2)], y=[round(ytop - ext, 2), round(ytop, 2)]),
                campus_world_m=dict(x=[terrain.x0, float(terrain.xs[-1])], y=[float(terrain.ys[-1]), terrain.ytop]),
                z_formula='UE Z (cm) = Location.Z + (pixel - 32768) * Scale.Z / 128',
                z_range_cm=[round(zmin * 100, 1), round(zmax * 100, 1)],
                z_representable_cm=[round(zmid * 100 - 32768 * zscale / 128, 1),
                                    round(zmid * 100 + 32767 * zscale / 128, 1)],
                z_step_cm=zscale / 128, max_decode_error_cm=round(err, 3),
                padding='outside the campus the edge heights are extended (flat streaks); the playable campus is the centre')
    return v, info


# =============================================================== terrain mesh tiles
def terrain_glb(terrain, albedo_png, path):
    g = GLB()
    g.material('M_Ground', (1, 1, 1), rough=0.95, texture_png=albedo_png)
    g.material('M_EdgeWall', (0.8, 0.2, 0.2), rough=1.0)
    N = terrain.normals()
    ncol, nrow = TILE_GRID
    cs = np.linspace(0, terrain.w - 1, ncol + 1).round().astype(int)
    rs = np.linspace(0, terrain.h - 1, nrow + 1).round().astype(int)
    W_m, H_m = 1274.0, 811.0                        # surface_albedo.png covers x [0, 1274], y [0, 811]
    tiles = []
    for ti in range(nrow):
        for tj in range(ncol):
            r0, r1, c0, c1 = rs[ti], rs[ti + 1], cs[tj], cs[tj + 1]
            Z = terrain.z[r0:r1 + 1, c0:c1 + 1]
            X, Y = np.meshgrid(terrain.xs[c0:c1 + 1], terrain.ys[r0:r1 + 1])
            pos = gl(np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1))
            nrm = gl(N[r0:r1 + 1, c0:c1 + 1].reshape(-1, 3))
            uv = np.stack([X.ravel() / W_m, (H_m - Y.ravel()) / H_m], axis=1)
            h, w = Z.shape
            k = np.arange(h * w).reshape(h, w)
            a, b, c, d = k[:-1, :-1].ravel(), k[:-1, 1:].ravel(), k[1:, :-1].ravel(), k[1:, 1:].ravel()
            idx = np.concatenate([np.stack([a, c, b], 1), np.stack([b, c, d], 1)])
            idx = fix_winding(pos, idx, nrm)
            name = f'T_Terrain_r{ti}_c{tj}'
            g.mesh(name, [dict(pos=pos, nrm=nrm, uv=uv, idx=idx, mat='M_Ground')],
                   extras=dict(kind='terrain', tile=[ti, tj], rows=[int(r0), int(r1)], cols=[int(c0), int(c1)]))
            tiles.append(name)
    # invisible boundary walls (hidden in game by import_reed.py, collision kept)
    x0, x1 = terrain.x0, float(terrain.xs[-1])
    y0, y1 = float(terrain.ys[-1]), terrain.ytop
    zlo = float(terrain.z.min()) - 5
    for name, (ax, ay, bx, by) in dict(N=(x0, y1, x1, y1), S=(x0, y0, x1, y0),
                                       W=(x0, y0, x0, y1), E=(x1, y0, x1, y1)).items():
        zhi = float(max(terrain.sample(np.array([ax, bx]), np.array([ay, by])).max(), terrain.z.max())) + EDGE_WALL_H_M
        box = trimesh.creation.box(extents=[max(abs(bx - ax), 1.0), max(abs(by - ay), 1.0), zhi - zlo])
        box.apply_translation([(ax + bx) / 2, (ay + by) / 2, (zhi + zlo) / 2])
        pos = gl(box.vertices)
        nrm = gl(box.vertex_normals)
        g.mesh(f'T_EdgeWall_{name}', [dict(pos=pos, nrm=nrm, idx=fix_winding(pos, box.faces, nrm), mat='M_EdgeWall')],
               extras=dict(kind='edge_wall', hidden_in_game=True))
    return g.save(path), tiles


# =============================================================== buildings
def building_prims(b, simplify):
    ring = [tuple(p) for p in b['ring']]
    tz = list(b.get('ring_top_z') or [b['top_z']] * len(ring))
    if ring[0] == ring[-1]:
        ring, tz = ring[:-1], tz[:-1]
    roof = b['roof']
    flat = roof['type'] != 'gable'
    if simplify and flat and len(ring) > 4:
        s = Polygon(ring).simplify(NBHD_SIMPLIFY_M, preserve_topology=True)
        if s.is_valid and s.area > 0.5 * Polygon(ring).area:
            ring = list(s.exterior.coords)[:-1]
            tz = [b['top_z']] * len(ring)
    P = Polygon(ring)
    if P.exterior.is_ccw is False:
        ring, tz = ring[::-1], tz[::-1]
    base = b['base_z']
    # walls: one flat-shaded quad per edge, outward normal (dy, -dx) for a CCW ring
    wp, wn, wi = [], [], []
    n = len(ring)
    for i in range(n):
        (ax, ay), (bx, by) = ring[i], ring[(i + 1) % n]
        L = math.hypot(bx - ax, by - ay)
        if L < 1e-3:
            continue
        nx, ny = (by - ay) / L, -(bx - ax) / L
        ta, tb = (tz[i], tz[(i + 1) % n]) if not flat else (b['top_z'], b['top_z'])
        k = len(wp)
        wp += [(ax, ay, base), (bx, by, base), (bx, by, tb), (ax, ay, ta)]
        wn += [(nx, ny, 0)] * 4
        wi += [(k, k + 1, k + 2), (k, k + 2, k + 3)]
    walls = None
    if wp:
        pos, nrm = gl(wp), gl(wn)
        walls = dict(pos=pos, nrm=nrm, idx=fix_winding(pos, wi, nrm))
    # roof
    rp, rn, ri = [], [], []
    parts = roof.get('parts') if roof['type'] == 'gable' else None
    if parts:
        for part in parts:
            a_, b_, c_ = part['plane']
            v, f = triangulate(part['ring'])
            if len(f) == 0:
                continue
            z = a_ * v[:, 0] + b_ * v[:, 1] + c_
            nn = np.array([-a_, -b_, 1.0]) / math.sqrt(a_ * a_ + b_ * b_ + 1)
            k = len(rp)
            rp += [tuple(p) for p in np.column_stack([v, z])]
            rn += [tuple(nn)] * len(v)
            ri += [tuple(t + k) for t in f]
    else:
        v, f = triangulate(ring)
        rp = [(p[0], p[1], b['top_z']) for p in v]
        rn = [(0, 0, 1)] * len(v)
        ri = [tuple(t) for t in f]
    roofp = None
    if ri:
        pos, nrm = gl(rp), gl(rn)
        roofp = dict(pos=pos, nrm=nrm, idx=fix_winding(pos, ri, nrm))
    return walls, roofp


def buildings_glb(blds, path):
    g = GLB()
    g.material('M_Wall_Campus', (0.62, 0.52, 0.44), rough=0.85)
    g.material('M_Roof_Campus', (0.33, 0.30, 0.30), rough=0.8)
    g.material('M_Wall_Neighbourhood', (0.72, 0.70, 0.66), rough=0.9)
    g.material('M_Roof_Neighbourhood', (0.42, 0.40, 0.40), rough=0.85)
    rows = []
    for b in blds:
        otype, oid = b['osm_id'].split('/')
        name = f'B_{otype}_{oid}'
        realm = 'Neighbourhood' if b['neighbourhood'] else 'Campus'
        walls, roofp = building_prims(b, simplify=b['neighbourhood'])
        prims = []
        if walls:
            prims.append(dict(walls, mat='M_Wall_' + realm))
        if roofp:
            prims.append(dict(roofp, mat='M_Roof_' + realm))
        extras = dict(osm_id=b['osm_id'], name=b.get('name'), map_no=b.get('map_no'), realm=b['realm'],
                      neighbourhood=b['neighbourhood'], height_m=b['height_m'], height_source=b['height_source'],
                      base_z_m=b['base_z'], top_z_m=b['top_z'], roof=b['roof']['type'])
        if g.mesh(name, prims, extras=extras):
            rows.append(dict(mesh=name, osm_id=b['osm_id'], name=b.get('name') or '', realm=realm.lower(),
                             map_no=b.get('map_no') or ''))
    return g.save(path), rows


# =============================================================== trees, signs mesh, water
def tree_glb(path):
    """Two unit trees, base at the origin: total height 1 m, crown radius 1 m. Instances scale (r, r, h)."""
    g = GLB()
    g.material('M_Bark', (0.30, 0.22, 0.15), rough=0.95)
    g.material('M_Leaves', (0.22, 0.36, 0.18), rough=0.9)

    def prim(m, mat):
        pos, nrm = gl(m.vertices), gl(m.vertex_normals)
        return dict(pos=pos, nrm=nrm, idx=fix_winding(pos, m.faces, nrm), mat=mat)

    trunk = trimesh.creation.cylinder(radius=0.08, height=0.4, sections=8)
    trunk.apply_translation([0, 0, 0.2])
    crown = trimesh.creation.icosphere(subdivisions=2, radius=1.0)
    crown.apply_scale([1.0, 1.0, 0.36])                  # ellipsoid: z from 0.28 to 1.0
    crown.apply_translation([0, 0, 0.64])
    g.mesh('SM_Tree_Broadleaf', [prim(trunk, 'M_Bark'), prim(crown, 'M_Leaves')], extras=dict(unit='h=1 m, r=1 m'))
    trunk2 = trimesh.creation.cylinder(radius=0.06, height=0.25, sections=8)
    trunk2.apply_translation([0, 0, 0.125])
    cone = trimesh.creation.cone(radius=1.0, height=0.85, sections=12)
    cone.apply_translation([0, 0, 0.15])
    g.mesh('SM_Tree_Conifer', [prim(trunk2, 'M_Bark'), prim(cone, 'M_Leaves')], extras=dict(unit='h=1 m, r=1 m'))
    return g.save(path)


SIGN_BOARD = dict(width_m=1.6, height_m=0.9, thick_m=0.08, center_z_m=1.55)


def sign_glb(path):
    """Signpost, base at the origin; the readable face of the board looks along +X (UE +X)."""
    g = GLB()
    g.material('M_SignBoard', (0.93, 0.91, 0.84), rough=0.7)
    g.material('M_SignPost', (0.25, 0.20, 0.16), rough=0.9)
    sb = SIGN_BOARD
    post = trimesh.creation.box(extents=[0.1, 0.1, sb['center_z_m']])
    post.apply_translation([-sb['thick_m'], 0, sb['center_z_m'] / 2])
    board = trimesh.creation.box(extents=[sb['thick_m'], sb['width_m'], sb['height_m']])
    board.apply_translation([0, 0, sb['center_z_m']])

    def prim(m, mat):
        pos, nrm = gl(m.vertices), gl(m.face_normals.repeat(3, axis=0))
        v = m.vertices[m.faces.ravel()]
        pos = gl(v)
        idx = np.arange(len(v)).reshape(-1, 3)
        return dict(pos=pos, nrm=nrm, idx=fix_winding(pos, idx, nrm), mat=mat)

    g.mesh('SM_SignPost', [prim(board, 'M_SignBoard'), prim(post, 'M_SignPost')], extras=dict(board=sb))
    return g.save(path)


def water_glb(water, path):
    g = GLB()
    g.material('M_Water', (0.16, 0.34, 0.38), rough=0.05, alpha=0.85)
    rows = []
    for lake in water['lakes']:
        v, f = triangulate(lake['ring'])
        z = lake['z'] + WATER_LIFT_M
        pos = gl(np.column_stack([v, np.full(len(v), z)]))
        nrm = gl(np.tile([0, 0, 1.0], (len(v), 1)))
        otype, oid = lake['osm_id'].split('/')
        name = f'W_{otype}_{oid}'
        g.mesh(name, [dict(pos=pos, nrm=nrm, idx=fix_winding(pos, f, nrm), mat='M_Water')],
               extras=dict(osm_id=lake['osm_id'], name=lake.get('name'), kind='lake', z_m=z))
        rows.append(dict(mesh=name, osm_id=lake['osm_id'], name=lake.get('name') or '', kind='lake',
                         surface_z_cm=round(z * 100, 1), area_m2=lake.get('area_m2')))
    for st in water['streams']:
        L = np.asarray(st['line'], dtype=np.float64)
        if len(L) < 2:
            continue
        half = st.get('width_m', 2.0) / 2
        d = np.gradient(L[:, :2], axis=0)
        d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)
        side = np.column_stack([-d[:, 1], d[:, 0]])       # left of the flow direction
        zc = L[:, 2] + 0.05
        left = np.column_stack([L[:, :2] + side * half, zc])
        right = np.column_stack([L[:, :2] - side * half, zc])
        P = np.empty((2 * len(L), 3))
        P[0::2], P[1::2] = left, right
        n = len(L)
        idx = []
        for i in range(n - 1):
            a, b, c, e = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
            idx += [(a, b, c), (b, e, c)]
        pos = gl(P)
        nrm = gl(np.tile([0, 0, 1.0], (len(P), 1)))
        otype, oid = st['osm_id'].split('/')
        name = f'W_{otype}_{oid}'
        g.mesh(name, [dict(pos=pos, nrm=nrm, idx=fix_winding(pos, idx, nrm), mat='M_Water')],
               extras=dict(osm_id=st['osm_id'], kind='stream', width_m=st.get('width_m')))
        rows.append(dict(mesh=name, osm_id=st['osm_id'], name=st.get('name') or '', kind='stream',
                         surface_z_cm=f"{round(zc.min() * 100, 1)}..{round(zc.max() * 100, 1)}",
                         area_m2=''))
    return g.save(path), rows


# =============================================================== main
def main():
    need(os.path.join(SRC, 'MANIFEST.json'))
    manifest = json.load(open(os.path.join(SRC, 'MANIFEST.json')))
    if manifest.get('status') != 'complete':
        sys.exit('world/3d/MANIFEST.json status is not "complete"')
    os.makedirs(OUT, exist_ok=True)
    water = json.load(open(need(os.path.join(SRC, 'water.json'))))
    blds = json.load(open(need(os.path.join(SRC, 'buildings.json'))))['buildings']
    trees = json.load(open(need(os.path.join(SRC, 'trees.json'))))['trees']
    signs = json.load(open(need(os.path.join(SRC, 'signs.json'))))['signs']
    files = {}

    log('terrain: carving lakes and streams')
    T = Terrain(water)
    log(f'  heightmap {T.w}x{T.h} @ {T.step} m, carved {int(T.carved.sum())} vertices')

    # ---- ground texture: power-of-two copy of surface_albedo.png (UV covers x 0..1274 m, y 0..811 m)
    alb = Image.open(need(os.path.join(SRC, 'surface_albedo.png'))).convert('RGB')
    assert alb.size == (1274, 811), alb.size
    tex = alb.resize((2048, 1024), Image.LANCZOS)
    tex_path = os.path.join(OUT, 'T_ReedGround.png')
    tex.save(tex_path, optimize=True)
    import io
    buf = io.BytesIO()
    tex.save(buf, format='PNG', optimize=True)

    # ---- (a) landscape heightmaps
    land = {}
    for size, sp in ((1009, 127.0), (2017, 64.0)):
        arr, info = landscape(T, size, sp)
        Image.fromarray(arr).save(os.path.join(OUT, info['file']))
        land[str(size)] = info
        log(f"  landscape {size}: UI location {info['ui_location_cm']} actor {info['actor_location_cm']} scale {info['scale']} "
            f"max decode error {info['max_decode_error_cm']} cm")

    # ---- (b) terrain mesh tiles
    sz, tiles = terrain_glb(T, buf.getvalue(), os.path.join(OUT, 'reed_terrain.glb'))
    log(f'  reed_terrain.glb {sz / 1e6:.1f} MB, {len(tiles)} tiles')

    # ---- buildings
    sz, brows = buildings_glb(blds, os.path.join(OUT, 'reed_buildings.glb'))
    log(f'  reed_buildings.glb {sz / 1e6:.1f} MB, {len(brows)} buildings')
    with open(os.path.join(OUT, 'buildings.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['mesh', 'osm_id', 'name', 'realm', 'map_no'], lineterminator='\n')
        w.writeheader()
        w.writerows(brows)

    # ---- trees
    tree_glb(os.path.join(OUT, 'reed_tree.glb'))
    with open(os.path.join(OUT, 'trees.csv'), 'w', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['x_cm', 'y_cm', 'z_cm', 'height_cm', 'radius_cm', 'source', 'form'])
        for t in trees:
            X, Y, Z = ue(t['x'], t['y'], t['z'])
            w.writerow([round(X, 1), round(Y, 1), round(Z, 1), round(t['h'] * 100, 1), round(t['r'] * 100, 1),
                        t['src'], t['form']])
    log(f'  trees.csv {len(trees)} trees')

    # ---- signs
    sign_glb(os.path.join(OUT, 'reed_sign.glb'))
    with open(os.path.join(OUT, 'signs.csv'), 'w', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['x_cm', 'y_cm', 'z_cm', 'yaw_deg', 'yaw_to_door_deg', 'door_x_cm', 'door_y_cm',
                    'osm_id', 'kind', 'name', 'map_no', 'category', 'door_estimated', 'text'])
        for s in signs:
            X, Y, Z = ue(s['x'], s['y'], T.sample(np.array(s['x']), np.array(s['y'])).item())
            if s.get('door'):                      # landmarks (lake, canyon, circle) have no door
                DX, DY, _ = ue(s['door'][0], s['door'][1], 0)
                to_door = round(math.degrees(math.atan2(DY - Y, DX - X)), 2)
                DX, DY = round(DX, 1), round(DY, 1)
            else:
                DX = DY = to_door = ''
            parts = [p for p in (s.get('use'), ('Since 2010: ' + s['changes']) if s.get('changes') else None) if p]
            text = ' '.join(parts) if parts else s['name'] + '.'
            w.writerow([round(X, 1), round(Y, 1), round(Z, 1), round(ue_yaw(s['facing_deg']), 2), to_door,
                        DX, DY, s['osm_id'], s['kind'], s['name'], s.get('map_no') or '',
                        s.get('category') or '', 'true' if s['door_estimated'] else 'false', text])
    log(f'  signs.csv {len(signs)} signs')

    # ---- water
    sz, wrows = water_glb(water, os.path.join(OUT, 'reed_water.glb'))
    with open(os.path.join(OUT, 'water.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['mesh', 'osm_id', 'name', 'kind', 'surface_z_cm', 'area_m2'], lineterminator='\n')
        w.writeheader()
        w.writerows(wrows)
    log(f'  reed_water.glb {sz / 1e3:.0f} kB, {len(wrows)} water bodies')

    # ---- player start: south of Eliot Circle, facing Eliot Hall
    g = json.load(open(GEOJSON))
    feats = {f['properties']['id']: f for f in g['features']}
    circle = shape(feats['way/119843563']['geometry'])
    eliot = shape(feats['way/39516532']['geometry'])
    ex, ey = eliot.centroid.x, eliot.centroid.y
    # Search the ground south of the circle (8-40 m below its south edge) for the open spot with the
    # clearest line of sight to Eliot Hall: not inside a building or water, no trunk within 3 m, and as
    # few tree crowns as possible crossing the sight line. Ties go to the spot nearest the circle.
    TX = np.array([[t['x'], t['y'], t['r']] for t in trees])
    solid = unary_union([Polygon(b['ring']) for b in blds] + [Polygon(l['ring']) for l in water['lakes']])
    target = Point(ex, ey)
    best = None
    for yy in np.arange(circle.bounds[1] - 8, circle.bounds[1] - 40, -2.0):
        for xx in np.arange(circle.centroid.x - 30, circle.centroid.x + 31, 2.0):
            p = Point(xx, yy)
            if solid.distance(p) < 3:
                continue
            d = np.hypot(TX[:, 0] - xx, TX[:, 1] - yy)
            if (d < 3).any():
                continue
            sight = LineString([(xx, yy), (ex, ey)])
            perp = np.abs((TX[:, 0] - xx) * (ey - yy) - (TX[:, 1] - yy) * (ex - xx)) / sight.length
            near = TX[perp < TX[:, 2]]
            blocked = sum(1 for t in near if sight.distance(Point(t[0], t[1])) < t[2] * 0.8)
            key = (blocked, p.distance(Point(circle.centroid.x, circle.bounds[1])))
            if best is None or key < best[0]:
                best = (key, xx, yy)
    (blocked, _), cx, py = best
    pz = T.sample(np.array(cx), np.array(py)).item() + 1.0
    log(f'  player start at world ({cx:.0f}, {py:.0f}) m, {blocked} crowns on the sight line to Eliot Hall')
    PX, PY, PZ = ue(cx, py, pz)
    EX, EY, _ = ue(ex, ey, 0)
    player = dict(location_cm=[round(PX, 1), round(PY, 1), round(PZ, 1)],
                  yaw_deg=round(math.degrees(math.atan2(EY - PY, EX - PX)), 2),
                  world_m=[round(cx, 2), round(py, 2), round(pz, 2)],
                  note='open ground 8-40 m south of the Eliot Circle lawn with the clearest view of Eliot Hall; '
                       '1 m above ground; yaw looks at the Eliot Hall centroid')

    # ---- self-check reference for import_reed.py: Eliot Hall bounds in UE cm
    eb = [b for b in blds if b['osm_id'] == 'way/39516532'][0]
    ring = np.asarray(eb['ring'])
    ref = dict(mesh='B_way_39516532', name='Eliot Hall',
               min_cm=[round(ring[:, 0].min() * 100, 1), round(-ring[:, 1].max() * 100, 1), round(eb['base_z'] * 100, 1)],
               max_cm=[round(ring[:, 0].max() * 100, 1), round(-ring[:, 1].min() * 100, 1), round(eb['top_z'] * 100, 1)])

    kit = dict(
        name='Reed College - Unreal import kit', generator='scripts/export_unreal.py',
        source_manifest=manifest.get('generated'), credit=manifest.get('credit'),
        mapping=dict(world='metres; x east, y north, z elevation (NAVD88), right-handed',
                     unreal='UE.X = x*100 (east), UE.Y = -y*100 (south), UE.Z = z*100 (elevation, no datum shift)',
                     gltf='glTF metres, Y up: (x, z, -y). UE glTF import maps glTF (X,Y,Z) m -> UE (X,Z,Y)*100 cm',
                     yaw='UE yaw = -(world heading, deg CCW from east); east 0, south 90, north -90',
                     campus_bounds_cm=dict(x=[0, 127200], y=[-81000, 0])),
        ground_texture=dict(file='T_ReedGround.png', size=[2048, 1024], covers_world_m=dict(x=[0, 1274], y=[0, 811]),
                            world_uv='U = UE.X / 127400; V = (UE.Y + 81100) / 81100'),
        terrain=dict(mesh_glb='reed_terrain.glb', tiles=tiles, carved=dict(lake_depth_m=LAKE_DEPTH_M,
                     lake_ramp_m=LAKE_RAMP_M, stream_m=STREAM_CARVE_M), heightmap_step_m=T.step),
        landscape=land,
        player_start=player, reference=ref,
        sign_board=SIGN_BOARD,
        counts=dict(buildings=len(brows), trees=len(trees), signs=len(signs), water=len(wrows)),
    )
    json.dump(kit, open(os.path.join(OUT, 'reed_kit.json'), 'w'), indent=1)
    for fn in sorted(os.listdir(OUT)):
        p = os.path.join(OUT, fn)
        if os.path.isfile(p):
            files[fn] = os.path.getsize(p)
    log('wrote ' + ', '.join(f'{k} ({v / 1e6:.2f} MB)' for k, v in files.items()))


if __name__ == '__main__':
    main()
