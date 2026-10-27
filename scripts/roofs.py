#!/usr/bin/env python3
"""Roof forms from the 2014 DOGAMI lidar.

Reads   world/3d/buildings.json (footprints in world metres)
        data/raw/lidar/highest_hit_utm10n_1m.tif, world/lidar/canopy.tif, world/lidar/hillshade.png
Writes  world/buildings/roofs.json
        world/buildings/roofs_qa.png, roofs_qa_campus.png
        world/buildings/profiles/<name>.png (cross-sections, lidar vs fitted roof)

Method (per building)
  1. Sample the highest-hit surface inside the footprint eroded by 1 m (full footprint
     for footprints too small to erode).
  2. Per-pixel 3x3 detrended roughness r3 (RMS residual of a local plane fit). Canopy
     returns are rough; planar roofs are not (a 45 deg ridge gives r3 ~ 0.47 m).
     Pixels with r3 > 1.0 m are dropped before plane fitting.
  3. Sequential RANSAC of planes (tolerance 0.3 m), scoring the largest 8-connected
     inlier component; LSQ refit; then merge near-coplanar facets.
  4. Unexplained pixels > 1 m above the nearest facet form blobs. A blob is vegetation
     when it is adjacent to canopy outside the footprint, or is rough (median r3 > 0.6 m)
     and larger than 6 m2. Other blobs are chimneys / dormers / mechanical boxes / towers
     by size, height above the roof and the facet they sit on.
  5. Classify from the main facets: flat (>= 75 % of roof points on facets < 8 deg),
     shed (one pitched facet), gabled (two opposite pitched facets), hipped (3-4 facets
     covering >= 3 aspect directions, <= 1 ridge, compact footprint), else complex.
  6. Ridges = horizontal intersection lines of adjacent, opposite-facing facets that are
     convex (both facets fall away from the line). Hips = sloping convex intersections.

Conventions: azimuths/aspects are compass bearings, degrees clockwise from north
(ridge azimuth folded to [0, 180)). Planes are z = a*x + b*y + c in world metres,
z = elevation (NAVD88). Heights *_h_m are above the building's ground_mean_z (the same
reference world/3d/buildings.json uses for top_z).
"""
import json
import math
import os
import sys
import time

import numpy as np
import rasterio
from rasterio import features
from affine import Affine
from scipy import ndimage as ndi
from scipy.spatial import cKDTree
from shapely.geometry import Polygon, LineString, shape, mapping
from shapely.ops import unary_union

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'world', 'buildings')
H, W = 811, 1274
T = Affine(1, 0, 0, 0, -1, H)  # world frame: x = col, y = 811 - row
RNG = np.random.default_rng(20141)

TOL = 0.30          # RANSAC inlier tolerance, m
FLAT_DEG = 8.0      # facets below this slope count as flat
ROUGH_DROP = 1.0    # r3 above this: never a roof pixel
STEP_OVER = 1.0     # blob threshold above facet, m


def load():
    with rasterio.open(os.path.join(ROOT, 'data/raw/lidar/highest_hit_utm10n_1m.tif')) as r:
        hh = r.read(1).astype(np.float64)
    with rasterio.open(os.path.join(ROOT, 'world/lidar/canopy.tif')) as r:
        can = r.read(1) == 1
    return hh, can


def roughness(z):
    """3x3 local plane fit: returns dz/dx, dz/dy (world), rms residual."""
    nan = ~np.isfinite(z)
    zf = np.where(nan, 0.0, z)
    ku = np.array([[-1, 0, 1]] * 3, float)            # u = +col (east)
    kv = np.array([[1, 1, 1], [0, 0, 0], [-1, -1, -1]], float)  # v = north (row up)
    one = np.ones((3, 3))
    s = ndi.correlate(zf, one, mode='nearest')
    s2 = ndi.correlate(zf * zf, one, mode='nearest')
    su = ndi.correlate(zf, ku, mode='nearest')
    sv = ndi.correlate(zf, kv, mode='nearest')
    a, b, c = su / 6, sv / 6, s / 9
    ssr = s2 - 9 * c * c - 6 * a * a - 6 * b * b
    r3 = np.sqrt(np.clip(ssr, 0, None) / 9)
    bad = ndi.maximum_filter(nan.astype(np.uint8), 3) > 0
    r3[bad] = np.nan
    return a, b, r3


def plane_lsq(x, y, z):
    A = np.c_[x, y, np.ones_like(x)]
    p, *_ = np.linalg.lstsq(A, z, rcond=None)
    return p


def slope_aspect(p):
    a, b = p[0], p[1]
    slope = math.degrees(math.atan(math.hypot(a, b)))
    # downslope direction is -(a, b); bearing clockwise from north
    asp = (math.degrees(math.atan2(-a, -b)) + 360) % 360
    return slope, asp


def angdiff(a, b, period=360.0):
    d = abs(a - b) % period
    return min(d, period - d)


def largest_cc(mask2d):
    lab, n = ndi.label(mask2d, structure=np.ones((3, 3)))
    if n == 0:
        return mask2d & False
    cnt = np.bincount(lab.ravel())
    cnt[0] = 0
    return lab == cnt.argmax()


