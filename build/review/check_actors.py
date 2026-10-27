# Cross-check actors in the GB Studio project against the GeoJSON and the CSV.
import json, glob, csv, re, os
gj = json.load(open("world/lowfi/reed_world.geojson"))
feat = {}
for f in gj["features"]:
    p = f["properties"]
    i = p.get("osm_id") or p.get("id") or f.get("id")
    feat[str(i)] = f
print("geojson features", len(feat), "sample keys", list(gj["features"][0]["properties"].keys())[:20])
rows = list(csv.DictReader(open("data/raw/reed_college_buildings.csv")))
def norm(s): return re.sub(r"[^a-z0-9]","",s.lower())
csvby = {}
for r in rows:
    for part in r["Building"].split("/"):
        csvby[norm(part)] = r
out=[]
for f in sorted(glob.glob("game/reed-world/project/scenes/*/actors/*.gbsres")):
    a = json.load(open(f)); scene = f.split("/")[-3]
    m = re.search(r"((?:way|node|relation)/\d+)", a["notes"]); oid = m.group(1) if m else None
    g = feat.get(oid)
    gname = g["properties"].get("name") if g else None
    text = " ".join(e["args"]["text"][i] for e in a["script"] if e["command"]=="EVENT_TEXT" for i in range(len(e["args"]["text"]))).replace("\n"," ")
    out.append((scene, a["name"], oid, g is not None, gname, a["x"], a["y"], a["notes"], text))
for o in out:
    print(o)
