# Independent limit count, reads game/reed-world directly (not the spec).
import json, glob, os, sys
from PIL import Image
import numpy as np
root = "game/reed-world"
DMG = {(0xe0,0xf8,0xcf),(0x86,0xc0,0x6c),(0x30,0x68,0x50),(0x07,0x18,0x21)}
print("== backgrounds ==")
for p in sorted(glob.glob(root+"/assets/backgrounds/*.png")):
    im = Image.open(p); mode = im.mode
    a = np.array(im.convert("RGBA"))
    h, w = a.shape[:2]
    cols = {tuple(c) for c in a.reshape(-1,4)}
    rgb = {c[:3] for c in cols}; alpha = {c[3] for c in cols}
    exact=set(); flip=set()
    for y in range(0,h,8):
        for x in range(0,w,8):
            t = a[y:y+8,x:x+8,:3]
            k = t.tobytes(); exact.add(k)
            vs = [t, t[:,::-1], t[::-1,:], t[::-1,::-1]]
            flip.add(min(v.tobytes() for v in vs))
    print(f"{os.path.basename(p):22s} {w}x{h} mode={mode} colours={len(rgb)} ok={rgb<=DMG} alpha={sorted(alpha)} mult8={w%8==0 and h%8==0} exact={len(exact)} flipcanon={len(flip)}")
print("== sprites (project + art) ==")
for p in sorted(glob.glob(root+"/assets/sprites/*.png")+glob.glob("art/sprites/*.png")+glob.glob(root+"/assets/ui/*.png")+glob.glob(root+"/assets/fonts/*.png")):
    a = np.array(Image.open(p).convert("RGBA"))
    opaque = {tuple(c[:3]) for c in a.reshape(-1,4) if c[3]>=200}
    semi = sorted({int(c[3]) for c in a.reshape(-1,4) if 0<c[3]<255})
    print(f"{p:50s} {a.shape[1]}x{a.shape[0]} opaque_colours={sorted('#%02x%02x%02x'%c for c in opaque)} semi_alpha={semi[:5]}")
print("== scenes ==")
bgs = {}
for p in glob.glob(root+"/assets/backgrounds/*.gbsres"):
    d=json.load(open(p)); bgs[d["id"]]=d
for sd in sorted(glob.glob(root+"/project/scenes/*/")):
    s = json.load(open(sd+"scene.gbsres"))
    na = len(glob.glob(sd+"actors/*.gbsres")); nt = len(glob.glob(sd+"triggers/*.gbsres"))
    single = s["width"]<=20 and s["height"]<=18
    bg = bgs.get(s["backgroundId"],{})
    print(f"{s['name']:32s} {s['width']}x{s['height']} actors={na} (lim {10 if single else 20}) triggers={nt} (lim 30) bg={bg.get('filename')} bgsize={bg.get('width')}x{bg.get('height')} match={bg.get('width')==s['width'] and bg.get('height')==s['height']}")
