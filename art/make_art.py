#!/usr/bin/env python3
"""make_art.py: regenerate the Reed World DMG art kit, deterministically.

Usage:  python3 art/make_art.py          (writes everything under art/)

Everything here is drawn from scratch in code, pixel by pixel: hand-typed
ASCII pixel maps for small pieces, and small procedural rules (distance
fields, periodic textures) for autotile families. Nothing is traced or copied
from any existing game.

Palette (index: hex, letter used in the ASCII maps):
    0  #e0f8cf  W  lightest   (also the sprite transparency key)
    1  #86c06c  L  light
    2  #306850  D  dark
    3  #071821  K  darkest

Outputs (all relative to art/):
    tiles/kit.png            tile atlas, 16 tiles per row, 8x8 tiles
    tiles/kit.json           tile names -> indices, metatiles, autotile tables
    tiles/metatiles.png      every metatile, unlabeled, in kit.json order
    tiles/metatiles_sheet.png labeled reference sheet (x2)
    sprites/player.png       6 frames: down-idle, down-walk, up-idle, up-walk,
                             right-idle, right-walk
    sprites/player_left.png  same, with left in place of right
    sprites/player_ext.png   12 frames incl. a second walk frame per direction
    sprites/player.json      frame metadata
    sprites/signpost.png     16x16 static signpost actor
    preview.png              10x9 metatile mock scene (160x144) scaled x4
    preview_1x.png           the same at 1x
    player_on_grounds.png    the player over every ground metatile
"""
import json
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
PALETTE_HEX = ["#e0f8cf", "#86c06c", "#306850", "#071821"]
RGB = np.array([[0xE0, 0xF8, 0xCF], [0x86, 0xC0, 0x6C],
                [0x30, 0x68, 0x50], [0x07, 0x18, 0x21]], np.uint8)
W, L, D, K = 0, 1, 2, 3
CHARS = {"W": W, "L": L, "D": D, "K": K, ".": W}
SPRITE_KEY = W          # colour treated as transparent in sprites
INF = 1e9


def art(s):
    """Parse an ASCII pixel map (rows of W/L/D/K, '.' = W) into an array."""
    rows = [r.strip() for r in s.strip("\n").split("\n") if r.strip()]
    w = len(rows[0])
    for r in rows:
        assert len(r) == w, (len(r), w, r)
    return np.array([[CHARS[c] for c in r] for r in rows], np.uint8)


def to_img(a, scale=1):
    im = Image.fromarray(RGB[a], "RGB")
    if scale != 1:
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    return im


# --------------------------------------------------------------------------
# Kit container: metatiles are sliced into 8x8 tiles and deduplicated.
# --------------------------------------------------------------------------
class Kit:
    def __init__(self):
        self.tiles, self.index, self.tile_names = [], {}, {}
        self.meta, self.info, self.tile_family = {}, {}, {}

    def tile(self, a, name, family):
        key = a.astype(np.uint8).tobytes()
        if key not in self.index:
            self.index[key] = len(self.tiles)
            self.tiles.append(a.astype(np.uint8).copy())
            self.tile_names[name] = self.index[key]
            self.tile_family[self.index[key]] = family
        return self.index[key]

    def add(self, name, a16, family, solid=False, **extra):
        assert a16.shape == (16, 16), (name, a16.shape)
        assert name not in self.meta, name
        quads = (("tl", 0, 0), ("tr", 0, 8), ("bl", 8, 0), ("br", 8, 8))
        ids = [self.tile(a16[y:y + 8, x:x + 8], f"{name}.{q}", family)
               for q, y, x in quads]
        self.meta[name] = ids
        self.info[name] = dict(family=family, solid=solid, **extra)
        return ids

    def meta_img(self, name):
        t = [self.tiles[i] for i in self.meta[name]]
        return np.vstack([np.hstack(t[:2]), np.hstack(t[2:])])


KIT = Kit()

# --------------------------------------------------------------------------
# Autotile bitmask convention
#   bit set = that neighbour is the SAME terrain (connects).
#   N=1 E=2 S=4 W=8  NE=16 SE=32 SW=64 NW=128
#   Blob/47: a diagonal bit only counts when both orthogonals beside it are
#   set (canon() clears it otherwise). 4-neighbour users pass mask|0xF0.
# --------------------------------------------------------------------------
BITS = {"N": 1, "E": 2, "S": 4, "W": 8, "NE": 16, "SE": 32, "SW": 64, "NW": 128}
OFFS = {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0),
        "NE": (1, -1), "SE": (1, 1), "SW": (-1, 1), "NW": (-1, -1)}
DIAG = {"NE": ("N", "E"), "SE": ("S", "E"), "SW": ("S", "W"), "NW": ("N", "W")}


def canon(mask):
    m = mask & 15
    for dg, (a, b) in DIAG.items():
        if mask & BITS[dg] and m & BITS[a] and m & BITS[b]:
            m |= BITS[dg]
    return m


BLOB47 = sorted({canon(m) for m in range(256)})
assert len(BLOB47) == 47


def rect_dist(px, py, cx, cy):
    x0, x1, y0, y1 = cx * 16, cx * 16 + 16, cy * 16, cy * 16 + 16
    dx = max(x0 - px, 0.0, px - x1)
    dy = max(y0 - py, 0.0, py - y1)
    return math.hypot(dx, dy)


def efield(mask, px, py, R):
    """Rounded distance (px) from point to the nearest non-member cell.
    Outer corners are rounded with radius R; <=0 means outside."""
    m = canon(mask)
    cell = (math.floor(px / 16), math.floor(py / 16))
    if cell != (0, 0):
        nm = [k for k, v in OFFS.items() if v == cell]
        if not nm or not (m & BITS[nm[0]]):
            return -1.0
    dd = {k: (rect_dist(px, py, *OFFS[k]) if not (m & BITS[k]) else INF)
          for k in OFFS}
    e = INF
    for a, b in (("N", "E"), ("E", "S"), ("S", "W"), ("W", "N")):
        da, db = dd[a], dd[b]
        v = R - math.hypot(R - da, R - db) if (da < R and db < R) else min(da, db)
        e = min(e, v)
    for dg in DIAG:
        e = min(e, dd[dg])
    return e


