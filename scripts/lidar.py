"""Derive lidar layers for the Reed world from DOGAMI's 2014 OLC Metro survey.

Inputs (already reprojected to UTM 10N at 1 m and aligned to the world frame;
raster origin = world NW corner, 1274 x 811 px, so pixel (row r, col c) covers
world x in [c, c+1), y in [811-r-1, 811-r)):
    data/raw/lidar/bare_earth_utm10n_1m.tif
    data/raw/lidar/highest_hit_utm10n_1m.tif
    data/raw/lidar/intensity_utm10n_1m.tif
    world/lowfi/reed_world.geojson (also updated in place: height_lidar_m per building)

Outputs in world/lidar/: ndsm.tif, canopy.tif, slope_deg.tif, hillshade.png,
surface_1m.tif, surface_legend.json, surface_preview.png, buildings_lidar.json.

Usage: python3 scripts/lidar.py   (run from the repo root, or pass the repo root as argv[1])
Needs: numpy, rasterio, scipy, scikit-image, shapely, Pillow.
"""
import json, os, sys
import numpy as np
import rasterio
from rasterio.features import rasterize
from affine import Affine
from scipy import ndimage as ndi
from shapely.geometry import shape, mapping
from shapely.ops import unary_union
from PIL import Image, ImageDraw

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
RAW = os.path.join(ROOT, 'data/raw/lidar')
OUT = os.path.join(ROOT, 'world/lidar')
WORLD = os.path.join(ROOT, 'world/lowfi/reed_world.geojson')
os.makedirs(OUT, exist_ok=True)

# ---------------- thresholds (documented in world/lidar/README.md) ----------------
CANOPY_MIN_M = 2.5          # nDSM above this, outside buildings, = tree canopy
BUILDING_BUFFER_M = 1.0     # footprints grown by this before masking canopy
STEEP_DEG = 25.0            # bare-earth slope above this = steep (ledge / cliff)
ROAD_HALF_M = 3.0           # streets -> ~6 m wide
PATH_HALF_M = 1.25          # footway / path / steps / pedestrian / cycleway -> ~2.5 m
SERVICE_HALF_M = 2.0        # service roads -> ~4 m
STREAM_HALF_M = 1.0         # waterway lines -> ~2 m wide channel
LEVEL_M = 3.5               # OSM building:levels x this = expected height
ABSENT_FRAC = 0.35          # < this share of footprint above 2 m = building absent in 2014

# ---------------- load rasters ----------------
def read(name):
    with rasterio.open(os.path.join(RAW, name)) as r:
        return r.read(1), r.profile, r.transform
be, prof, tr = read('bare_earth_utm10n_1m.tif')
hh, _, _ = read('highest_hit_utm10n_1m.tif')
it, _, _ = read('intensity_utm10n_1m.tif')
H, W = be.shape
valid = np.isfinite(be) & np.isfinite(hh)
# The intensity raster's nodata is 0 inside the survey, but the area east of the survey
# edge (x > ~1136 m) is filled with 255, not 0. Treat both as nodata.
it_valid = valid & (it > 0) & ~((it == 255) & ~valid)

# world-frame affine: world x = col, world y = H - row
WT = Affine(1, 0, 0, 0, -1, H)

def burn(geoms, all_touched=False):
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return np.zeros((H, W), bool)
    return rasterize(((mapping(g), 1) for g in geoms), out_shape=(H, W), transform=WT,
                     all_touched=all_touched, fill=0, dtype='uint8').astype(bool)

def save_tif(name, arr, dtype, nodata):
    p = dict(prof); p.update(dtype=dtype, nodata=nodata, count=1, compress='deflate')
    with rasterio.open(os.path.join(OUT, name), 'w', **p) as dst:
        dst.write(arr.astype(dtype), 1)

world = json.load(open(WORLD))
F = world['features']
K = lambda *ks: [f for f in F if f['properties']['kind'] in ks]
G = lambda f: shape(f['geometry'])