class Roof:
    """Work on one building's window of the rasters."""

    def __init__(self, b, hh, can, rough):
        self.b = b
        self.poly = Polygon(b['ring']).buffer(0)
        x0, y0, x1, y1 = self.poly.bounds
        pad = 4
        self.c0 = max(int(math.floor(x0)) - pad, 0)
        self.c1 = min(int(math.ceil(x1)) + pad, W)
        self.r0 = max(int(math.floor(H - y1)) - pad, 0)
        self.r1 = min(int(math.ceil(H - y0)) + pad, H)
        sl = (slice(self.r0, self.r1), slice(self.c0, self.c1))
        self.hh = hh[sl]
        self.can = can[sl]
        self.r3 = rough[sl]
        h, w = self.hh.shape
        self.tr = T * Affine.translation(self.c0, self.r0)
        self.shape = (h, w)
        self.full = features.rasterize([self.poly], out_shape=(h, w), transform=self.tr,
                                       all_touched=False).astype(bool)
        if self.full.sum() < 4:
            self.full = features.rasterize([self.poly], out_shape=(h, w), transform=self.tr,
                                           all_touched=True).astype(bool)
        er = self.poly.buffer(-1.0)
        self.core = np.zeros((h, w), bool)
        if not er.is_empty:
            self.core = features.rasterize([er], out_shape=(h, w), transform=self.tr).astype(bool)
        if self.core.sum() < max(6, 0.25 * self.full.sum()):
            self.core = self.full.copy()
        rr, cc = np.mgrid[0:h, 0:w]
        self.X = self.c0 + cc + 0.5
        self.Y = H - (self.r0 + rr) - 0.5

    # ---------------------------------------------------------------- planes
    def fit(self, max_planes=12):
        valid = self.core & np.isfinite(self.hh)
        self.valid = valid
        self.n_valid = int(valid.sum())
        cand = valid & ~(self.r3 > ROUGH_DROP)
        self.labels = np.zeros(self.shape, int)  # 0 unassigned, k facet id
        self.facets = []
        if self.n_valid < 5:
            return
        idx = np.argwhere(cand)
        min_sup = max(8, min(int(0.03 * self.n_valid), 40))
        free = cand.copy()
        k = 0
        fails = 0
        while k < max_planes:
            fi = np.argwhere(free)
            if len(fi) < min_sup:
                break
            xs, ys = self.X[free], self.Y[free]
            zs = self.hh[free]
            tree = cKDTree(np.c_[xs, ys])
            n = len(xs)
            trials = 250 if n > 60 else 120
            rs_ = self.r3[free]
            smooth = np.nonzero(np.nan_to_num(rs_, nan=9) < 0.2)[0]
            pool = smooth if len(smooth) >= 10 else np.arange(n)
            seeds = pool[RNG.integers(0, len(pool), trials)]
            nb = tree.query_ball_point(np.c_[xs[seeds], ys[seeds]], r=2.3)
            planes = []
            for s_, lst in zip(seeds, nb):
                if len(lst) < 6:
                    continue
                lst = np.asarray(lst)
                pp = plane_lsq(xs[lst], ys[lst], zs[lst])
                rr_ = np.abs(zs[lst] - (pp[0] * xs[lst] + pp[1] * ys[lst] + pp[2]))
                if np.median(rr_) > TOL:
                    continue
                if math.degrees(math.atan(math.hypot(pp[0], pp[1]))) > 70:
                    continue
                planes.append(tuple(pp))
            if not planes:
                fails += 1
                if fails >= 3:
                    break
                continue
            Pm = np.array(planes)
            res = np.abs(zs[None, :] - (Pm[:, 0:1] * xs[None, :] + Pm[:, 1:2] * ys[None, :] + Pm[:, 2:3]))
            cnt = (res < TOL).sum(1)
            best = None
            for j in np.argsort(-cnt)[:6]:
                m = np.zeros(self.shape, bool)
                m[free] = res[j] < TOL
                m = largest_cc(m)
                sz = m.sum()
                if best is None or sz > best[0]:
                    best = (sz, m, Pm[j])
            sz, m, p = best
            if sz < min_sup:
                fails += 1
                if fails >= 3:
                    break
                continue
            for _ in range(3):  # refit and regrow
                p = plane_lsq(self.X[m], self.Y[m], self.hh[m])
                if not np.all(np.isfinite(p)):
                    break
                r = np.abs(self.hh - (p[0] * self.X + p[1] * self.Y + p[2]))
                g = free & (r < TOL)
                lab, _ = ndi.label(g, structure=np.ones((3, 3)))
                keep = np.unique(lab[m & g])
                keep = keep[keep > 0]
                m2 = np.isin(lab, keep) if len(keep) else m
                if m2.sum() == m.sum():
                    m = m2
                    break
                m = m2
            if m.sum() < min_sup:
                fails += 1
                if fails >= 3:
                    break
                continue
            k += 1
            self.labels[m] = k
            free &= ~m
            self.facets.append({'id': k, 'p': p})
        self._merge()
        self._absorb()
        self._facet_stats()

    def _merge(self):
        changed = True
        while changed and len(self.facets) > 1:
            changed = False
            for i in range(len(self.facets)):
                for j in range(i + 1, len(self.facets)):
                    fi, fj = self.facets[i], self.facets[j]
                    mi, mj = self.labels == fi['id'], self.labels == fj['id']
                    u = mi | mj
                    p = plane_lsq(self.X[u], self.Y[u], self.hh[u])
                    r = np.abs(self.hh[u] - (p[0] * self.X[u] + p[1] * self.Y[u] + p[2]))
                    si, ai = slope_aspect(fi['p'])
                    sj, aj = slope_aspect(fj['p'])
                    nd = abs(si - sj) < 4 and (angdiff(ai, aj) < 12 or max(si, sj) < 3)
                    if nd and np.percentile(r, 90) < TOL * 1.3:
                        self.labels[mj] = fi['id']
                        fi['p'] = p
                        del self.facets[j]
                        changed = True
                        break
                if changed:
                    break

    def _absorb(self):
        """Assign leftover valid pixels lying within 1.5*TOL of an adjacent facet plane."""
        for _ in range(2):
            un = self.valid & (self.labels == 0) & ~(self.r3 > ROUGH_DROP)
            if not un.any() or not self.facets:
                return
            grow = ndi.grey_dilation(self.labels, footprint=np.ones((3, 3)))
            for f in self.facets:
                p = f['p']
                r = np.abs(self.hh - (p[0] * self.X + p[1] * self.Y + p[2]))
                take = un & (grow == f['id']) & (r < 1.5 * TOL)
                self.labels[take] = f['id']
                un &= ~take

    def _facet_stats(self):
        ground = self.b['ground_mean_z']
        for f in self.facets:
            m = self.labels == f['id']
            f['n'] = int(m.sum())
            f['slope'], f['aspect'] = slope_aspect(f['p'])
            f['z_med'] = float(np.median(self.hh[m]))
            f['h_med'] = f['z_med'] - ground
            f['pitched'] = f['slope'] >= FLAT_DEG
            f['mask'] = m
            inner = m & ndi.binary_erosion(m)
            rv = self.r3[inner if inner.sum() >= 3 else m]
            f['rough'] = float(np.nanmedian(rv)) if np.isfinite(rv).any() else 9.0
        # facets fitted into canopy: rough interior -> vegetation, not roof
        self.veg_facet_px = 0
        for f in list(self.facets):
            if f['rough'] > 0.35 and f['n'] < 400:
                self.labels[f['mask']] = 0
                self.veg_facet_px += f['n']
                self.facets.remove(f)

    # ---------------------------------------------------------------- blobs
    def blobs(self):
        self.features = []
        self.veg_px = getattr(self, 'veg_facet_px', 0)
        if not self.facets:
            un = self.valid
            self.veg_px = int((self.valid & (self.r3 > 0.6)).sum())
            return
        dist, (ir, ic) = ndi.distance_transform_edt(self.labels == 0, return_indices=True)
        near = self.labels[ir, ic]
        planez = np.full(self.shape, np.nan)
        for f in self.facets:
            p = f['p']
            m = near == f['id']
            planez[m] = p[0] * self.X[m] + p[1] * self.Y[m] + p[2]
        dz = self.hh - planez
        self.dz = dz
        un = self.valid & (self.labels == 0)
        known_veg = un & (self.r3 > ROUGH_DROP)
        high = un & (dz > STEP_OVER)
        lab, n = ndi.label(high, structure=np.ones((3, 3)))
        outside_can = self.can & ~self.full
        ring = ndi.binary_dilation(self.full, iterations=3) & ~self.full
        out_can_near = outside_can & ring
        ground = self.b['ground_mean_z']
        main_top = max((f['z_med'] for f in self.facets if f['n'] >= 0.1 * self.n_valid),
                       default=max(f['z_med'] for f in self.facets))
        for k in range(1, n + 1):
            m = lab == k
            area = int(m.sum())
            rr = float(np.nanmedian(self.r3[m])) if np.isfinite(self.r3[m]).any() else 9.0
            # does this blob, grown outwards, meet canopy outside the footprint?
            g = ndi.binary_dilation(m, iterations=4)
            touches_tree = bool((g & out_can_near).any())
            top = float(np.nanmax(self.hh[m]))
            over = float(np.nanmax(dz[m]))
            host = np.bincount(near[m]).argmax()
            hf = next(f for f in self.facets if f['id'] == host)
            cx, cy = float(self.X[m].mean()), float(self.Y[m].mean())
            if touches_tree or (area > 6 and rr > 0.6) or (area > 40 and rr > 0.45):
                self.veg_px += area
                continue
            if area < 2 and over < 1.5:
                continue
            if over > 4 and area >= 9:
                kind = 'tower'
            elif not hf['pitched'] and area <= 400:
                kind = 'mechanical'
            elif area <= 6:
                kind = 'chimney'
            elif hf['pitched'] and area <= 40 and over <= 3.5:
                kind = 'dormer'
            else:
                kind = 'raised_block'
            self.features.append({'type': kind, 'x': round(cx, 2), 'y': round(cy, 2),
                                  'area_m2': area, 'top_h_m': round(top - ground, 2),
                                  'above_roof_m': round(over, 2), 'roughness_m': round(rr, 2)})
        # facets standing well above the main roof and small = tower/raised volume
        for f in self.facets:
            if f['n'] < 0.2 * self.n_valid and f['z_med'] > main_top + 4 and f['n'] >= 6:
                m = f['mask']
                f['role'] = 'tower'
                self.features.append({'type': 'tower', 'x': round(float(self.X[m].mean()), 2),
                                      'y': round(float(self.Y[m].mean()), 2), 'area_m2': f['n'],
                                      'top_h_m': round(float(np.max(self.hh[m])) - ground, 2),
                                      'above_roof_m': round(f['z_med'] - main_top, 2),
                                      'facet': f['id']})
        # rough unexplained pixels not high above the roof (canopy at roof level)
        self.veg_px += int((un & ~high & (self.r3 > 0.6)).sum())

    # ---------------------------------------------------------------- lines
    def lines(self):
        self.ridges, self.hips, self.valleys = [], [], []
        P = [f for f in self.facets if f.get('role') != 'tower' and f['n'] >= 6]
        dil = {f['id']: ndi.binary_dilation(f['mask'], iterations=2) for f in P}
        for i in range(len(P)):
            for j in range(i + 1, len(P)):
                fi, fj = P[i], P[j]
                if not (fi['pitched'] or fj['pitched']):
                    continue
                both = dil[fi['id']] & dil[fj['id']]
                if both.sum() < 3:
                    continue
                pi, pj = fi['p'], fj['p']
                ni = np.array([-pi[0], -pi[1], 1.0])
                nj = np.array([-pj[0], -pj[1], 1.0])
                d = np.cross(ni, nj)
                dh = math.hypot(d[0], d[1])
                if dh < 1e-6:
                    continue
                d = d / np.linalg.norm(d)
                # point on line: solve the two plane eqns at the centroid of the shared zone
                bx, by = self.X[both], self.Y[both]
                mx, my = bx.mean(), by.mean()
                # minimise distance to (mx,my) subject to both planes:  (a_i-a_j)x+(b_i-b_j)y = c_j-c_i
                A = np.array([pi[0] - pj[0], pi[1] - pj[1]])
                rhs = pj[2] - pi[2]
                t = (rhs - A @ [mx, my]) / (A @ A)
                q = np.array([mx, my]) + t * A
                u = np.array([d[0], d[1]]) / dh
                s = (bx - q[0]) * u[0] + (by - q[1]) * u[1]
                s0, s1 = np.percentile(s, 2), np.percentile(s, 98)
                if s1 - s0 < 1.5:
                    continue
                p0, p1 = q + s0 * u, q + s1 * u
                z0 = pi[0] * p0[0] + pi[1] * p0[1] + pi[2]
                z1 = pi[0] * p1[0] + pi[1] * p1[1] + pi[2]
                # convex? both facets lie below the line
                zc_i = fi['z_med']
                zc_j = fj['z_med']
                zl = 0.5 * (z0 + z1)
                convex = zl > zc_i + 0.2 and zl > zc_j + 0.2
                ln = LineString([tuple(p0), tuple(p1)]).intersection(self.poly.buffer(0.5))
                if ln.is_empty or ln.length < 1.5:
                    continue
                if ln.geom_type != 'LineString':
                    ln = max(getattr(ln, 'geoms', [ln]), key=lambda g: g.length)
                (ax_, ay_), (bx_, by_) = ln.coords[0], ln.coords[-1]
                za = pi[0] * ax_ + pi[1] * ay_ + pi[2]
                zb = pi[0] * bx_ + pi[1] * by_ + pi[2]
                ground = self.b['ground_mean_z']
                seg = {'p0': [round(ax_, 2), round(ay_, 2)], 'p1': [round(bx_, 2), round(by_, 2)],
                       'z0': round(za, 2), 'z1': round(zb, 2),
                       'h_m': round(0.5 * (za + zb) - ground, 2),
                       'azimuth_deg': round((math.degrees(math.atan2(bx_ - ax_, by_ - ay_)) + 360) % 180, 1),
                       'length_m': round(ln.length, 2), 'facets': [fi['id'], fj['id']]}
                level = abs(za - zb) / max(ln.length, 1e-6) < math.tan(math.radians(6))
                if fi['pitched'] and fj['pitched']:
                    opposite = angdiff(fi['aspect'], fj['aspect']) > 140
                    if convex and level and opposite:
                        seg['kind'] = 'ridge'
                        self.ridges.append(seg)
                        continue
                else:
                    # pitched facet meeting a flat deck above it -> deck edge (truncated ridge)
                    pf, ff = (fi, fj) if fi['pitched'] else (fj, fi)
                    if ff['z_med'] > pf['z_med'] + 0.2 and level and ln.length >= 3:
                        seg['kind'] = 'deck_edge'
                        self.ridges.append(seg)
                    continue
                if False:
                    pass
                elif convex and not level:
                    self.hips.append(seg)
                elif not convex and zl < min(zc_i, zc_j) - 0.2:
                    self.valleys.append(seg)
        self.ridges = merge_ridges(self.ridges, self.b['ground_mean_z'])
        self.ridges.sort(key=lambda r: (r['kind'] != 'ridge', -r['length_m']))

    # ---------------------------------------------------------------- classify
    def classify(self):
        b = self.b
        ground = b['ground_mean_z']
        main = [f for f in self.facets if f.get('role') != 'tower' and f['n'] >= max(6, 0.03 * self.n_valid)]
        npts = sum(f['n'] for f in main)
        explained = sum(f['n'] for f in self.facets)
        nonveg = max(self.n_valid - self.veg_px, 1)
        self.explained_frac = explained / nonveg
        self.veg_frac = self.veg_px / max(self.n_valid, 1)
        out = {}
        if not main:
            self.type = 'complex' if self.n_valid > 20 else 'flat'
            self.main = []
            return
        flat_n = sum(f['n'] for f in main if not f['pitched'])
        pit = [f for f in main if f['pitched']]
        pit_n = sum(f['n'] for f in pit)
        frac_flat = flat_n / npts
        self.main = main
        self.frac_flat = frac_flat
        major = [f for f in pit if f['n'] >= 0.10 * max(pit_n, 1)]
        major.sort(key=lambda f: -f['n'])
        fill = self.poly.area / max(self.poly.minimum_rotated_rectangle.area, 1e-6)
        self.fill = fill
        # aspect clusters of the pitched facets (weighted, 30 deg)
        cl = []
        for f in sorted(pit, key=lambda f: -f['n']):
            for c in cl:
                if angdiff(c['a'], f['aspect']) < 30:
                    c['n'] += f['n']
                    break
            else:
                cl.append({'a': f['aspect'], 'n': f['n']})
        cl = [c for c in cl if c['n'] >= 0.10 * max(pit_n, 1)]
        self.aspect_clusters = cl
        # deck: flat facets at the top of a pitched roof (truncated / mansard-like)
        self.deck = None
        if pit:
            ptop = np.percentile(np.concatenate([self.hh[f['mask']] for f in pit]), 95)
            decks = [f for f in main if not f['pitched'] and f['z_med'] >= ptop - 0.7]
            deck_n = sum(f['n'] for f in decks)
            if decks and pit_n >= 0.25 * npts and deck_n >= 0.7 * flat_n:
                self.deck = max(decks, key=lambda f: f['n'])
        typ = 'complex'
        nridge = len([r for r in self.ridges if r['kind'] == 'ridge'])
        if frac_flat >= 0.75:
            typ = 'flat'
        elif (frac_flat <= 0.25 or self.deck is not None) and cl:
            if len(cl) == 1:
                typ = 'shed' if self.deck is None else 'complex'
            elif len(cl) == 2 and angdiff(cl[0]['a'], cl[1]['a']) > 150:
                typ = 'gabled'
                # multiple ridges must be parallel for a (multi-)gable
                azs = [r['azimuth_deg'] for r in self.ridges if r['kind'] == 'ridge']
                if azs and max(angdiff(a, azs[0], 180) for a in azs) > 20:
                    typ = 'complex'
            elif len(cl) in (3, 4) and fill > 0.85:
                dirs = {int(round(((c['a'] - cl[0]['a']) % 360) / 90.0)) % 4 for c in cl}
                spaced = all(min(angdiff(c['a'], cl[0]['a'] + 90 * k) for k in range(4)) < 25 for c in cl)
                if len(dirs) >= 3 and spaced and nridge <= 1 and (len(self.hips) >= 1 or self.deck is not None):
                    typ = 'hipped'
        self.type = typ
        self.major = major

    def heights(self):
        ground = self.b['ground_mean_z']
        ring = self.full & ~ndi.binary_erosion(self.full)
        if not self.main:
            z = self.hh[self.valid]
            z = z[np.isfinite(z)]
            if len(z) == 0:
                return None, None, None
            e = float(np.percentile(z, 50))
            return e, e, 0.0
        if self.type == 'flat':
            flats = [f for f in self.main if not f['pitched']]
            big = max(flats, key=lambda f: f['n'])
            e = big['z_med']
            r = max(f['z_med'] for f in flats if f['n'] >= 0.1 * self.n_valid) if flats else e
            pitch = float(np.average([f['slope'] for f in flats], weights=[f['n'] for f in flats]))
            return e, r, pitch
        pit = [f for f in self.main if f['pitched']]
        eaves, w = [], []
        for f in pit:
            p = f['p']
            near = ndi.binary_dilation(f['mask'], iterations=3) & ring
            if near.sum() < 2:
                near = f['mask']
            zz = p[0] * self.X[near] + p[1] * self.Y[near] + p[2]
            # eave = lowest edge of the facet: use a low percentile of its edge pixels
            eaves.append(np.percentile(zz, 20))
            w.append(f['n'])
        e = float(np.average(eaves, weights=w))
        zz = np.concatenate([self.hh[f['mask']] for f in pit])
        r = float(np.percentile(zz, 97))
        if self.ridges:
            r = max(r, max(max(x['z0'], x['z1']) for x in self.ridges))
        if getattr(self, 'deck', None) is not None:
            r = max(r, self.deck['z_med'])
        pitch = float(np.average([f['slope'] for f in pit], weights=[f['n'] for f in pit]))
        return e, max(r, e), pitch

    def facet_polys(self):
        """Partition the full footprint among facets (nearest facet pixel)."""
        if not self.facets:
            return {}
        _, (ir, ic) = ndi.distance_transform_edt(self.labels == 0, return_indices=True)
        part = np.where(self.full, self.labels[ir, ic], 0).astype(np.int32)
        polys = {}
        for geom, val in features.shapes(part, mask=part > 0, transform=self.tr):
            polys.setdefault(int(val), []).append(shape(geom))
        res = {}
        for k, g in polys.items():
            u = unary_union(g).intersection(self.poly).simplify(0.4)
            if u.is_empty:
                continue
            res[k] = u
        return res


