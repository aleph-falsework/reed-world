# For every signposted building: does its OSM footprint land on ROOF/FACADE metatiles in its scene,
# and is its sign near it? Also draws OSM outlines over three scene backgrounds.
import json, glob, math
from PIL import Image, ImageDraw
from shapely.geometry import shape, box, Point
gj = json.load(open("world/lowfi/reed_world.geojson"))
F = {f["properties"]["id"]: f for f in gj["features"]}
spec = json.load(open("world/gb/project_spec.json"))
rows = []
for sc in spec["scenes"]:
    sid = sc["id"]; m = json.load(open(f"world/gb/{sid}/meta.json"))
    x0, y0, x1, y1 = m["world_box_m"]; cls = m["metatile_classes"]
    for a in sc["actors"]:
        f = F[a["id"]]; g = shape(f["geometry"])
        if g.geom_type == "Point":
            rows.append((sid, a["name"], a["id"], "point", "", "")); continue
        inside = g.intersection(box(x0, y0, x1, y1)).area / g.area
        # metatile coverage: metatiles whose centre is inside the polygon
        hit = tot = 0
        for my, r in enumerate(cls):
            for mx, c in enumerate(r):
                cx, cy = x0 + mx * 6 + 3, y1 - my * 6 - 3
                if g.contains(Point(cx, cy)):
                    tot += 1; hit += c in ("ROOF", "FACADE", "DOOR")
        # sign distance (metres) from sign centre to polygon
        sx, sy = x0 + (a["x"] + 1) * 3, y1 - (a["y"]) * 3
        dist = g.distance(Point(sx, sy))
        rows.append((sid, a["name"], a["id"], f"{inside:.0%} in scene", f"{hit}/{tot} footprint metatiles drawn as building", f"sign {dist:.0f} m from footprint"))
for r in rows: print(r)

def overlay(sid, names):
    m = json.load(open(f"world/gb/{sid}/meta.json")); x0, y0, x1, y1 = m["world_box_m"]
    im = Image.open(f"world/gb/{sid}/background.png").convert("RGB")
    d = ImageDraw.Draw(im)
    sc = [s for s in spec["scenes"] if s["id"] == sid][0]
    for a in sc["actors"]:
        g = shape(F[a["id"]]["geometry"])
        if g.geom_type != "Polygon": continue
        pts = [((x - x0) / 3 * 8, (y1 - y) / 3 * 8) for x, y in g.exterior.coords]
        d.line(pts, fill=(255, 0, 0), width=2)
        c = g.representative_point(); d.text(((c.x - x0) / 3 * 8, (y1 - c.y) / 3 * 8), a["name"][:14], fill=(255, 0, 0))
    im.save(f"build/review/overlay_{sid}.png")
for s in ("old_campus", "science_quad", "west_campus", "the_grove"):
    overlay(s, None)
