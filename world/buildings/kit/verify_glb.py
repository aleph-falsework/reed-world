#!/usr/bin/env python3
"""Checks reed_buildings_detailed.glb: loads in trimesh, node names match reed_buildings.glb, triangle winding agrees
with the normals, walls face out of the footprint, roofs face up, and UVs tile at the true texture scale."""
import json
import os
import struct
import sys

import numpy as np
import trimesh
from shapely import contains_xy
from shapely.geometry import Polygon

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
GLB = os.path.join(ROOT, 'world', 'buildings', 'out', 'reed_buildings_detailed.glb')
OLD = os.path.join(ROOT, 'world', 'unreal', 'reed_buildings.glb')


def glb_json(p):
    with open(p, 'rb') as f:
        f.read(12)
        n = struct.unpack('<I', f.read(4))[0]
        f.read(4)
        return json.loads(f.read(n))


def main():
    ok = True
    scene = trimesh.load(GLB)                      # resolves textures/ next to the glb
    print('trimesh: %d geometries, %d triangles' % (len(scene.geometry), sum(len(g.faces) for g in scene.geometry.values())))
    j, jo = glb_json(GLB), glb_json(OLD)
    new = [n['name'] for n in j['nodes']]
    old = [n['name'] for n in jo['nodes']]
    same = set(new) == set(old) and len(new) == len(set(new))
    print('node names identical to reed_buildings.glb: %s (%d vs %d)' % (same, len(new), len(old)))
    ok &= same
    tiles = {k: v['tile_m'] for k, v in json.load(open(os.path.join(HERE, 'materials.json')))['materials'].items()}
    blds = {b['osm_id']: b for b in json.load(open(os.path.join(ROOT, 'world', '3d', 'buildings.json')))['buildings']}
    with open(GLB, 'rb') as f:
        data = f.read()
    jl = struct.unpack('<I', data[12:16])[0]
    binoff = 20 + jl + 8

    def acc(i):
        a = j['accessors'][i]
        v = j['bufferViews'][a['bufferView']]
        dt = np.uint32 if a['componentType'] == 5125 else np.float32
        k = dict(SCALAR=1, VEC2=2, VEC3=3)[a['type']]
        return np.frombuffer(data, dt, a['count'] * k, binoff + v['byteOffset']).reshape(a['count'], k)
    wind_bad = tri = wall_out = wall_n = roof_up = roof_n = nb_out = nb_n = 0
    uv_err = []
    for node in j['nodes']:
        b = blds[node['extras']['osm_id']]
        P = Polygon(b['ring']).buffer(0)
        for prim in j['meshes'][node['mesh']]['primitives']:
            mat = j['materials'][prim['material']]['name'][2:]
            pos = acc(prim['attributes']['POSITION']).astype(np.float64)
            nrm = acc(prim['attributes']['NORMAL']).astype(np.float64)
            uv = acc(prim['attributes']['TEXCOORD_0']).astype(np.float64)
            F = acc(prim['indices'])[:, 0].reshape(-1, 3)
            a, bb, c = pos[F[:, 0]], pos[F[:, 1]], pos[F[:, 2]]
            fn = np.cross(bb - a, c - a)
            vn = nrm[F].sum(1)
            wind_bad += int(((fn * vn).sum(1) < 0).sum())
            tri += len(F)
            # world coords: x = gl x, y = -gl z, z = gl y
            cen = (a + bb + c) / 3
            wx, wy, wz = cen[:, 0], -cen[:, 2], cen[:, 1]
            n = vn / np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-9)
            nx, ny, nz = n[:, 0], -n[:, 2], n[:, 1]
            # main walls: vertical faces lying on the footprint outline
            vert = np.abs(nz) < 0.05
            on = np.array([P.exterior.distance(__import__('shapely').geometry.Point(x, y)) < 0.02 for x, y in zip(wx[vert], wy[vert])]) if vert.any() else np.zeros(0, bool)
            if on.any():
                px, py = wx[vert][on] + nx[vert][on] * 0.1, wy[vert][on] + ny[vert][on] * 0.1
                outside = ~contains_xy(P, px, py)
                if b['neighbourhood']:          # footprints simplified by 0.4 m: informational only
                    nb_out += int(outside.sum())
                    nb_n += int(on.sum())
                else:
                    wall_out += int(outside.sum())
                    wall_n += int(on.sum())
            if mat.startswith('Roof'):
                roof_up += int((nz > 0).sum())
                roof_n += len(nz)
            # UV scale on wall material faces: |dUV| / |dXYZ| == 1 / tile
            t = tiles.get(mat)
            if t and mat in ('Brick_Gothic', 'Brick_Common', 'Siding_Wood', 'Stucco', 'Concrete') and len(F):
                e_xyz = np.linalg.norm(bb - a, axis=1)
                e_uv = np.linalg.norm(uv[F[:, 1]] - uv[F[:, 0]], axis=1)
                m = e_xyz > 0.5
                uv_err.extend(list(e_uv[m] / e_xyz[m] * t[0]))
    print('triangles %d, winding disagreeing with normals: %d' % (tri, wind_bad))
    print('campus walls on the footprint facing outward: %d / %d' % (wall_out, wall_n))
    print('neighbourhood (simplified footprints, informational): %d / %d' % (nb_out, nb_n))
    print('roof faces pointing up: %d / %d' % (roof_up, roof_n))
    u = np.array(uv_err)
    print('wall UV scale (|dUV| * tile / |dxyz|, 1.0 = true scale): median %.4f, p1 %.4f, p99 %.4f' %
          (np.median(u), np.percentile(u, 1), np.percentile(u, 99)))
    ok &= wind_bad == 0 and wall_out == wall_n and roof_up == roof_n and abs(np.median(u) - 1) < 1e-3
    print('OK' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