def merge_ridges(rs, ground):
    """Merge parallel, laterally close (< 2.5 m) overlapping segments of the same kind."""
    rs = [dict(r) for r in rs]
    changed = True
    while changed:
        changed = False
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                a, b = rs[i], rs[j]
                if a['kind'] != b['kind'] or angdiff(a['azimuth_deg'], b['azimuth_deg'], 180) > 10:
                    continue
                p0, p1 = np.array(a['p0']), np.array(a['p1'])
                u = (p1 - p0) / max(np.linalg.norm(p1 - p0), 1e-6)
                nrm = np.array([-u[1], u[0]])
                q0, q1 = np.array(b['p0']), np.array(b['p1'])
                off = max(abs((q0 - p0) @ nrm), abs((q1 - p0) @ nrm))
                sa = sorted([0.0, (p1 - p0) @ u]); sb = sorted([(q0 - p0) @ u, (q1 - p0) @ u])
                overlap = min(sa[1], sb[1]) - max(sa[0], sb[0])
                if off > 2.5 or overlap < -1.0:
                    continue
                if a['kind'] == 'deck_edge' and abs(a['h_m'] - b['h_m']) > 0.6:
                    continue
                lo, hi = min(sa[0], sb[0]), max(sa[1], sb[1])
                big = a if a['length_m'] >= b['length_m'] else b
                bp = np.array(big['p0']); bu = u
                # keep the longer one's line, extend to the union span
                c0 = p0 + lo * u; c1 = p0 + hi * u
                shift = ((bp - p0) @ nrm) * nrm
                c0, c1 = c0 + shift, c1 + shift
                z = max(a['z0'], a['z1'], b['z0'], b['z1'])
                m = {'p0': [round(c0[0], 2), round(c0[1], 2)], 'p1': [round(c1[0], 2), round(c1[1], 2)],
                     'z0': round(z, 2), 'z1': round(z, 2), 'h_m': round(z - ground, 2),
                     'azimuth_deg': big['azimuth_deg'], 'length_m': round(hi - lo, 2),
                     'kind': a['kind'], 'facets': sorted(set(a.get('facets', []) + b.get('facets', [])))}
                rs[i] = m
                del rs[j]
                changed = True
                break
            if changed:
                break
    return rs


