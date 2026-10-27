"""Build the canonical Reed College WORLD layer from an Overpass GeoJSON export.

State only: every feature keeps its OSM id; geometry is reprojected from WGS84
to UTM zone 10N (EPSG:32610) and shifted so the SW corner of the world is (0,0)
in metres, x east, y north.
Usage: python3 build_world.py export.geojson buildings.csv out_dir
"""
import csv, json, sys, re
from shapely.geometry import shape, mapping, box
from shapely.ops import transform
from pyproj import Transformer

src, csv_path, out = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(src))
feats = data['features']
fwd = Transformer.from_crs(4326, 32610, always_xy=True).transform

def P(g):
    return transform(fwd, g)

campus_ll = next(shape(f['geometry']) for f in feats if f['properties'].get('@id') == 'way/27400683')
campus = P(campus_ll)
MARGIN = 100
minx, miny, maxx, maxy = campus.bounds
minx, miny, maxx, maxy = minx - MARGIN, miny - MARGIN, maxx + MARGIN, maxy + 15  # Overpass export stops ~20 m north of campus
frame = box(minx, miny, maxx, maxy)
W, H = maxx - minx, maxy - miny
local = lambda g: transform(lambda x, y, z=None: (round(x - minx, 2), round(y - miny, 2)), g)

# ---- CSV (the 2010 campus-map key + uses) and name matching to OSM ----
rows = list(csv.DictReader(open(csv_path, encoding='utf-8-sig')))
ALIAS = {  # CSV building -> OSM name(s)
    'Theatre Annex / Reed Warehouse': ['Reed Warehouse'],
    'Health & Counseling Center': ['Health and Counseling Center'],
    'Scholz': ['Scholz Hall'], 'Foster': ['Foster Hall'], 'MacNaughton': ['MacNaughton Hall'],
    'Prexy': ['Prexy Hall'], 'Sports Center': ['Reed College Sports Center'],
    'Anna Mann': ['Anna Mann Hall'], 'Griffin': ['Griffin Hall'], 'McKinley': ['McKinley Hall'],
    'Woodbridge': ['Woodbridge Hall'], 'Library (Hauser)': ['Eric V. Hauser Memorial Library'],
    'Greywood': ['Greywood Hall'], 'Educational Technology Center (ETC)': ['Educational Technology Center'],
    'Studio Art': ['Studio Arts'], 'Trillium (not on this map)': ['Trillium'],
    'Performing Arts Building (not on this map)': ['Performing Arts Building'],
}
csv_by_osmname = {}
for r in rows:
    for n in ALIAS.get(r['Building'], [r['Building']]):
        csv_by_osmname[n] = r

def kind_of(p, gtype):
    if 'entrance' in p and gtype == 'Point': return 'entrance'  # RW-7
    if 'building' in p: return 'building'
    hw = p.get('highway')
    if hw in ('footway', 'path', 'pedestrian', 'steps', 'cycleway', 'track'): return 'path'
    if hw in ('service',): return 'service_road'
    if hw and gtype != 'Point': return 'road'
    if p.get('waterway'): return 'stream'
    if p.get('natural') == 'water': return 'water'
    if p.get('landuse') == 'forest' or p.get('natural') in ('wood',): return 'woods'
    if p.get('natural') == 'scrub': return 'scrub'
    if p.get('leisure') == 'pitch' or p.get('leisure') == 'sports_centre': return 'field'
    if p.get('amenity') == 'parking': return 'parking'
    if p.get('leisure') in ('park', 'garden', 'dog_park', 'playground') or p.get('landuse') == 'grass' or p.get('leisure') == 'lawn': return 'green'
    if p.get('amenity') == 'university': return 'campus_boundary'
    if p.get('leisure') == 'golf_course': return 'golf'
    if p.get('natural') == 'tree' and p.get('name'): return 'landmark_tree'
    if p.get('natural') == 'tree' and gtype == 'Point': return 'tree'  # RW-7: unnamed trees
    if p.get('amenity') == 'theatre': return 'venue'
    return None