def blob_family(prefix, shade, family, solid):
    names = {}
    for m in BLOB47:
        a = np.zeros((16, 16), np.uint8)
        for y in range(16):
            for x in range(16):
                a[y, x] = shade(m, x, y)
        n = f"{prefix}_{m:03d}"
        KIT.add(n, a, family, solid=solid, mask=m)
        names[m] = n
    return names


# --------------------------------------------------------------------------
# Ground: lawn
# --------------------------------------------------------------------------
LAWN = np.full((8, 8), L, np.uint8)
TUFT = art("""
LLLLLLLL
LLLLLLLL
LDLLDLLL
LLDDLLLL
LLLLLLLL
LLLLLLLL
LLLLLLLL
LLLLLLLL
""")
TUFT2 = art("""
LLLLLLLL
LLLLLLLL
LLLLLLLL
LLLLLLLL
LLLLDLDL
LLLLLDLL
LLLLLLLL
LLLLLLLL
""")
FLOWER = art("""
LLLLLLLL
LLLLLLLL
LLLLLWLL
LLLLWDWL
LLLLLWLL
LLLLLLLL
LLLLLLLL
LLLLLLLL
""")


def q4(tl, tr, bl, br):
    return np.vstack([np.hstack([tl, tr]), np.hstack([bl, br])])


KIT.add("lawn_a", q4(LAWN, LAWN, LAWN, LAWN), "lawn")
KIT.add("lawn_b", q4(TUFT, LAWN, LAWN, TUFT2), "lawn")
KIT.add("lawn_c", q4(LAWN, FLOWER, TUFT2, LAWN), "lawn")

# --------------------------------------------------------------------------
# Footpath: narrow (10 px) pipe autotile, 4-neighbour, 16 cases
# --------------------------------------------------------------------------
P0, P1 = 3, 13          # path occupies [P0, P1) across the cell
PEBBLES = {(5, 1)}
NIBBLES = {(3, 1), (4, 6), (1, 3), (6, 4), (2, 7), (7, 2), (4, 0), (0, 4)}


def in_path(mask, x, y):
    n, e, s, w = (mask & 1, mask & 2, mask & 4, mask & 8)
    cx = P0 <= x < P1
    cy = P0 <= y < P1
    if cx and cy:
        # round the outer corners of the centre pad
        if x == P0 and y == P0 and not n and not w: return False
        if x == P1 - 1 and y == P0 and not n and not e: return False
        if x == P0 and y == P1 - 1 and not s and not w: return False
        if x == P1 - 1 and y == P1 - 1 and not s and not e: return False
        return True
    if cx and y < P0 and n: return True
    if cx and y >= P1 and s: return True
    if cy and x < P0 and w: return True
    if cy and x >= P1 and e: return True
    # fillet the inner corners of a turn
    if n and w and (x, y) == (P0 - 1, P0 - 1): return True
    if n and e and (x, y) == (P1, P0 - 1): return True
    if s and w and (x, y) == (P0 - 1, P1): return True
    if s and e and (x, y) == (P1, P1): return True
    return False