def r2(v, n=2):
    return None if v is None else round(float(v), n)


def poly_coords(g):
    if g.geom_type == 'Polygon':
        return [[[round(x, 2), round(y, 2)] for x, y in g.exterior.coords[:-1]]]
    return [[[round(x, 2), round(y, 2)] for x, y in p.exterior.coords[:-1]] for p in g.geoms]


def estimate(b, reason):
    """Rule for buildings with no usable 2014 lidar."""
    poly = Polygon(b['ring'])
    mrr = poly.minimum_rotated_rectangle
    cs = list(mrr.exterior.coords)[:4]
    e0 = math.dist(cs[0], cs[1])
    e1 = math.dist(cs[1], cs[2])
    lo = (cs[0], cs[1]) if e0 >= e1 else (cs[1], cs[2])
    short = min(e0, e1)
    az = (math.degrees(math.atan2(lo[1][0] - lo[0][0], lo[1][1] - lo[0][1])) + 360) % 180
    h = b['height_m'] if b.get('height_source') not in ('lidar_p90',) else None
    lv_h = h if h else (7.0 if poly.area < 400 else 14.0)
    if poly.area >= 600:
        rule = ('estimate: no usable 2014 lidar roof (%s). Footprint >= 600 m2 -> flat roof at the '
                'non-lidar height from buildings.json (OSM height or levels x 3.5 m; '
                'default 14 m).' % reason)
        return {'type': 'flat', 'eave_h_m': r2(lv_h), 'ridge_h_m': r2(lv_h), 'pitch_deg': 0.0,
                'ridge_azimuth_deg': None, 'ridges': [], 'rule': rule}
    pitch = 30.0
    rise = 0.5 * short * math.tan(math.radians(pitch))
    eave = max(lv_h - rise, 2.4) if lv_h > 3.5 else 2.4
    rule = ('estimate: no usable 2014 lidar roof (%s). Footprint < 600 m2 -> gabled, 30 deg, ridge along '
            'the long axis of the minimum rotated rectangle; eave = height - rise (min 2.4 m), '
            'height = OSM height/levels x 3.5 m, else 7 m.' % reason)
    c = mrr.centroid
    half = 0.5 * max(e0, e1)
    ux, uy = math.sin(math.radians(az)), math.cos(math.radians(az))
    rz = b['ground_mean_z'] + eave + rise
    ridge = [{'p0': [r2(c.x - ux * half), r2(c.y - uy * half)], 'p1': [r2(c.x + ux * half), r2(c.y + uy * half)],
              'z0': r2(rz), 'z1': r2(rz), 'h_m': r2(eave + rise), 'azimuth_deg': r2(az, 1),
              'length_m': r2(2 * half)}]
    return {'type': 'gabled', 'eave_h_m': r2(eave), 'ridge_h_m': r2(eave + rise), 'pitch_deg': pitch,
            'ridge_azimuth_deg': r2(az, 1), 'ridges': ridge, 'rule': rule}