world, used_csv, skipped = [], set(), {'power': 0, 'route_relations': 0, 'invalid': [], 'other': 0}
for f in feats:
    p = f['properties']; fid = p['@id']
    if 'route' in p: skipped['route_relations'] += 1; continue
    if 'power' in p or 'generator:source' in p: skipped['power'] += 1; continue
    try:
        g = P(shape(f['geometry']))
    except Exception:
        skipped['invalid'].append(fid); continue
    k = kind_of(p, f['geometry']['type'])
    if not k: skipped['other'] += 1; continue
    if not g.is_valid: g = g.buffer(0)
    if not g.intersects(frame): continue
    g = g.intersection(frame)
    if g.is_empty: continue
    props = {'id': fid, 'kind': k}
    if p.get('name'): props['name'] = p['name']
    for t in ('building', 'highway', 'building:levels', 'height', 'leisure', 'landuse', 'natural', 'amenity', 'surface', 'sport'):
        if t in p: props[t] = p[t]
    if p.get('bridge') == 'yes' and k in ('path', 'road', 'service_road'):  # RW-7
        props['bridge'] = True
        if 'layer' in p: props['layer'] = p['layer']
    if k == 'entrance':  # RW-7
        for t in ('entrance', 'door', 'wheelchair', 'access', 'ref', 'level', 'addr:housenumber', 'addr:street'):
            if t in p: props[t] = p[t]
    if k == 'tree':  # RW-7
        for t in ('genus', 'species', 'leaf_type', 'leaf_cycle', 'denotation', 'circumference', 'height'):
            if t in p: props[t] = p[t]
    if k == 'building':
        c = P(shape(f['geometry'])).representative_point()
        on_campus = campus.buffer(5).contains(c)
        r = csv_by_osmname.get(p.get('name', ''))
        props['realm'] = 'campus' if (on_campus or r) else 'neighborhood'
        if r:
            used_csv.add(r['Building'])
            props.update({'map_no': r['Map #'], 'category': r['Category'], 'use': r['What happens there']})
            if r['Changes since 2010 map']: props['changes'] = r['Changes since 2010 map']
    if k in ('venue',) or (k == 'green' and p.get('name') == 'Eliot Circle'):
        r = csv_by_osmname.get(p.get('name', ''))
        if r:
            used_csv.add(r['Building'])
            props.update({'map_no': r['Map #'], 'category': r['Category'], 'use': r['What happens there']})
    world.append({'type': 'Feature', 'id': fid, 'properties': props, 'geometry': mapping(local(g))})

# ---- RW-7: link each entrance to the building it touches or is nearest to (<= 3 m) ----
ENTRANCE_LINK_M = 3.0
_bld = [(f, shape(f['geometry'])) for f in world if f['properties']['kind'] == 'building']
for f in world:
    if f['properties']['kind'] != 'entrance': continue
    pt = shape(f['geometry'])
    best = min(((bg.boundary.distance(pt) if not bg.contains(pt) else 0.0, bf) for bf, bg in _bld), key=lambda t: t[0], default=None)
    if best and best[0] <= ENTRANCE_LINK_M:
        f['properties']['building_id'] = best[1]['id']
        f['properties']['building_dist_m'] = round(best[0], 2)
    else:
        f['properties']['building_id'] = None

meta = {
    'title': 'Reed College — world layer (state only)',
    'crs': 'EPSG:32610 (UTM 10N), shifted: x = E - %.2f, y = N - %.2f, metres' % (minx, miny),
    'origin_utm': [minx, miny], 'size_m': [round(W, 2), round(H, 2)],
    'source': 'OpenStreetMap (Overpass export) + reed_college_buildings.csv',
    'credit': '© OpenStreetMap contributors (ODbL)',
}
json.dump({'type': 'FeatureCollection', 'meta': meta, 'features': world}, open(f'{out}/reed_world.geojson', 'w'), indent=1)

# ---- gaps report data ----
camp_b = [f for f in world if f['properties']['kind'] == 'building' and f['properties']['realm'] == 'campus']
gaps = {
    'csv_not_in_osm': [r['Map #'] + ' ' + r['Building'] for r in rows if r['Building'] not in used_csv],
    'osm_named_not_in_csv': sorted({f['properties']['name'] for f in camp_b if 'name' in f['properties'] and 'map_no' not in f['properties']}),
    'campus_buildings_total': len(camp_b),
    'campus_buildings_unnamed': [f['id'] for f in camp_b if 'name' not in f['properties']],
    'buildings_with_levels': sum(1 for f in camp_b if 'building:levels' in f['properties']),
    'buildings_with_height': sum(1 for f in camp_b if 'height' in f['properties']),
    'neighborhood_buildings': sum(1 for f in world if f['properties']['kind'] == 'building' and f['properties']['realm'] == 'neighborhood'),
    'skipped': skipped,
    'entrances': sum(1 for f in world if f['properties']['kind'] == 'entrance'),
    'entrances_unlinked': [f['id'] for f in world if f['properties']['kind'] == 'entrance' and not f['properties'].get('building_id')],
    'bridges': [f['id'] for f in world if f['properties'].get('bridge')],
    'trees': sum(1 for f in world if f['properties']['kind'] == 'tree'),
    'named_campus_buildings_without_entrance': sorted({f['properties']['name'] for f in camp_b if 'name' in f['properties']} - {
        b['properties'].get('name') for b in camp_b if any(e['properties'].get('building_id') == b['id'] for e in world if e['properties']['kind'] == 'entrance')} - {None}),
    'world_size_m': meta['size_m'],
}
json.dump(gaps, open(f'{out}/gaps.json', 'w'), indent=1)
print(json.dumps(gaps, indent=1))
