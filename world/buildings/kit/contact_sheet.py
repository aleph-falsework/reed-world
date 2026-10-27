#!/usr/bin/env python3
"""Contact sheet of the kit: each material tiled 2 x 2 (to show seams), lit with its normal map from the
upper left, labelled with its real-world tile size. Optional module renders (out/preview/modules_*.png)
are appended. Writes world/buildings/kit/contact_sheet.png."""
import glob
import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
TEX = os.path.join(HERE, 'textures')
PREV = os.path.join(os.path.dirname(HERE), 'out', 'preview')
CELL = 300


def font(sz):
    for p in ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/dejavu/DejaVuSans.ttf'):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()


def lit(name, reps):
    bc = np.asarray(Image.open(os.path.join(TEX, f'T_{name}_BC.png')).convert('RGB'), dtype=np.float64) / 255
    n = np.asarray(Image.open(os.path.join(TEX, f'T_{name}_N.png')).convert('RGB'), dtype=np.float64) / 255 * 2 - 1
    orm = np.asarray(Image.open(os.path.join(TEX, f'T_{name}_ORM.png')).convert('RGB'), dtype=np.float64) / 255
    L = np.array([-0.5, 0.55, 0.67])                         # light from the upper left (image +Y = up)
    L /= np.linalg.norm(L)
    ndl = np.clip((n * L).sum(-1), 0, 1)
    flat = L[2]
    shade = 0.35 + 0.65 * ndl / flat
    spec = (1 - orm[..., 1]) ** 3 * np.clip(ndl, 0, 1) ** 20 * 0.6
    img = bc * shade[..., None] + spec[..., None]
    img = np.tile(img, (reps, reps, 1))
    im = Image.fromarray(np.clip(img * 255, 0, 255).astype(np.uint8))
    return im.resize((CELL, CELL), Image.LANCZOS)


def main():
    M = json.load(open(os.path.join(HERE, 'materials.json')))['materials']
    names = list(M)
    cols = 5
    mods = sorted(glob.glob(os.path.join(PREV, 'module_*.png')))
    rows = (len(names) + cols - 1) // cols
    mrows = (len(mods) + cols - 1) // cols
    W, H = cols * (CELL + 10) + 10, rows * (CELL + 40) + mrows * (CELL + 40) + 60
    sheet = Image.new('RGB', (W, H), (28, 28, 30))
    d = ImageDraw.Draw(sheet)
    d.text((10, 12), 'Reed style kit: materials (2x2 tiles, lit from upper left) and modules', fill=(230, 230, 230), font=font(22))
    f = font(15)
    for k, nm in enumerate(names):
        r, c = divmod(k, cols)
        x, y = 10 + c * (CELL + 10), 50 + r * (CELL + 40)
        tm = M[nm]['tile_m']
        sheet.paste(lit(nm, 2 if tm else 1), (x, y))
        lab = f"M_{nm}  " + (f"{tm[0]:.2f} m tile (shown {2 * tm[0]:.1f} m)" if tm else 'decal 0..1')
        d.text((x, y + CELL + 4), lab, fill=(220, 220, 220), font=f)
    y0 = 50 + rows * (CELL + 40)
    for k, p in enumerate(mods):
        r, c = divmod(k, cols)
        x, y = 10 + c * (CELL + 10), y0 + r * (CELL + 40)
        im = Image.open(p).convert('RGB')
        im.thumbnail((CELL, CELL))
        sheet.paste(im, (x, y))
        d.text((x, y + CELL + 4), os.path.basename(p)[7:-4], fill=(220, 220, 220), font=f)
    out = os.path.join(HERE, 'contact_sheet.png')
    sheet.save(out, optimize=True)
    print('wrote', out, sheet.size)


if __name__ == '__main__':
    main()