def process(b, hh, can, rough):
    base = {'osm_id': b['osm_id'], 'name': b['name'], 'map_no': b['map_no'], 'realm': b['realm'],
            'area_m2': b['area_m2'], 'ground_z': b['ground_mean_z']}
    if b.get('lidar_2014_absent'):
        e = estimate(b, 'lidar_2014_absent: open ground in 2014, built later')
        return dict(base, source='estimate', confidence='low', confidence_score=0.2,
                    eave_z=r2(b['ground_mean_z'] + e['eave_h_m']),
                    ridge_z=r2(b['ground_mean_z'] + e['ridge_h_m']),
                    facets=[], hips=[], features=[], **e), None
    R = Roof(b, hh, can, rough)
    R.fit(max_planes=16 if b['realm'] == 'campus' or b['area_m2'] > 400 else 6)
    if R.n_valid < 5 or not R.facets:
        e = estimate(b, ('outside the survey (east of x ~1136 m)' if Polygon(b['ring']).centroid.x > 1130
                         else 'too few lidar returns inside the footprint (%d)' % R.n_valid))
        return dict(base, source='estimate', confidence='low', confidence_score=0.15,
                    eave_z=r2(b['ground_mean_z'] + e['eave_h_m']),
                    ridge_z=r2(b['ground_mean_z'] + e['ridge_h_m']),
                    facets=[], hips=[], features=[], **e), R
    R.blobs()
    R.lines()
    R.classify()
    e, r, pitch = R.heights()
    ground = b['ground_mean_z']
    # canopy-occluded small buildings: roof plane found in the trees
    if b['area_m2'] < 500 and (e - ground) > 12 and R.veg_frac > 0.3:
        est = estimate(b, 'canopy-occluded: fitted eave %.1f m above ground with %.0f %% vegetation '
                          'on a < 500 m2 footprint' % (e - ground, 100 * R.veg_frac))
        return dict(base, source='estimate', confidence='low', confidence_score=0.2,
                    eave_z=r2(ground + est['eave_h_m']), ridge_z=r2(ground + est['ridge_h_m']),
                    facets=[], hips=[], features=[], **est), R
    fp = R.facet_polys()
    facets = []
    for f in R.facets:
        d = {'id': f['id'], 'plane': [round(f['p'][0], 5), round(f['p'][1], 5), round(f['p'][2], 3)],
             'slope_deg': r2(f['slope'], 1), 'aspect_deg': r2(f['aspect'], 1) if f['pitched'] else None,
             'kind': 'tower' if f.get('role') == 'tower' else ('pitched' if f['pitched'] else 'flat'),
             'n_px': f['n'], 'h_med_m': r2(f['h_med'])}
        if f['id'] in fp:
            d['outline'] = poly_coords(fp[f['id']])
        facets.append(d)
    ridge_az = None
    if R.type in ('gabled', 'hipped', 'complex') and R.ridges:
        ridge_az = R.ridges[0]['azimuth_deg']
    elif R.type == 'gabled' and R.major:
        ridge_az = round((R.major[0]['aspect'] + 90) % 180, 1)
    elif R.type == 'shed' and R.major:
        ridge_az = round((R.major[0]['aspect'] + 90) % 180, 1)  # high edge direction
    feats = list(R.features)
    if R.ridges and R.ridges[0]['kind'] == 'ridge':
        main_az = R.ridges[0]['azimuth_deg']
        for k, rg in enumerate(R.ridges[1:], 1):
            if rg['kind'] == 'ridge' and angdiff(rg['azimuth_deg'], main_az, 180) > 60 and rg['length_m'] >= 3:
                feats.append({'type': 'cross_gable', 'ridge_index': k,
                              'x': r2(0.5 * (rg['p0'][0] + rg['p1'][0])),
                              'y': r2(0.5 * (rg['p0'][1] + rg['p1'][1])),
                              'h_m': rg['h_m'], 'length_m': rg['length_m']})
    # flat levels
    levels = None
    if R.type == 'flat':
        levels = sorted({r2(f['h_med'], 1) for f in R.main if not f['pitched'] and f['n'] >= 0.05 * R.n_valid})
    # confidence
    n_term = min(1.0, R.n_valid / 60.0)
    score = R.explained_frac * (0.5 + 0.5 * n_term) * (1 - 0.6 * R.veg_frac)
    if R.type == 'complex':
        score *= 0.85
    if R.type == 'flat' and R.frac_flat > 0.9:
        score = min(1.0, score * 1.1)
    score = max(0.0, min(1.0, score))
    conf = 'high' if score >= 0.7 else ('medium' if score >= 0.45 else 'low')
    out = dict(base, source='lidar_2014', type=R.type,
               ridge_azimuth_deg=ridge_az,
               pitch_deg=r2(pitch, 1),
               eave_h_m=r2(e - ground), ridge_h_m=r2(r - ground),
               eave_z=r2(e), ridge_z=r2(r),
               ridges=R.ridges, hips=R.hips, valleys=R.valleys,
               facets=facets, features=feats,
               confidence=conf, confidence_score=round(score, 2),
               stats={'n_px': R.n_valid, 'explained_frac': r2(R.explained_frac),
                      'veg_frac': r2(R.veg_frac), 'flat_frac': r2(getattr(R, 'frac_flat', None)),
                      'n_facets': len(R.facets)})
    if levels:
        out['flat_levels_h_m'] = levels
    if getattr(R, 'deck', None) is not None and R.type != 'flat':
        out['form'] = 'deck'
        out['deck_h_m'] = r2(R.deck['h_med'])
        out['deck_facet'] = R.deck['id']
    if R.type == 'flat':
        out['edges'] = out['ridges']   # step / deck edges on a flat roof, not ridges
        out['ridges'] = []
    for s in out['ridges'] + out['hips'] + out['valleys'] + out.get('edges', []):
        s.pop('facets', None)
    return out, R