# ---------------- nDSM ----------------
ndsm = np.where(valid, hh - be, np.nan).astype('float32')
save_tif('ndsm.tif', ndsm, 'float32', np.nan)
nd0 = np.nan_to_num(np.clip(ndsm, 0, None))

# ---------------- building heights ----------------
# 3x3 std of the highest-hit DSM: high over tree crowns, low over roofs. Used to flag tree overhang.
ROUGH = ndi.generic_filter(np.nan_to_num(np.where(valid, hh, 0.0)), np.std, size=3)
buildings = K('building')
report = []
for f in buildings:
    g = G(f)
    m = burn([g])
    if m.sum() < 4:                      # tiny footprints: take any touched pixel
        m = burn([g], all_touched=True)
    vals = ndsm[m & valid]
    p = f['properties']
    rec = {'id': p['id'], 'name': p.get('name'), 'realm': p.get('realm'), 'area_m2': round(g.area, 1),
           'levels': p.get('building:levels'), 'height_osm': p.get('height'), 'px': int(m.sum()), 'px_valid': int(vals.size)}
    if vals.size == 0:
        p['height_lidar_m'] = None
        rec['height_lidar_m'] = None
    else:
        h90 = float(np.percentile(vals, 90))
        rec['height_lidar_p50_m'] = round(float(np.percentile(vals, 50)), 1)
        rec['rough_frac'] = round(float((ROUGH[m & valid] > 0.5).mean()), 2)
        p['height_lidar_m'] = round(max(h90, 0.0), 1)
        rec['height_lidar_m'] = p['height_lidar_m']
        rec['frac_above_2m'] = round(float((vals > 2.0).mean()), 2)
        if vals.size >= 20 and rec['frac_above_2m'] < ABSENT_FRAC:
            p['lidar_2014_absent'] = True   # footprint is open ground in 2014: built later (or mis-drawn)
            rec['absent_2014'] = True
    report.append(rec)

# disagreements with OSM levels x 3.5 m (and explicit height)
disagree = []
for r in report:
    if r['height_lidar_m'] is None or r.get('absent_2014'):
        continue
    for src, exp in (('levels', float(r['levels']) * LEVEL_M if r['levels'] else None),
                     ('height', float(str(r['height_osm']).split()[0]) if r['height_osm'] else None)):
        if exp is None:
            continue
        d = r['height_lidar_m'] - exp
        if abs(d) > max(5.0, 0.5 * exp):
            disagree.append({'id': r['id'], 'name': r['name'], 'realm': r['realm'], 'osm': src, 'expected_m': round(exp, 1),
                             'lidar_p90_m': r['height_lidar_m'], 'lidar_p50_m': r['height_lidar_p50_m'], 'diff_m': round(d, 1),
                             'rough_frac': r['rough_frac'],
                             'likely_cause': 'tree overhang (median roof agrees with OSM)' if abs(r['height_lidar_p50_m'] - exp) <= max(3.0, 0.35 * exp)
                                             else 'OSM tag low or tall storeys (median roof also disagrees)'})

# ---------------- canopy ----------------
bld_geoms = [G(f) for f in buildings]
bld_mask = burn(bld_geoms)
bld_buf = burn([g.buffer(BUILDING_BUFFER_M) for g in bld_geoms])
canopy = (nd0 > CANOPY_MIN_M) & valid & ~bld_buf
st = ndi.generate_binary_structure(2, 1)
canopy = ndi.binary_opening(canopy, st)
canopy = ndi.binary_closing(canopy, st) & ~bld_buf & valid
lab, n = ndi.label(canopy)
sizes = ndi.sum(canopy, lab, range(1, n + 1))
canopy &= np.isin(lab, 1 + np.flatnonzero(sizes >= 4))
save_tif('canopy.tif', canopy, 'uint8', 255)