def path_shade(mask, x, y):
    if not in_path(mask, x, y):
        # a darker grass fringe along the path edge
        return L
    nb = [in_path(mask, x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
    if not all(nb) and (x % 8, y % 8) in NIBBLES:
        return L                     # grass creeping over the path edge
    if all(nb) and (x % 8, y % 8) in PEBBLES:
        return L
    return W


PATH = {}
for m in range(16):
    a = np.array([[path_shade(m, x, y) for x in range(16)] for y in range(16)], np.uint8)
    PATH[m] = f"path_{m:02d}"
    KIT.add(PATH[m], a, "path", mask=m)

# --------------------------------------------------------------------------
# Paved: plaza (running-bond pavers) and parking (asphalt + bay stripe)
# --------------------------------------------------------------------------
PAVER = art("""
WWWWWWWL
WWWWWWWL
WWWWWWWL
LLLLLLLL
WWWLWWWW
WWWLWWWW
WWWLWWWW
LLLLLLLL
""")
KIT.add("paved_plaza", q4(PAVER, PAVER, PAVER, PAVER), "paved")


def road_tex(u, v):
    return D


ROADFILL = np.array([[road_tex(u, v) for u in range(8)] for v in range(8)], np.uint8)
STRIPE = ROADFILL.copy()
STRIPE[:, 0] = W
KIT.add("paved_parking", q4(STRIPE, ROADFILL, STRIPE, ROADFILL), "paved")

# --------------------------------------------------------------------------
# Road with kerbs (blob/47). Outside the kerb is assumed to be lawn.
# --------------------------------------------------------------------------


def road_shade(m, x, y):
    e = efield(m, x + 0.5, y + 0.5, 3.0)
    if e < 0: return L
    if e < 1: return K
    if e < 2: return W
    return road_tex(x % 8, y % 8)


ROAD = blob_family("road", road_shade, "road", solid=False)

# --------------------------------------------------------------------------
# Water with shores (blob/47) + one extra fill variant
# --------------------------------------------------------------------------
WAVE_A = {(1, 2): L, (2, 1): L, (3, 1): L, (4, 2): L, (6, 6): W}
WAVE_B = {(4, 5): L, (5, 4): L, (6, 4): L, (7, 5): L, (1, 1): W}


def water_tex(u, v, waves=WAVE_A):
    return waves.get((u, v), D)


def water_shade(m, x, y):
    e = efield(m, x + 0.5, y + 0.5, 4.0)
    u, v = x % 8, y % 8
    if e < 0: return L
    if e < 1: return W
    if e < 2: return L if (u + v) % 4 else D
    if e < 3: return D
    return water_tex(u, v)


WATER = blob_family("water", water_shade, "water", solid=True)
WB = np.array([[water_tex(u, v, WAVE_B) for u in range(8)] for v in range(8)], np.uint8)
WA = np.array([[water_tex(u, v) for u in range(8)] for v in range(8)], np.uint8)
KIT.add("water_fill_b", q4(WA, WB, WB, WA), "water", solid=True)

# --------------------------------------------------------------------------
# Trees: single 16x16 tree, and dense forest (blob/47) + fill variant
# --------------------------------------------------------------------------


def single_tree():
    a = np.full((16, 16), L, np.uint8)
    cx, cy, r = 8.0, 6.5, 6.6
    for y in range(16):
        for x in range(16):
            px, py = x + 0.5, y + 0.5
            # ground shadow (sun from upper left)
            if ((px - 9.0) / 6.0) ** 2 + ((py - 13.6) / 2.2) ** 2 < 1:
                a[y, x] = D
            q = math.hypot(px - cx, py - cy)
            if q < r:
                a[y, x] = K
            if q < r - 1:
                a[y, x] = D
                # leaf clumps: three lit lobes in the upper left
                for lx, ly, lr in ((5.6, 4.6, 2.3), (9.4, 3.4, 1.7), (4.4, 8.4, 1.4)):
                    if math.hypot(px - lx, py - ly) < lr:
                        a[y, x] = L
                # self-shadow on the lower right rim
                if q > r - 2.4 and (px - cx) + (py - cy) > 3.5:
                    a[y, x] = K
    # trunk
    for y in range(12, 15):
        a[y, 7] = K; a[y, 8] = D; a[y, 9] = K
    a[13, 8] = D; a[14, 7:10] = K
    # a few dark leaf gaps
    for x, y in ((10, 6), (7, 9), (11, 9)):
        a[y, x] = K
    return a


KIT.add("tree", single_tree(), "tree", solid=True)


def forest_tex(u, v, variant=0):
    px, py = u + 0.5, v + 0.5
    q = math.hypot(px - 4, py - 4)
    if q > 4.15: return K
    if variant == 0:
        if math.hypot(px - 3.0, py - 2.8) < 1.5: return L
    else:
        if math.hypot(px - 4.6, py - 2.6) < 1.1 or (u, v) == (2, 4): return L
    if q > 2.9 and (px - 4) + (py - 4) > 2.4: return K
    return D


def scallop(u):
    return 2.0 * math.sin(math.pi * (u + 0.5) / 8) ** 2


F_T0 = 2.2


def forest_e(m, px, py):
    e = efield(m, px, py, 4.0)
    if e < 0: return -1
    return e + scallop(math.floor(px) % 8) + scallop(math.floor(py) % 8)


def forest_shade(m, x, y):
    e = forest_e(m, x + 0.5, y + 0.5)
    if e < F_T0:
        if forest_e(m, x - 0.5, y - 0.5) >= F_T0 or forest_e(m, x + 0.5, y - 0.5) >= F_T0:
            return D                        # cast shadow on the grass
        return L
    if e < F_T0 + 1:
        return K
    return forest_tex(x % 8, y % 8)


FOREST = blob_family("forest", forest_shade, "forest", solid=True)
FA = np.array([[forest_tex(u, v) for u in range(8)] for v in range(8)], np.uint8)
FB = np.array([[forest_tex(u, v, 1) for u in range(8)] for v in range(8)], np.uint8)
KIT.add("forest_fill_b", q4(FA, FB, FB, FA), "forest", solid=True)

# --------------------------------------------------------------------------
# Steep ground: ledge (one-way drop south), cliff faces, stairs
# --------------------------------------------------------------------------
LEDGE = art("""
LLLLLLLL
WWWWWWWW
LLLLLLLL
DDLDDDLD
DKDDDKDD
DKDDDKDD
KKKKKKKK
DDDDDDDD
""")
LEDGE_CAP_L = art("""
LLLLLLLL
LLWWWWWW
LWLLLLLL
LKDDDDLD
LLKDDKDD
LLKDDKDD
LLLKKKKK
LLLLDDDD
""")
LEDGE_CAP_R = art("""
LLLLLLLL
WWWWWWLL
LLLLLLWL
DDLDDDKL
DKDDDKLL
DKDDKKLL
KKKKKLLL
DDDDLLLL
""")
ROCK_A = art("""
DLLLDKDL
LDDDDKLD
LDDDDKDD
DDDDKDDD
KKKKDLLL
LLDKLDDD
DDDKDDDD
DDKKKDDD
""")
ROCK_B = art("""
DDKDLLLD
LDKLDDDD
DDKDDDDK
KKKDDDKK
DLLKKKDL
LDDDKLDD
DDDDKDDD
KKDDKDDK
""")
CLIFF_LIP = np.vstack([art("LLLLLLLL\nLLLLLLLL\nWWWWWWWW\nKKKKKKKK"), ROCK_A[4:]])
CLIFF_BASE = np.vstack([ROCK_B[:3], art("KKKKKKKK\nDLDDDLDD\nLLLLLLLL\nLLLLLLLL\nLLLLLLLL")])
SIDE_L = ROCK_A.copy(); SIDE_L[:, :2] = L; SIDE_L[:, 2] = K
SIDE_R = ROCK_A.copy(); SIDE_R[:, 6:] = L; SIDE_R[:, 5] = K
STAIR_L = art("""
KDWWWWWW
KDWWWWWW
KDLLLLLL
KDKKKKKK
KDWWWWWW
KDWWWWWW
KDLLLLLL
KDKKKKKK
""")
STAIR_R = STAIR_L[:, ::-1].copy()

KIT.add("ledge", q4(LAWN, LAWN, LEDGE, LEDGE), "steep", ledge="S")
KIT.add("ledge_l", q4(LAWN, LAWN, LEDGE_CAP_L, LEDGE), "steep", ledge="S")
KIT.add("ledge_r", q4(LAWN, LAWN, LEDGE, LEDGE_CAP_R), "steep", ledge="S")
KIT.add("cliff_top", q4(LAWN, LAWN, CLIFF_LIP, CLIFF_LIP), "steep", solid=True)
KIT.add("cliff_face", q4(ROCK_A, ROCK_B, ROCK_B, ROCK_A), "steep", solid=True)
KIT.add("cliff_base", q4(ROCK_A, ROCK_B, CLIFF_BASE, CLIFF_BASE), "steep", solid=True)
KIT.add("cliff_face_l", q4(SIDE_L, ROCK_B, SIDE_L, ROCK_A), "steep", solid=True)
KIT.add("cliff_face_r", q4(ROCK_A, SIDE_R, ROCK_B, SIDE_R), "steep", solid=True)
KIT.add("stairs", q4(STAIR_L, STAIR_R, STAIR_L, STAIR_R), "steep")

# --------------------------------------------------------------------------
# Bridges / boardwalk
# --------------------------------------------------------------------------
BR_TOP = art("""
KKKKKKKK
DDDDDDDD
KKKKKKKK
WWWLWWWL
WWWLWWWL
WWWLWWWL
WWWLWWWL
WWWLWWWL
""")
BR_BOT = art("""
WWWLWWWL
WWWLWWWL
WWWLWWWL
WWWLWWWL
WWWLWWWL
KKKKKKKK
DDDDDDDD
KKKKKKKK
""")
KIT.add("bridge_h", q4(BR_TOP, BR_TOP, BR_BOT, BR_BOT), "bridge")
KIT.add("bridge_v", q4(BR_TOP.T, BR_BOT.T, BR_TOP.T, BR_BOT.T), "bridge")

# --------------------------------------------------------------------------
# Buildings. Footprint w x h metatiles: rows 0..h-2 roof, last row facade.
# --------------------------------------------------------------------------


def tudor_tex(u, v):
    if v in (3, 7): return K
    if v == 2 and u == 3: return K
    if v == 6 and u == 7: return K
    if (u, v) in {(5, 0), (1, 4)}: return L       # glints on slate
    return D


def roof_tudor(n, e, w):
    a = np.array([[tudor_tex(x % 8, y % 8) for x in range(16)] for y in range(16)], np.uint8)
    if n:
        a[0, :] = K; a[1, :] = L; a[2, :] = L; a[3, :] = K
    if w:
        a[:, 0] = K; a[(1 if n else 0):, 1] = L
    if e:
        a[:, 15] = K; a[(1 if n else 0):, 14] = L
    if n and w: a[1:3, 1] = L; a[0, 0] = K
    if n and e: a[1:3, 14] = L
    return a


def flat_tex(u, v):
    return L if (u, v) in {(2, 3), (3, 3)} else W


def roof_flat(n, e, w):
    a = np.array([[flat_tex(x % 8, y % 8) for x in range(16)] for y in range(16)], np.uint8)
    if n: a[0, :] = K; a[1, :] = L; a[2, :] = D
    if w: a[:, 0] = K; a[:, 1] = L; a[(2 if n else 0):, 2] = D
    if e: a[:, 15] = K; a[:, 14] = L; a[(2 if n else 0):, 13] = D
    if n and w: a[1, 1] = L; a[2, 1] = L; a[2, 2] = D
    if n and e: a[1, 14] = L; a[2, 14] = L; a[2, 13] = D
    return a


ROOF_SUFFIX = {}
for style, fn in (("tudor", roof_tudor), ("flat", roof_flat)):
    for n in (0, 1):
        for e in (0, 1):
            for w in (0, 1):
                suf = "".join(c for c, f in (("n", n), ("e", e), ("w", w)) if f)
                name = f"roof_{style}" + (f"_{suf}" if suf else "")
                KIT.add(name, fn(n, e, w), "building", solid=True, roof=style)


def gable():
    a = roof_tudor(0, 0, 0)
    for y in range(16):
        for x in range(16):
            half = (y + 1) * 0.5 + 0.5
            dx = abs(x + 0.5 - 8.0)
            if dx < half:
                a[y, x] = K
            if dx < half - 1:
                a[y, x] = L                         # stone coping
            if dx < half - 2:
                a[y, x] = brick(x % 8, y + 2)       # brick face
    for y in range(8, 15):
        a[y, 7] = K; a[y, 8] = K
        a[y, 6] = D; a[y, 9] = D
    a[7, 7:9] = D
    a[9, 8] = W
    a[15, 5:11] = L                                 # sill / string course
    return a


def brick(u, y):
    """W brick with L mortar; courses 3 px tall, half-bond joints."""
    c = (y - 2) // 3
    if (y - 2) % 3 == 2: return L
    return L if u == (3 if c % 2 == 0 else 7) else W


def facade_base():
    a = np.zeros((16, 16), np.uint8)
    for y in range(16):
        for x in range(16):
            a[y, x] = brick(x % 8, y)
    a[0, :] = K; a[1, :] = D           # eave line + shadow under the eaves
    a[13, :] = L; a[14, :] = D; a[15, :] = K   # string course, plinth, base
    return a


def add_window(a, x0):
    """Tall lancet window, 4 px wide, at columns x0..x0+3."""
    a[4, x0 + 1:x0 + 3] = D
    for y in range(5, 12):
        a[y, x0] = D; a[y, x0 + 3] = D
        a[y, x0 + 1:x0 + 3] = K
    a[6, x0 + 1] = W
    a[12, x0:x0 + 4] = L


def door_opening(x, y):
    if y < 4: return False
    if y == 4: return 6 <= x <= 9
    if y == 5: return 4 <= x <= 11
    return 3 <= x <= 12


def add_door(a, opening):
    for y in range(2, 16):
        for x in range(16):
            if opening(x, y):
                edge = not all(opening(x + dx, y + dy) or y + dy > 15
                               for dx, dy in ((1, 0), (-1, 0), (0, -1)))
                a[y, x] = K if edge else D
            elif any(opening(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
                a[y, x] = L                             # dressed stone surround
    for y in range(8, 15):
        if opening(7, y): a[y, 7] = K                   # leaf seam
    for x in range(16):
        if opening(x, 7) and a[7, x] == D: a[7, x] = K  # transom bar
    a[15, 2:14] = L                                     # threshold step


def corner(a, side):
    if side == "l":
        a[:, 0] = K
        for y in range(2, 15):
            a[y, 1] = L if ((y - 2) // 3) % 2 == 0 else D   # quoins
        a[0, 0] = K
    else:
        a[:, 15] = K
        for y in range(2, 15):
            a[y, 14] = L if ((y - 2) // 3) % 2 == 0 else D
    return a


wall = facade_base()
win = facade_base(); add_window(win, 2); add_window(win, 10)
wall_l, wall_r = corner(wall.copy(), "l"), corner(wall.copy(), "r")
KIT.add("facade_wall", wall, "building", solid=True)
KIT.add("facade_wall_l", wall_l, "building", solid=True)
KIT.add("facade_wall_r", wall_r, "building", solid=True)
KIT.add("facade_window", win, "building", solid=True)
# end bays keep their quoined pier and one window, so they reuse wall corner tiles
win_l = win.copy(); win_l[:, :8] = wall_l[:, :8]
win_r = win.copy(); win_r[:, 8:] = wall_r[:, 8:]
KIT.add("facade_window_l", win_l, "building", solid=True)
KIT.add("facade_window_r", win_r, "building", solid=True)

door = facade_base(); add_door(door, door_opening)
KIT.add("facade_door", door, "building", solid=False, door=True)
# middle of the wide double door: flat Tudor lintel, two identical 8 px leaves
mid = facade_base()
for y in range(2, 16):
    for x in range(16):
        if y == 3: mid[y, x] = L
        elif y == 4: mid[y, x] = K
        elif y >= 5: mid[y, x] = D
mid[7, :] = K                                       # transom bar
for y in range(8, 15):
    mid[y, 15] = K                                  # single centre seam (x = 15 of 32)
mid[10, 13] = L; mid[10, 1] = L                     # ring handles either side of the seam
mid[15, :] = L
door_bl_plain = door[8:, :8].copy()
door_bl_plain[:7, 7] = D                            # left leaf of the wide door has no seam
KIT.add("facade_ddoor_l", q4(door[:8, :8], mid[:8, 8:], door_bl_plain, mid[8:, 8:]),
        "building", solid=False, door=True)
KIT.add("facade_ddoor_r", q4(mid[:8, :8], door[:8, 8:], mid[8:, :8], door[8:, 8:]),
        "building", solid=False, door=True)

plaque = facade_base()
plaque[3, 2:14] = K; plaque[7, 2:14] = K
plaque[3:8, 2] = K; plaque[3:8, 13] = K
plaque[4:7, 3:13] = L
plaque[4, 4:7] = D; plaque[4, 8:12] = D
plaque[6, 4:9] = D; plaque[6, 10:12] = D
KIT.add("facade_plaque", plaque, "building", solid=True, sign=True)

ribbon = facade_base()
ribbon[2:13, :] = W
ribbon[3, :] = D
ribbon[4:9, :] = K
for x in range(16):
    if x % 4 == 3: ribbon[4:9, x] = D
ribbon[5, 1::4] = W
ribbon[9, :] = L
ribbon[11, :] = L
KIT.add("facade_ribbon", ribbon, "building", solid=True)
KIT.add("roof_tudor_gable", gable(), "building", solid=True)

# --------------------------------------------------------------------------
# Props: signpost, fence, hedge, bench
# --------------------------------------------------------------------------
KIT.add("signpost", art("""
LLLLLLLLLLLLLLLL
LLLLLLLLLLLLLLLL
LLKKKKKKKKKKKKLL
LKWWWWWWWWWWWWKL
LKWDDWDDDWDDDWKL
LKWWWWWWWWWWWWKL
LKWDDDWDDWDDWWKL
LKWWWWWWWWWWWWKL
LLKKKKKKKKKKKKLL
LLLLKDKLLKDKLLLL
LLLLKDKLLKDKLLLL
LLLLKDKLLKDKLLLL
LLLLKDKLLKDKLLLL
LLLLKKKDLKKKDLLL
LLLLLDDDLLDDDLLL
LLLLLLLLLLLLLLLL
"""), "prop", solid=True, sign=True)

FENCE = art("""
LLLLLLLL
KKLLLLLL
KWKKKKKK
KWWWWWWW
KDKKKKKK
KWWWWWWW
KDKKKKKK
LDDDDDDD
""")
FENCE_V = art("""
LLKWWKLL
LLKWWKLL
LKWWWWKL
LKDWWDKL
LLKWWKDL
LLKWWKDL
LLKWWKDL
LLKWWKDL
""")
KIT.add("fence_h", q4(LAWN, LAWN, FENCE, FENCE), "prop", solid=True)
KIT.add("fence_v", q4(FENCE_V, LAWN, FENCE_V, LAWN), "prop", solid=True)


def hedge(cap_l, cap_r):
    def inside(x, y):
        top = 2 + (0 if x % 4 in (1, 2) else 1)
        x0 = 2 if cap_l else -99
        x1 = 13 if cap_r else 99
        if not (top <= y <= 12 and x0 <= x <= x1): return False
        if cap_l and x == x0 and (y <= 4 or y >= 11): return False
        if cap_r and x == x1 and (y <= 4 or y >= 11): return False
        return True
    a = np.full((16, 16), L, np.uint8)
    for y in range(16):
        for x in range(16):
            if inside(x, y):
                edge = not all(inside(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
                if edge: a[y, x] = K
                elif x % 4 in (1, 2) and y == 4: a[y, x] = L
                elif (x % 4, y % 4) == (3, 2) and y > 5: a[y, x] = K
                elif (x % 4, y % 4) == (1, 0) and y > 5: a[y, x] = L
                else: a[y, x] = D
            elif inside(x, y - 1) or inside(x - 1, y - 1):
                a[y, x] = D
    return a


KIT.add("hedge", hedge(False, False), "prop", solid=True)
KIT.add("hedge_l", hedge(True, False), "prop", solid=True)
KIT.add("hedge_r", hedge(False, True), "prop", solid=True)

BENCH = art("""
LKKKKKKKKKKKKKKL
LKDDDDDDDDDDDDKL
LKKKKKKKKKKKKKKL
LKWWWWWWWWWWWWKL
LKLLLLLLLLLLLLKL
LKKKKKKKKKKKKKKL
LLKDLLLLLLLLKDLL
LLLDDLLLLLLLLDDL
""")
KIT.add("bench", np.vstack([np.full((8, 16), L, np.uint8), BENCH]), "prop", solid=True)

# --------------------------------------------------------------------------
# Layout helpers, usable by the exporter (import make_art or copy them)
# --------------------------------------------------------------------------


def mask_at(grid, x, y, same):
    """8-bit neighbour mask for cell (x, y); `same(v)` says if a cell connects.
    Off-map neighbours count as connected (no edge drawn at the map edge)."""
    h, w = len(grid), len(grid[0])
    m = 0
    for k, (dx, dy) in OFFS.items():
        nx, ny = x + dx, y + dy
        if not (0 <= nx < w and 0 <= ny < h) or same(grid[ny][nx]):
            m |= BITS[k]
    return m


def building_layout(w, h, roof="tudor", doors=(), ddoors=(), gables=(),
                    plaques=(), windows=True):
    """Metatile names for a w x h building. Last row is the facade.
    doors/ddoors/plaques/gables are column indices (ddoor = left column)."""
    rows = []
    for j in range(h - 1):
        row = []
        for i in range(w):
            if roof == "tudor" and j == h - 2 and i in gables:
                row.append("roof_tudor_gable"); continue
            suf = "".join(c for c, f in (("n", j == 0), ("e", i == w - 1), ("w", i == 0)) if f)
            row.append(f"roof_{roof}" + (f"_{suf}" if suf else ""))
        rows.append(row)
    base = "facade_window" if windows else "facade_wall"
    if roof == "flat" and windows: base = "facade_ribbon"
    row = []
    for i in range(w):
        if i in doors: row.append("facade_door")
        elif i in ddoors: row.append("facade_ddoor_l")
        elif i - 1 in ddoors: row.append("facade_ddoor_r")
        elif i in plaques: row.append("facade_plaque")
        elif i == 0 and base != "facade_ribbon": row.append(base + "_l")
        elif i == w - 1 and base != "facade_ribbon": row.append(base + "_r")
        else: row.append(base)
    rows.append(row)
    return rows


def render_grid(names):
    h, w = len(names), len(names[0])
    out = np.zeros((h * 16, w * 16), np.uint8)
    for y in range(h):
        for x in range(w):
            out[y * 16:y * 16 + 16, x * 16:x * 16 + 16] = KIT.meta_img(names[y][x])
    return out


# --------------------------------------------------------------------------
# Sprites. '.' = transparent (drawn as W, the sprite key colour).
# --------------------------------------------------------------------------
HEAD_FRONT = """
.......KK.......
.....KKDDKK.....
....KLLLLLLK....
...KLLLLLLLLK...
..KDDDDDDDDDDK..
..KKLLLLLLLLKK..
..KLLKLLLLKLLK..
..KLLKLLLLKLLK..
...KLLLLLLLLK...
....KKLLLLKK....
"""
BODY_FRONT = """
...KDLDDDDLDK...
..KDDLDDDDLDDK..
..KLKDDDDDDKLK..
...KKKKKKKKKK...
"""
HEAD_BACK = """
.......KK.......
.....KKDDKK.....
....KLLLLLLK....
...KLLLLLLLLK...
..KDDDDDDDDDDK..
..KKKKKKKKKKKK..
..KKKDKKKKDKKK..
..KKKKKKKKKKKK..
...KKKKKKKKKK...
...KKKLLLLKKK...
"""
BODY_BACK = """
..KDKLLLLLLKDK..
..KDKLDDDDLKDK..
..KLKLLLLLLKLK..
...KKKKKKKKKK...
"""
LEGS_IDLE = """
....KDK..KDK....
....KKK..KKK....
"""
LEGS_WALK_A = """
....KDK..KKK....
....KKK.........
"""
LEGS_WALK_B = """
....KKK..KDK....
.........KKK....
"""
SIDE_HEAD = """
......KK........
....KKDDKK......
...KLLLLLLK.....
..KLLLLLLLLK....
..KDDDDDDDDDK...
..KKKKKLLLLLK...
..KKKKLLLKLLK...
..KKKKLLLLLLLK..
...KKKLLLLLK....
....KKKLLLKK....
"""
SIDE_BODY = {
    "idle": """
..KKKKKDDDDK....
.KLLLKDDDDDK....
.KLDLKDDLDDK....
.KLLLKKKKKKK....
..KKK.KDDK......
......KKKK......
""",
    "walk": """
..KKKKKDDDDK....
.KLLLKDDDDDK....
.KLDLKDDDLDK....
.KLLLKKKKKKK....
..KKKKDKKDK.....
....KKK..KKK....
""",
    "walk_b": """
..KKKKKDDDDK....
.KLLLKDDDDDK....
.KLDLKDLDDDK....
.KLLLKKKKKKK....
..KKK.KDDK......
......KKKKK.....
""",
}


def frame(*parts):
    a = art("\n".join(p.strip("\n") for p in parts))
    assert a.shape == (16, 16), a.shape
    return a


FRAMES = {
    "down_idle": frame(HEAD_FRONT, BODY_FRONT, LEGS_IDLE),
    "down_walk": frame(HEAD_FRONT, BODY_FRONT, LEGS_WALK_A),
    "down_walk_b": frame(HEAD_FRONT, BODY_FRONT, LEGS_WALK_B),
    "up_idle": frame(HEAD_BACK, BODY_BACK, LEGS_IDLE),
    "up_walk": frame(HEAD_BACK, BODY_BACK, LEGS_WALK_A),
    "up_walk_b": frame(HEAD_BACK, BODY_BACK, LEGS_WALK_B),
    "right_idle": frame(SIDE_HEAD, SIDE_BODY["idle"]),
    "right_walk": frame(SIDE_HEAD, SIDE_BODY["walk"]),
    "right_walk_b": frame(SIDE_HEAD, SIDE_BODY["walk_b"]),
}
for k in ("idle", "walk", "walk_b"):
    FRAMES[f"left_{k}"] = FRAMES[f"right_{k}"][:, ::-1].copy()

SIGNPOST_SPRITE = art("""
................
................
.KKKKKKKKKKKKKK.
.KLLLLLLLLLLLLK.
.KLDDLDDDLDDDLK.
.KLLLLLLLLLLLLK.
.KLDDDLDDLDDLLK.
.KLLLLLLLLLLLLK.
.KKKKKKKKKKKKKK.
......KDDK......
......KDDK......
......KDDK......
......KDDK......
......KDDK......
.....DKKKKD.....
................
""")

STRIP_MAIN = ["down_idle", "down_walk", "up_idle", "up_walk", "right_idle", "right_walk"]
STRIP_LEFT = ["down_idle", "down_walk", "up_idle", "up_walk", "left_idle", "left_walk"]
STRIP_EXT = [f"{d}_{k}" for d in ("down", "up", "right", "left") for k in ("idle", "walk", "walk_b")]


def strip(names):
    return np.hstack([FRAMES[n] for n in names])


def blit_sprite(dst, spr, x, y):
    h, w = spr.shape
    region = dst[y:y + h, x:x + w]
    mask = spr != SPRITE_KEY
    region[mask] = spr[mask]


# --------------------------------------------------------------------------
# Preview scene: 10 x 9 metatiles
# --------------------------------------------------------------------------


def lawn_variant(x, y):
    hsh = (x * 73856093 ^ y * 19349663) % 11
    return "lawn_b" if hsh == 0 else "lawn_c" if hsh == 1 else "lawn_a"


def preview_scene():
    # terrain letters: . lawn, P path, Z plaza, ~ water, F forest, T tree,
    # B building, S signpost metatile, b bench
    terrain = [
        "FFF.....T.",
        "FFBBBBBB.T",
        "F.BBBBBB.T",
        "..BBBBBB..",
        "....ZZS...",
        "....P.b...",
        "~~..PPPPPP",
        "~~~.P....T",
        "~~~~P...TT",
    ]
    names = [[None] * 10 for _ in range(9)]
    bld = building_layout(6, 3, "tudor", ddoors=(2,), gables=(1, 4), plaques=(4,))
    for y in range(9):
        for x in range(10):
            c = terrain[y][x]
            if c == "B":
                names[y][x] = bld[y - 1][x - 2]
            elif c == "P":
                m = mask_at(terrain, x, y, lambda v: v in "PZ") & 15
                # off-map counts as connected only for the path leaving south/east
                names[y][x] = PATH[m]
            elif c == "~":
                m = canon(mask_at(terrain, x, y, lambda v: v == "~"))
                names[y][x] = "water_fill_b" if m == 255 and (x + y) % 2 else WATER[m]
            elif c == "F":
                m = canon(mask_at(terrain, x, y, lambda v: v == "F"))
                names[y][x] = "forest_fill_b" if m == 255 and (x + y) % 2 else FOREST[m]
            elif c == "T": names[y][x] = "tree"
            elif c == "Z": names[y][x] = "paved_plaza"
            elif c == "S": names[y][x] = "signpost"
            elif c == "b": names[y][x] = "bench"
            else: names[y][x] = lawn_variant(x, y)
    img = render_grid(names)
    blit_sprite(img, FRAMES["down_walk"], 4 * 16, 5 * 16 + 2)
    blit_sprite(img, SIGNPOST_SPRITE, 3 * 16, 4 * 16)
    return img, names


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
FONT = ImageFont.load_default()


def label(draw, xy, text, fill):
    draw.fontmode = "1"
    draw.text(xy, text, fill=fill, font=FONT)


def check_palette(path):
    im = Image.open(path).convert("RGB")
    cols = {c for _, c in im.getcolors(1 << 20)}
    allowed = {tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in PALETTE_HEX}
    assert cols <= allowed, (path, cols - allowed)


def write_all():
    os.makedirs(os.path.join(ROOT, "tiles"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "sprites"), exist_ok=True)
    written = []

    # tile atlas
    n = len(KIT.tiles)
    cols = 16
    rows = (n + cols - 1) // cols
    atlas = np.full((rows * 8, cols * 8), W, np.uint8)
    for i, t in enumerate(KIT.tiles):
        atlas[(i // cols) * 8:(i // cols) * 8 + 8, (i % cols) * 8:(i % cols) * 8 + 8] = t
    p = os.path.join(ROOT, "tiles", "kit.png"); to_img(atlas).save(p); written.append(p)

    # metatile strip (unlabeled), 16 metatiles per row
    names = list(KIT.meta)
    mrows = (len(names) + 15) // 16
    sheet = np.full((mrows * 16, 16 * 16), W, np.uint8)
    for i, nm in enumerate(names):
        y, x = (i // 16) * 16, (i % 16) * 16
        sheet[y:y + 16, x:x + 16] = KIT.meta_img(nm)
    p = os.path.join(ROOT, "tiles", "metatiles.png"); to_img(sheet).save(p); written.append(p)

    # labeled reference sheet, grouped by family
    fams = []
    for nm in names:
        f = KIT.info[nm]["family"]
        if not fams or fams[-1][0] != f: fams.append((f, []))
        fams[-1][1].append(nm)
    cell_w, cell_h, per_row, sc = 84, 52, 12, 2
    total_rows = sum((len(v) + per_row - 1) // per_row for _, v in fams)
    H = total_rows * cell_h + len(fams) * 14 + 8
    im = Image.new("RGB", (per_row * cell_w + 8, H), tuple(RGB[W]))
    dr = ImageDraw.Draw(im)
    y = 4
    for f, lst in fams:
        label(dr, (4, y), f"[{f}]", tuple(RGB[K])); y += 14
        for i, nm in enumerate(lst):
            if i and i % per_row == 0: y += cell_h
            x = 4 + (i % per_row) * cell_w
            im.paste(to_img(KIT.meta_img(nm), sc), (x, y))
            label(dr, (x, y + 33), nm.replace("roof_", "r_").replace("facade_", "f_"), tuple(RGB[D]))
        y += cell_h
    p = os.path.join(ROOT, "tiles", "metatiles_sheet.png"); im.save(p); written.append(p)

    # kit.json
    autotile = {
        "convention": {
            "bits": BITS,
            "meaning": "bit set = that neighbour is the same terrain (connects); "
                       "off-map neighbours count as connected",
            "blob47": "diagonal bits only count when both adjacent orthogonals are set; "
                      "reduce any 8-bit mask with canon(mask) before lookup",
            "n4_on_blob": "to drive a blob family from 4 neighbours, look up canon(mask4 | 0xF0)",
            "outside": "edges are drawn against lawn (L); place lawn on the other side",
        },
        "path": {"type": "n4", "masks": {str(m): PATH[m] for m in range(16)},
                 "connects_to": ["path", "paved_plaza", "bridge", "facade_door", "stairs"]},
        "road": {"type": "blob47", "masks": {str(m): ROAD[m] for m in BLOB47}},
        "water": {"type": "blob47", "masks": {str(m): WATER[m] for m in BLOB47},
                  "fill_variants": ["water_255", "water_fill_b"],
                  "note": "count bridge cells as water when computing masks"},
        "forest": {"type": "blob47", "masks": {str(m): FOREST[m] for m in BLOB47},
                   "fill_variants": ["forest_255", "forest_fill_b"]},
    }
    kit = {
        "palette": PALETTE_HEX,
        "palette_note": "index 0 lightest .. 3 darkest",
        "tile_size": 8, "metatile_size": 16, "metres_per_metatile": 6,
        "atlas": "kit.png", "atlas_columns": cols,
        "tile_count": n,
        "tiles": KIT.tile_names,
        "tile_family": {str(k): v for k, v in KIT.tile_family.items()},
        "metatile_order": "[top-left, top-right, bottom-left, bottom-right] tile indices",
        "metatiles": KIT.meta,
        "metatile_info": KIT.info,
        "autotile": autotile,
        "building": {
            "rule": "w x h metatiles; rows 0..h-2 roof, row h-1 facade. Roof edges: "
                    "_n on row 0, _w on column 0, _e on column w-1 (combine, e.g. roof_tudor_nw). "
                    "Facade: *_l / *_r on the end columns. roof_tudor_gable may replace any "
                    "roof cell in row h-2. facade_ddoor_l + facade_ddoor_r form a 2-wide door.",
            "roof_styles": ["tudor", "flat"],
            "facades": ["facade_wall", "facade_window", "facade_ribbon", "facade_door",
                        "facade_ddoor_l", "facade_ddoor_r", "facade_plaque"],
            "helper": "make_art.building_layout(w, h, roof, doors, ddoors, gables, plaques)",
        },
    }
    p = os.path.join(ROOT, "tiles", "kit.json")
    with open(p, "w") as f: json.dump(kit, f, indent=1)
    written.append(p)

    # sprites
    for fn, lst in (("player.png", STRIP_MAIN), ("player_left.png", STRIP_LEFT),
                    ("player_ext.png", STRIP_EXT)):
        p = os.path.join(ROOT, "sprites", fn); to_img(strip(lst)).save(p); written.append(p)
    p = os.path.join(ROOT, "sprites", "signpost.png"); to_img(SIGNPOST_SPRITE).save(p); written.append(p)
    pj = {
        "frame_size": [16, 16],
        "transparent": PALETTE_HEX[SPRITE_KEY],
        "transparent_note": "sprites use only #86c06c/#306850/#071821; #e0f8cf is background. "
                            "Change SPRITE_KEY in make_art.py if GB Studio needs another key.",
        "sheets": {
            "player.png": {"layout": "horizontal strip", "frames": STRIP_MAIN,
                           "note": "left = right mirrored (engine flip)"},
            "player_left.png": {"layout": "horizontal strip", "frames": STRIP_LEFT},
            "player_ext.png": {"layout": "horizontal strip", "frames": STRIP_EXT,
                               "note": "walk cycle per direction: idle, walk, idle, walk_b"},
        },
        "suggested_collision_box": {"x": 0, "y": 8, "w": 16, "h": 8,
                                    "note": "feet half of the frame, pixels from top-left"},
        "relayout": "edit STRIP_* lists in make_art.py and rerun",
    }
    p = os.path.join(ROOT, "sprites", "player.json")
    with open(p, "w") as f: json.dump(pj, f, indent=1)
    written.append(p)

    # preview
    img, scene = preview_scene()
    p = os.path.join(ROOT, "preview_1x.png"); to_img(img).save(p); written.append(p)
    p = os.path.join(ROOT, "preview.png"); to_img(img, 4).save(p); written.append(p)

    # player over every ground type
    grounds = [
        ("lawn_a", None), ("lawn_b", None), ("lawn_c", None),
        ("path N-S", "path"), ("path cross", "pathx"), ("plaza", "paved_plaza"),
        ("parking", "paved_parking"), ("road", "road"), ("road kerb", "roadk"),
        ("bridge_h", "bridge_h"), ("bridge_v", "bridge_v"), ("shore", "shore"),
        ("forest edge", "forest"), ("ledge", "ledge"), ("stairs", "stairs"),
        ("cliff base", "cliff"), ("door", "door"), ("tree", "tree"),
    ]
    per_row, sc = 6, 3
    cw, ch = 48 * sc + 6, 48 * sc + 18
    im = Image.new("RGB", (per_row * cw + 6, ((len(grounds) + per_row - 1) // per_row) * ch + 6),
                   tuple(RGB[W]))
    dr = ImageDraw.Draw(im)
    for i, (lab, kind) in enumerate(grounds):
        g = [[lab if kind is None else "lawn_a"] * 3 for _ in range(3)]
        sx, sy = 16, 16
        if kind == "path": g = [["lawn_a", PATH[5], "lawn_a"]] * 3
        if kind == "pathx": g = [["lawn_a", PATH[5], "lawn_a"], [PATH[10], PATH[15], PATH[10]], ["lawn_a", PATH[5], "lawn_a"]]
        if kind in ("paved_plaza", "paved_parking", "bridge_h", "bridge_v", "stairs", "tree"):
            g = [[kind] * 3 for _ in range(3)]
        if kind == "tree": g = [["lawn_a", "tree", "lawn_a"], ["lawn_a"] * 3, ["lawn_a"] * 3]
        if kind == "road": g = [[ROAD[255]] * 3 for _ in range(3)]
        if kind == "roadk":
            g = [["lawn_a"] * 3, [ROAD[canon(2 | 4 | 8 | 0xF0)]] * 3, [ROAD[255]] * 3]
        if kind == "shore":
            g = [["lawn_a"] * 3, ["lawn_a"] * 3, [WATER[canon(2 | 4 | 8 | 0xF0)]] * 3]
            sy = 8
        if kind == "forest":
            g = [[FOREST[canon(0xFF & ~4 & ~32 & ~64)]] * 3, ["lawn_a"] * 3, ["lawn_a"] * 3]
        if kind == "ledge": g = [["ledge_l", "ledge", "ledge_r"], ["lawn_a"] * 3, ["lawn_a"] * 3]; sy = 8
        if kind == "cliff": g = [["cliff_face"] * 3, ["cliff_base"] * 3, ["lawn_a"] * 3]; sy = 24
        if kind == "door":
            g = [["roof_tudor_nw", "roof_tudor_n", "roof_tudor_ne"],
                 ["facade_window_l", "facade_door", "facade_window_r"], ["lawn_a"] * 3]
            sy = 24
        patch = render_grid(g)
        blit_sprite(patch, FRAMES["down_idle" if i % 2 == 0 else "right_walk"], sx, sy)
        x, y = 3 + (i % per_row) * cw, 3 + (i // per_row) * ch
        im.paste(to_img(patch, sc), (x, y))
        label(dr, (x, y + 48 * sc + 2), lab, tuple(RGB[K]))
    p = os.path.join(ROOT, "player_on_grounds.png"); im.save(p); written.append(p)

    for p in written:
        if p.endswith(".png"): check_palette(p)
    return written, scene


if __name__ == "__main__":
    out, _ = write_all()
    fam = {}
    for i, f in KIT.tile_family.items(): fam[f] = fam.get(f, 0) + 1
    print(f"unique 8x8 tiles: {len(KIT.tiles)}   metatiles: {len(KIT.meta)}")
    print("tiles per family:", fam)
    for p in out: print("wrote", os.path.relpath(p, ROOT))