# ======================================================================== QA
TYPE_COL = {'flat': '#4c78a8', 'gabled': '#e45756', 'hipped': '#f58518', 'shed': '#b279a2',
            'complex': '#54a24b', 'estimate': '#9d9d9d'}


def qa_map(blds, roofs, path, extent=None, dpi=200, label=False):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MP
    from PIL import Image
    hs = np.asarray(Image.open(os.path.join(ROOT, 'world/lidar/hillshade.png')))
    x0, x1, y0, y1 = extent or (0, W, 0, H)
    fig_w = 14
    fig, ax = plt.subplots(figsize=(fig_w, fig_w * (y1 - y0) / (x1 - x0) + 0.6))
    ax.imshow(hs, cmap='gray', extent=(0, W, 0, H), vmin=0, vmax=300, interpolation='bilinear')
    for b in blds:
        r = roofs[b['osm_id']]
        key = 'estimate' if r['source'] == 'estimate' else r['type']
        alpha = {'high': 0.8, 'medium': 0.6, 'low': 0.35}[r['confidence']]
        ax.add_patch(MP(b['ring'], closed=True, fc=TYPE_COL[key], ec='k', lw=0.4, alpha=alpha))
        for s in r['ridges']:
            ax.plot([s['p0'][0], s['p1'][0]], [s['p0'][1], s['p1'][1]],
                    color='#ffff33' if s.get('kind', 'ridge') == 'ridge' else '#33e0ff', lw=1.6,
                    solid_capstyle='round')
        for s in r.get('hips', []):
            ax.plot([s['p0'][0], s['p1'][0]], [s['p0'][1], s['p1'][1]], color='#ffffff', lw=0.7)
        for f in r['features']:
            if f['type'] in ('tower', 'chimney', 'dormer', 'mechanical', 'raised_block'):
                mk = {'tower': ('^', 7), 'chimney': ('s', 3), 'dormer': ('o', 3),
                      'mechanical': ('D', 3), 'raised_block': ('P', 4)}[f['type']]
                ax.plot(f['x'], f['y'], mk[0], ms=mk[1], mfc='k', mec='w', mew=0.5)
        if label and r['name'] and x0 <= b['ring'][0][0] <= x1 and y0 <= b['ring'][0][1] <= y1:
            c = Polygon(b['ring']).representative_point()
            ax.text(c.x, c.y, r['name'].replace(' Hall', '')[:18], fontsize=5.5, ha='center',
                    va='center', color='k',
                    bbox=dict(fc='w', ec='none', alpha=0.55, pad=0.4))
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect('equal')
    from matplotlib.lines import Line2D
    hl = [MP([[0, 0]], fc=c, ec='k', lw=0.4, label=k) for k, c in TYPE_COL.items()]
    hl += [Line2D([], [], color='#ffff33', lw=1.6, label='ridge'),
           Line2D([], [], color='#33e0ff', lw=1.6, label='deck edge'),
           Line2D([], [], color='w', lw=0.7, label='hip'),
           Line2D([], [], marker='^', ls='', mfc='k', mec='w', label='tower'),
           Line2D([], [], marker='s', ls='', ms=4, mfc='k', mec='w', label='chimney'),
           Line2D([], [], marker='o', ls='', ms=4, mfc='k', mec='w', label='dormer'),
           Line2D([], [], marker='D', ls='', ms=4, mfc='k', mec='w', label='mechanical')]
    ax.legend(handles=hl, loc='lower left', fontsize=7, ncol=4, framealpha=0.85)
    ax.set_title('Roof forms from 2014 lidar (fill opacity = confidence). World metres, north up.',
                 fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=dpi)
    plt.close(fig)