# Planar raised objects outside OSM footprints (structures missing from OSM, or since demolished):
# canopy components whose surface is smooth (low 3x3 roughness of the highest-hit DSM).
hh0 = np.where(valid, hh, np.nan)
rough = ROUGH
lab, n = ndi.label(canopy)
unmapped = []
for i, sl in enumerate(ndi.find_objects(lab), start=1):
    comp = lab[sl] == i
    a = int(comp.sum())
    if a < 60:
        continue
    r_med = float(np.median(rough[sl][comp]))
    if r_med < 0.35:
        rows, cols = np.nonzero(comp)
        unmapped.append({'x': round(float(cols.mean() + sl[1].start) + 0.5, 1), 'y': round(H - float(rows.mean() + sl[0].start) - 0.5, 1),
                         'area_m2': a, 'ndsm_p90_m': round(float(np.percentile(nd0[sl][comp], 90)), 1), 'roughness_m': round(r_med, 2)})

# ---------------- slope + hillshade ----------------
be_f = be.copy()
if (~np.isfinite(be_f)).any():  # fill gaps with nearest valid value for gradient only
    idx = ndi.distance_transform_edt(~np.isfinite(be_f), return_distances=False, return_indices=True)
    be_f = be_f[tuple(idx)]
dzdy, dzdx = np.gradient(be_f, 1.0)
slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
slope_out = np.where(np.isfinite(be), slope, np.nan).astype('float32')
save_tif('slope_deg.tif', slope_out, 'float32', np.nan)

az, alt = np.radians(315.0), np.radians(45.0)
# rows run north->south, so north-facing gradient is -dzdy
aspect = np.arctan2(-dzdx, dzdy)
slp = np.arctan(np.hypot(dzdx, dzdy))
hs = np.sin(alt) * np.cos(slp) + np.cos(alt) * np.sin(slp) * np.cos(az - np.pi / 2 - aspect)
hs = np.clip(hs, 0, 1) * 255
hs = np.where(np.isfinite(be), hs, 0).astype('uint8')
Image.fromarray(hs).save(os.path.join(OUT, 'hillshade.png'))
steep = (slope > STEEP_DEG) & np.isfinite(be)

# ---------------- surface map ----------------
CLASSES = [  # value, name, colour (preview), meaning
    (0, 'nodata', '#000000', 'Outside the lidar survey and no OSM class'),
    (1, 'building', '#d7301f', 'OSM building footprint'),
    (2, 'water', '#2166ac', 'OSM water polygon or stream (~2 m)'),
    (3, 'road', '#404040', 'OSM street, ~6 m wide'),
    (4, 'path', '#f4c542', 'OSM footway/path/steps/pedestrian ~2.5 m, service road ~4 m'),
    (5, 'tree', '#1b7837', 'Lidar canopy: nDSM > 2.5 m outside footprints'),
    (6, 'steep', '#8c510a', 'Bare-earth slope > 25 deg (ledge / cliff)'),
    (7, 'paved', '#a6a6a6', 'Open low ground, low lidar intensity, or OSM parking'),
    (8, 'lawn', '#a6d96a', 'Open low ground, high lidar intensity, or OSM lawn / pitch'),
]
cls = dict((n, v) for v, n, _, _ in CLASSES)

water_g = [G(f) for f in K('water')] + [G(f).buffer(STREAM_HALF_M) for f in K('stream') if f['geometry']['type'] != 'Point']
road_g = [G(f).buffer(ROAD_HALF_M) for f in K('road')]
path_g = [G(f).buffer(PATH_HALF_M) for f in K('path')] + [G(f).buffer(SERVICE_HALF_M) for f in K('service_road')]
bridge_g = [G(f).buffer({'road': ROAD_HALF_M, 'service_road': SERVICE_HALF_M}.get(f['properties']['kind'], PATH_HALF_M))
            for f in F if f['properties'].get('bridge')]
water_m, road_m, path_m, bridge_m = burn(water_g), burn(road_g), burn(path_g), burn(bridge_g)
parking_m = burn([G(f) for f in K('parking') if f['geometry']['type'] != 'Point'])
lawn_osm = [f for f in F if f['geometry']['type'] in ('Polygon', 'MultiPolygon') and (
    f['properties'].get('leisure') in ('pitch', 'lawn') or f['properties'].get('landuse') == 'grass')]