def profile_plot(b, r, R, path, hh):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    poly = Polygon(b['ring'])
    c = poly.centroid
    if r['ridges']:
        az = r['ridges'][0]['azimuth_deg']
        mid = r['ridges'][0]
        c = type(c)(0.5 * (mid['p0'][0] + mid['p1'][0]), 0.5 * (mid['p0'][1] + mid['p1'][1]))
        cut = az + 90
    else:
        mrr = list(poly.minimum_rotated_rectangle.exterior.coords)
        e0 = math.dist(mrr[0], mrr[1]); e1 = math.dist(mrr[1], mrr[2])
        a_, b_ = (mrr[0], mrr[1]) if e0 >= e1 else (mrr[1], mrr[2])
        cut = (math.degrees(math.atan2(b_[0] - a_[0], b_[1] - a_[1])) + 90) % 360
    ux, uy = math.sin(math.radians(cut)), math.cos(math.radians(cut))
    L = 0.5 * math.hypot(poly.bounds[2] - poly.bounds[0], poly.bounds[3] - poly.bounds[1]) + 6
    # pixels within 0.8 m of the section line, inside a window
    s_all = (R.X - c.x) * ux + (R.Y - c.y) * uy
    d_all = -(R.X - c.x) * uy + (R.Y - c.y) * ux
    sel = (np.abs(d_all) <= 0.8) & (np.abs(s_all) <= L) & np.isfinite(R.hh)
    s = s_all[sel]; z = R.hh[sel]
    lab = R.labels[sel]; inside = R.full[sel]
    fig, ax = plt.subplots(figsize=(8, 3.6))
    g = b['ground_mean_z']
    roof_pts = inside & (lab > 0)
    ax.scatter(s[~inside], z[~inside] - g, s=9, c='#7a9c5a', label='lidar outside footprint')
    ax.scatter(s[inside & (lab == 0)], z[inside & (lab == 0)] - g, s=12, c='#e45756', marker='x',
               label='inside, not on a facet (veg / feature / edge)')
    ax.scatter(s[roof_pts], z[roof_pts] - g, s=12, c='k', label='inside, on fitted facet')
    # fitted roof along the line: plane of the facet partition at each sample
    ss = np.linspace(-L, L, 600)
    xs, ys = c.x + ss * ux, c.y + ss * uy
    if R.facets:
        _, (ir, ic) = ndi.distance_transform_edt(R.labels == 0, return_indices=True)
        part = np.where(R.full, R.labels[ir, ic], 0)
        cols = np.floor(xs - R.c0).astype(int); rows = np.floor(H - ys - R.r0).astype(int)
        ok = (cols >= 0) & (rows >= 0) & (cols < R.shape[1]) & (rows < R.shape[0])
        zf = np.full_like(ss, np.nan)
        pid = {f['id']: f['p'] for f in R.facets}
        for k in np.nonzero(ok)[0]:
            fid = part[rows[k], cols[k]]
            if fid and Polygon(b['ring']).contains(type(c)(xs[k], ys[k])):
                p = pid[fid]
                zf[k] = p[0] * xs[k] + p[1] * ys[k] + p[2]
        ax.plot(ss, zf - g, color='#4c78a8', lw=2, label='fitted roof (facet planes)')
    ax.axhline(r['eave_h_m'], color='#f58518', ls='--', lw=1, label='eave %.1f m' % r['eave_h_m'])
    ax.axhline(r['ridge_h_m'], color='#b279a2', ls=':', lw=1.2, label='ridge %.1f m' % r['ridge_h_m'])
    ax.axhline(0, color='0.5', lw=0.8)
    ax.set_xlabel('distance along section (m), section bearing %.0f deg' % (cut % 360))
    ax.set_ylabel('height above ground_mean_z (m)')
    ax.set_title('%s  %s: %s, pitch %s deg, conf %s' % (
        b['name'], b['osm_id'], r['type'], r['pitch_deg'], r['confidence']), fontsize=9)
    ax.set_aspect('equal', adjustable='datalim')
    ax.legend(fontsize=6.5, loc='upper center', bbox_to_anchor=(0.5, -0.22), ncol=3, frameon=False)
    fig.set_size_inches(8, 4.4)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


PROFILES = ['way/39516532', 'way/40877451', 'way/142983685', 'way/119843553', 'way/40877485',
            'way/339603988']


# ====================================================================== styles
STYLE_DEFAULTS = {  # inferred family defaults, used only where a row has no sourced value
    'collegiate_gothic_brick': ('red brick, limestone dressings', 'slate or tile, steep', 'limestone', 'leaded casement, stone mullions'),
    'arts_crafts_house': ('wood shingle / clapboard', 'composition shingle', 'painted wood', 'multi-pane double-hung'),
    'midcentury_residence': ('brick and wood siding', 'low-pitch or flat membrane', 'painted wood', 'ribbon / aluminium sliding'),
    'midcentury_academic': ('brick, concrete', 'flat membrane', 'concrete', 'steel ribbon'),
    'modern_residence': ('brick veneer, fibre-cement / metal panel', 'standing-seam metal or membrane', 'metal', 'aluminium-clad casement'),
    'contemporary_academic': ('brick, glass curtain wall', 'membrane / metal', 'metal', 'aluminium curtain wall'),
    'small_house': ('wood siding', 'asphalt shingle', 'painted wood', 'double-hung'),
    'utility': ('metal panel / concrete block', 'metal or membrane', 'none', 'few, industrial'),
    'unknown': ('', '', '', ''),
}
STYLE_COLS = ['osm_id', 'name', 'style', 'year_built', 'architect', 'wall_material', 'roof_material', 'trim',
              'window_type', 'source', 'confidence', 'notes']


def build_styles(blds):
    import csv
    raw = {}
    with open(os.path.join(ROOT, 'data/raw/building_styles.csv'), newline='') as f:
        for row in csv.DictReader(f):
            raw[row['osm_id']] = row
    out = []
    for b in blds:
        if b['osm_id'] in raw:
            r = dict(raw[b['osm_id']])
            d = STYLE_DEFAULTS[r['style']]
            filled = []
            for k, v in zip(('wall_material', 'roof_material', 'trim', 'window_type'), d):
                if not r[k] and v:
                    r[k] = v
                    filled.append(k)
            if filled:
                r['notes'] = (r['notes'] + ' ' if r['notes'] else '') + \
                    '[%s = family default, inferred]' % ', '.join(filled)
        else:
            tag = b['building']
            small = b['area_m2'] < 40 if b['realm'] != 'campus' else b['area_m2'] < 60
            if tag in ('garage', 'garages', 'shed', 'roof', 'service', 'retail', 'carport') or small \
                    or b['area_m2'] > 800:
                st, why = 'utility', 'rule: OSM building=%s, %.0f m2 -> utility' % (tag, b['area_m2'])
            else:
                st, why = 'small_house', 'rule: unnamed %s building=%s, %.0f m2 -> small_house' % (
                    b['realm'], tag, b['area_m2'])
            d = STYLE_DEFAULTS[st]
            r = {'osm_id': b['osm_id'], 'name': b['name'] or '', 'style': st, 'year_built': '',
                 'architect': '', 'wall_material': d[0], 'roof_material': d[1], 'trim': d[2],
                 'window_type': d[3], 'source': 'rule', 'confidence': 'low',
                 'notes': why + '; materials = family default, inferred'}
        out.append(r)
    with open(os.path.join(OUT, 'styles.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=STYLE_COLS)
        w.writeheader()
        for r in out:
            w.writerow({k: r.get(k, '') for k in STYLE_COLS})
    missing = [b['osm_id'] for b in blds if b['name'] and b['osm_id'] not in raw]
    from collections import Counter
    return Counter(r['style'] for r in out), out, missing


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.join(OUT, 'profiles'), exist_ok=True)
    blds = json.load(open(os.path.join(ROOT, 'world/3d/buildings.json')))['buildings']
    hh, can = load()
    _, _, rough = roughness(hh)
    roofs, keep = {}, {}
    for b in blds:
        r, R = process(b, hh, can, rough)
        roofs[b['osm_id']] = r
        if b['osm_id'] in PROFILES:
            keep[b['osm_id']] = R
    from collections import Counter
    cnt = Counter(('estimate' if r['source'] == 'estimate' else r['type']) for r in roofs.values())
    cnt_c = Counter(('estimate' if r['source'] == 'estimate' else r['type'])
                    for r in roofs.values() if r['realm'] == 'campus')
    doc = {
        'generator': 'scripts/roofs.py',
        'generated': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'source_data': 'DOGAMI 2014 OLC Metro lidar, highest hit 1 m (data/raw/lidar/highest_hit_utm10n_1m.tif)',
        'frame': 'world metres: x east, y north; z = elevation NAVD88. *_h_m are heights above ground_z '
                 '(= ground_mean_z in world/3d/buildings.json).',
        'conventions': {
            'azimuth': 'compass bearing, degrees clockwise from north; ridge azimuths folded to [0,180)',
            'aspect_deg': 'downslope direction of a facet, compass bearing',
            'plane': 'z = a*x + b*y + c (world metres, z elevation)',
            'facet.outline': 'facet region = footprint partitioned by nearest fitted pixel, simplified 0.4 m',
            'pitch_deg': 'area-weighted mean slope of the pitched facets (flat: of the flat facets)',
            'eave_h_m': 'pitched: facet planes evaluated at the footprint edge; flat: main roof level',
            'ridge_h_m': 'highest ridge line (or p97 of pitched roof points if no ridge line); flat: highest '
                         'major flat level',
        },
        'types': {
            'flat': '>= 75 % of roof points on facets < 8 deg (flat_levels_h_m lists levels)',
            'gabled': 'two opposite pitched facets carry >= 85 % of pitched points',
            'hipped': '3-4 major pitched facets, >= 3 aspect directions, <= 1 ridge, >= 1 hip line, '
                      'footprint fills > 85 % of its oriented box',
            'shed': 'one pitched facet carries >= 85 % of pitched points',
            'complex': 'everything else; see facets / ridges / hips / valleys',
        },
        'features': 'tower (> 4 m above roof, >= 9 m2, or a small facet > 4 m above main roof), chimney '
                    '(<= 6 m2), dormer (<= 40 m2 on a pitched facet, <= 3.5 m), mechanical (on flat roof), '
                    'raised_block (other), cross_gable (secondary ridge > 60 deg off the main ridge). '
                    '1 m lidar: chimneys and dormers are indicative only.',
        'vegetation_rule': 'pixels with 3x3 detrended roughness > 1 m excluded; unexplained blobs > 1 m above '
                           'the roof that touch canopy outside the footprint, or are rough (median r3 > 0.6 m, '
                           '> 6 m2) are vegetation, not structure',
        'confidence': 'score = explained_frac * (0.5 + 0.5*min(1, n_px/60)) * (1 - 0.6*veg_frac), x0.85 for '
                      'complex; high >= 0.7, medium >= 0.45, else low',
        'estimate_rule': 'buildings absent from the 2014 lidar (lidar_2014_absent) or with < 5 returns: '
                         'see each record\'s `rule` (>= 600 m2 flat; else 30 deg gable along the long axis)',
        'counts': dict(cnt), 'counts_campus': dict(cnt_c),
        'count': len(roofs),
        'roofs': [roofs[b['osm_id']] for b in blds],
    }
    with open(os.path.join(OUT, 'roofs.json'), 'w') as f:
        json.dump(doc, f, separators=(',', ':'))
    print('roofs.json', len(roofs), dict(cnt), 'campus', dict(cnt_c), '%.1fs' % (time.time() - t0))
    qa_map(blds, roofs, os.path.join(OUT, 'roofs_qa.png'), dpi=190)
    qa_map(blds, roofs, os.path.join(OUT, 'roofs_qa_campus.png'), extent=(330, 1000, 300, 800),
           dpi=170, label=True)
    byid = {b['osm_id']: b for b in blds}
    for oid in PROFILES:
        if keep.get(oid) is None:
            continue
        b = byid[oid]
        nm = (b['name'] or oid).lower().replace(' ', '_').replace('.', '')
        profile_plot(b, roofs[oid], keep[oid], os.path.join(OUT, 'profiles', nm + '.png'), hh)
    cnt_s, rows, missing = build_styles(blds)
    print('styles.csv', dict(cnt_s), 'named without curated row:', missing)
    ready = {
        'ready': True, 'generated': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'generator': 'scripts/roofs.py',
        'files': {
            'roofs.json': {'count': len(roofs), 'desc': 'roof form per building (osm_id), facets with planes and '
                           'outlines, ridges/deck edges/hips/valleys, features, eave/ridge heights'},
            'styles.csv': {'count': len(rows), 'desc': 'style family + materials per building (osm_id)'},
            'roofs_qa.png': {}, 'roofs_qa_campus.png': {}, 'profiles/': {},
        },
        'roof_counts': dict(cnt), 'roof_counts_campus': dict(cnt_c), 'style_counts': dict(cnt_s),
        'notes': ['Join on osm_id with world/3d/buildings.json.',
                  'Heights *_h_m are above ground_mean_z; *_z are NAVD88 elevations.',
                  'ridges[].kind = ridge | deck_edge (edge of a flat deck atop steep slopes, e.g. Eliot Hall); '
                  'roofs with a deck have form = deck and deck_h_m.',
                  'type flat: step/deck edges are in `edges`, `ridges` is empty.',
                  'source = estimate rows carry a `rule` string.'],
    }
    with open(os.path.join(OUT, 'ROOFS_READY.json'), 'w') as f:
        json.dump(ready, f, indent=1)
    print('done %.1fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