lawn_osm_m = burn([G(f) for f in lawn_osm])

# --- calibrate the paved / lawn intensity threshold on known areas ---
it_s = ndi.median_filter(np.where(it_valid, it, 0), size=3).astype(float)
open_low = valid & it_valid & ~bld_buf & ~canopy & ~water_m & ~road_m & ~path_m & (nd0 < 1.0)
# South Lawn (the open lawn south of Eliot Hall): hand-picked box from the hillshade/intensity, world metres
SOUTH_LAWN = (470, 230, 720, 330)  # x0, y0, x1, y1
yy, xx = np.mgrid[0:H, 0:W]
wx, wy = xx + 0.5, H - yy - 0.5
south_lawn_m = (wx >= SOUTH_LAWN[0]) & (wx <= SOUTH_LAWN[2]) & (wy >= SOUTH_LAWN[1]) & (wy <= SOUTH_LAWN[3])
paved_ref = it_s[open_low & parking_m]
lawn_ref = it_s[open_low & (lawn_osm_m | south_lawn_m)]
cands = np.arange(10, 250)
bal = [0.5 * ((paved_ref < t).mean() + (lawn_ref >= t).mean()) for t in cands]
THRESH = int(cands[int(np.argmax(bal))])
calib = {
    'threshold_intensity': THRESH, 'balanced_accuracy': round(float(max(bal)), 3),
    'paved_ref_px': int(paved_ref.size), 'lawn_ref_px': int(lawn_ref.size),
    'paved_ref_percentiles_10_50_90': [float(np.percentile(paved_ref, q)) for q in (10, 50, 90)],
    'lawn_ref_percentiles_10_50_90': [float(np.percentile(lawn_ref, q)) for q in (10, 50, 90)],
    'south_lawn_box_xy': SOUTH_LAWN,
    'paved_ref_as_paved': round(float((paved_ref < THRESH).mean()), 3),
    'lawn_ref_as_lawn': round(float((lawn_ref >= THRESH).mean()), 3),
}

grass = it_s >= THRESH
grass = ndi.median_filter(grass.astype('uint8'), size=5).astype(bool)   # remove salt-and-pepper

surf = np.zeros((H, W), 'uint8')
rest = np.ones((H, W), bool)
def put(mask, name):
    global rest
    m = mask & rest
    surf[m] = cls[name]
    rest &= ~m
put(bld_mask, 'building')
put(bridge_m, 'path')            # bridges carry the path over water/canyon (deviation from strict order; see README)
put(water_m, 'water')
put(road_m, 'road')
put(path_m, 'path')
put(canopy, 'tree')
put(steep, 'steep')
put(parking_m, 'paved')          # OSM parking is paved even where lidar is missing
put(lawn_osm_m, 'lawn')          # OSM lawn / pitches are lawn even where lidar is missing
# Forest floor in the canyon and woods returns low intensity (litter, shade) and would read as paved;
# nothing there is paved except mapped paths, so call it lawn (soft ground).
natural_m = burn([G(f) for f in F if f['geometry']['type'] in ('Polygon', 'MultiPolygon') and (
    f['properties']['kind'] in ('woods', 'scrub') or f['properties'].get('name') == 'Reed Canyon')])
put(natural_m & valid, 'lawn')
put(valid & it_valid & ~grass, 'paved')
put(valid & it_valid & grass, 'lawn')
put(valid, 'lawn')               # valid elevation but no intensity: default lawn (rare)
save_tif('surface_1m.tif', surf, 'uint8', 0)

counts = {n: int((surf == v).sum()) for v, n, _, _ in CLASSES}
tot = surf.size
legend = {
    'raster': 'surface_1m.tif', 'size_px': [W, H], 'cell_m': 1,
    'frame': 'world frame (see reed_world.geojson meta); row 0 = north edge y=%d, col 0 = x=0' % H,
    'priority': ['building', 'water (bridges excepted)', 'road', 'path', 'tree', 'steep', 'paved/lawn', 'nodata'],
    'classes': [{'value': v, 'name': n, 'color': c, 'meaning': d, 'cells': counts[n], 'pct': round(100 * counts[n] / tot, 2)}
                for v, n, c, d in CLASSES],
    'thresholds': {'canopy_ndsm_m': CANOPY_MIN_M, 'building_buffer_m': BUILDING_BUFFER_M, 'steep_slope_deg': STEEP_DEG,
                   'road_width_m': 2 * ROAD_HALF_M, 'path_width_m': 2 * PATH_HALF_M, 'service_width_m': 2 * SERVICE_HALF_M,
                   'stream_width_m': 2 * STREAM_HALF_M, 'paved_lawn_intensity': THRESH},
    'intensity_calibration': calib,
    'source': 'DOGAMI 2014 OLC Metro lidar (bare earth, highest hit, intensity) + OpenStreetMap',
}
json.dump(legend, open(os.path.join(OUT, 'surface_legend.json'), 'w'), indent=1)

# ---------------- preview ----------------
pal = np.zeros((256, 3), 'uint8')
for v, n, c, _ in CLASSES:
    pal[v] = [int(c[i:i + 2], 16) for i in (1, 3, 5)]
rgb = pal[surf]
shade = (0.75 + 0.25 * hs / 255.0)[..., None]   # light relief so the canyon reads
rgb = (rgb * shade).astype('uint8')
SC = 2
img = Image.fromarray(rgb).resize((W * SC, H * SC), Image.NEAREST)
d = ImageDraw.Draw(img)
for f in buildings:
    g = G(f)
    for poly in (getattr(g, 'geoms', None) or [g]):
        d.line([(x * SC, (H - y) * SC) for x, y in poly.exterior.coords], fill=(60, 0, 0), width=1)
for f in K('entrance'):
    x, y = f['geometry']['coordinates']
    d.rectangle([x * SC - 2, (H - y) * SC - 2, x * SC + 2, (H - y) * SC + 2], fill=(255, 255, 255))
# legend
lx, ly = 8, 8
d.rectangle([lx - 4, ly - 4, lx + 120, ly + 14 * len(CLASSES)], fill=(255, 255, 255))
for i, (v, n, c, _) in enumerate(CLASSES):
    d.rectangle([lx, ly + 14 * i, lx + 10, ly + 14 * i + 10], fill=tuple(pal[v]))
    d.text((lx + 16, ly + 14 * i), '%d %s' % (v, n), fill=(0, 0, 0))
img.save(os.path.join(OUT, 'surface_preview.png'))

# ---------------- write back + report ----------------
json.dump(world, open(WORLD, 'w'), indent=1)
summary = {'disagreements': disagree,
           'absent_in_2014': [{'id': r['id'], 'name': r['name'], 'realm': r['realm'], 'frac_above_2m': r['frac_above_2m'],
                               'area_m2': r['area_m2']} for r in report if r.get('absent_2014')],
           'possible_unmapped_structures': unmapped,
           'buildings': report}
json.dump(summary, open(os.path.join(OUT, 'buildings_lidar.json'), 'w'), indent=1)
print(json.dumps({'counts_pct': {n: round(100 * counts[n] / tot, 2) for n in counts}, 'calibration': calib,
                  'disagreements_campus': [d for d in disagree if d['realm'] == 'campus'],
                  'disagreements_neigh': len([d for d in disagree if d['realm'] != 'campus']),
                  'disagreements_neigh_tree': len([d for d in disagree if d['realm'] != 'campus' and d['likely_cause'].startswith('tree')]), 'absent_campus': [a for a in summary['absent_in_2014'] if a['realm'] == 'campus'],
                  'absent_neigh': len([a for a in summary['absent_in_2014'] if a['realm'] != 'campus']),
                  'unmapped': unmapped}, indent=1))
