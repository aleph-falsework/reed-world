#!/usr/bin/env python3
"""Reed style kit: original, procedural, tileable PBR-ish textures (numpy + Pillow).

    python3 world/buildings/kit/make_kit.py          # writes world/buildings/kit/textures/*.png + materials.json

Every material gets three maps:
    T_<Name>_BC.png   base colour (sRGB)
    T_<Name>_N.png    tangent-space normal, OpenGL / glTF convention (+Y = up in the image).
                      Unreal expects DirectX (-Y): import_reed.py sets flip_green_channel on import.
    T_<Name>_ORM.png  R = ambient occlusion (1), G = roughness, B = metallic (glTF metallicRoughness layout;
                      Unreal samples G and B)

Each texture covers a fixed real-world size (materials.json "tile_m"), and the meshes from
scripts/build_buildings.py carry UVs = metres / tile_m, so everything renders at true scale.
Bricks are 20 x 6.5 cm with 1 cm joints (a 21 x 7.5 cm module).
"""
import json
import os

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
TEX = os.path.join(HERE, 'textures')


# ------------------------------------------------------------------ helpers (all periodic -> tileable)
def fnoise(h, w, beta=1.8, seed=0, cutoff=None):
    """Tileable fractal noise, zero mean, unit std. beta: spectral falloff (higher = smoother)."""
    rng = np.random.default_rng(seed)
    F = np.fft.fft2(rng.standard_normal((h, w)))
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.fftfreq(w)[None, :]
    k = np.sqrt(fx * fx + fy * fy)
    k[0, 0] = 1.0
    F *= k ** (-beta / 1.0)
    if cutoff:
        F *= np.exp(-(k / cutoff) ** 2)
    F[0, 0] = 0
    n = np.real(np.fft.ifft2(F))
    return (n - n.mean()) / (n.std() + 1e-9)


def smooth(x, e0, e1):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def normal_from_height(hgt, strength):
    """OpenGL convention: nx = -dh/dx, ny = +dh/d(row) (image up = +Y). Periodic gradients."""
    dx = (np.roll(hgt, -1, 1) - np.roll(hgt, 1, 1)) * 0.5
    dy = (np.roll(hgt, -1, 0) - np.roll(hgt, 1, 0)) * 0.5
    n = np.stack([-dx * strength, dy * strength, np.ones_like(hgt)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return np.clip((n * 0.5 + 0.5) * 255 + 0.5, 0, 255).astype(np.uint8)


def to8(rgb):
    return np.clip(rgb * 255 + 0.5, 0, 255).astype(np.uint8)


def srgb(*c):
    return np.array(c, dtype=np.float64) / 255.0


def save_set(name, bc, hgt, strength, rough, metal=0.0):
    os.makedirs(TEX, exist_ok=True)
    Image.fromarray(to8(bc)).save(os.path.join(TEX, f'T_{name}_BC.png'), optimize=True)
    Image.fromarray(normal_from_height(hgt, strength)).save(os.path.join(TEX, f'T_{name}_N.png'), optimize=True)
    h, w = bc.shape[:2]
    rough = np.broadcast_to(np.asarray(rough, dtype=np.float64), (h, w))
    metal = np.broadcast_to(np.asarray(metal, dtype=np.float64), (h, w))
    orm = np.stack([np.ones((h, w)), rough, metal], -1)
    Image.fromarray(to8(orm)).save(os.path.join(TEX, f'T_{name}_ORM.png'), optimize=True)


def grid_cells(N, nx, ny, stagger=0.5, Wpx=None):
    """Running-bond layout over an N x N (or Wpx x N) image: returns row index, cell index, local x/y in px."""
    W = Wpx or N
    i, j = np.mgrid[0:N, 0:W].astype(np.float64)
    ch = N / ny
    cw = W / nx
    row = np.floor(i / ch).astype(int)
    off = (row % 2) * stagger * cw
    xx = (j + off) % W
    col = np.floor(xx / cw).astype(int) % nx
    lx = xx - col * cw
    ly = i - row * ch
    return row, col, lx, ly, cw, ch


# ------------------------------------------------------------------ materials
def brick(name, N, tile_m, base, spread, clinker_frac, clinker_rgb, mortar_rgb, seed, headers=False, low=1.0):
    """Running bond, 20 x 6.5 cm bricks, 1 cm joints. tile_m must be a multiple of the 0.21 x 0.075 m module."""
    nx, ny = round(tile_m / 0.21), round(tile_m / 0.075)
    rng = np.random.default_rng(seed)
    row, col, lx, ly, cw, ch = grid_cells(N, nx, ny)
    if headers:   # Flemish-ish: every 6th course is a header course (half-length bricks), as in Reed's Gothic walls
        hdr = (row % 6) == 5
        cw2 = cw / 2
        i, j = np.mgrid[0:N, 0:N].astype(np.float64)
        xx = (j + (row % 2) * cw2 * 0.5) % N
        col2 = np.floor(xx / cw2).astype(int)
        lx = np.where(hdr, xx - col2 * cw2, lx)
        col = np.where(hdr, 1000 + col2, col)
        cwv = np.where(hdr, cw2, cw)
    else:
        cwv = np.full_like(lx, cw)
    mort = 0.01 * N / tile_m                                 # 1 cm joint in px
    # distance to the brick edge (joint on the left and top of each cell)
    dxl = lx - mort
    dxr = cwv - lx
    dyt = ly - mort
    dyb = ch - ly
    d = np.minimum(np.minimum(dxl, dxr), np.minimum(dyt, dyb))
    is_brick = d > 0
    key = row * 4096 + col
    uk, inv = np.unique(key, return_inverse=True)
    inv = inv.reshape(key.shape)
    tone = rng.normal(0, 1, len(uk))
    clink = rng.random(len(uk)) < clinker_frac
    burnt = rng.random(len(uk)) < 0.12                        # darker fire-flashed ends
    t = tone[inv][..., None]
    col_rgb = base[None, None, :] * (1 + spread * t)
    col_rgb = np.where(clink[inv][..., None], clinker_rgb * (1 + 0.15 * t), col_rgb)
    # fire flash towards one end of some bricks
    flash = burnt[inv] * smooth(lx / cwv, 0.55, 1.0)
    col_rgb = col_rgb * (1 - 0.35 * flash[..., None])
    n1 = fnoise(N, N, 1.2, seed + 1)
    n2 = fnoise(N, N, 2.4, seed + 2)
    col_rgb = col_rgb * (1 + 0.06 * low * n1[..., None] + 0.05 * n2[..., None])
    mor = mortar_rgb * (1 + 0.08 * n1[..., None])
    bc = np.where(is_brick[..., None], col_rgb, mor)
    # soften the edge into the joint
    edge = smooth(d, 0, 1.6)
    bc = mor * (1 - edge[..., None]) + bc * edge[..., None]
    hgt = 0.15 + 0.85 * smooth(d, -0.5, 2.5) + 0.06 * n1 + 0.03 * fnoise(N, N, 1.0, seed + 3)
    # chipped arrises
    chips = (fnoise(N, N, 1.4, seed + 4) > 1.6) & (d < 4)
    hgt = np.where(chips, hgt * 0.6, hgt)
    rough = np.where(is_brick, 0.82, 0.95) - 0.25 * clink[inv] * is_brick + 0.04 * n2
    save_set(name, bc, hgt, 2.2, np.clip(rough, 0, 1))
    return dict(tile_m=[tile_m, tile_m], size=N, note=f'{nx} bricks x {ny} courses, running bond')


def stone(name, N, tile_m, base, seed, blocks=(2, 4), joint_m=0.006):
    """Cast stone / limestone ashlar: tile_m wide, blocks (cols, rows), fine joints, soft tooling noise."""
    nx, ny = blocks
    row, col, lx, ly, cw, ch = grid_cells(N, nx, ny)
    j = joint_m * N / tile_m
    d = np.minimum(np.minimum(lx - j, cw - lx), np.minimum(ly - j, ch - ly))
    rng = np.random.default_rng(seed)
    uk, inv = np.unique(row * 64 + col, return_inverse=True)
    tone = rng.normal(0, 1, len(uk))[inv.reshape(row.shape)]
    n1, n2, n3 = fnoise(N, N, 1.3, seed), fnoise(N, N, 2.6, seed + 1), fnoise(N, N, 0.6, seed + 2)
    bc = base[None, None] * (1 + 0.035 * tone[..., None] + 0.05 * n2[..., None] + 0.025 * n1[..., None])
    # weathering: darker streaks running down
    streak = fnoise(N, N, 2.0, seed + 5)
    streak = np.repeat(streak.mean(axis=0, keepdims=True), N, 0) * 0.5 + 0.5 * n2
    bc *= (1 - 0.06 * np.clip(streak, 0, 3)[..., None])
    jm = smooth(d, 0, 1.2)
    bc = bc * (0.72 + 0.28 * jm[..., None])
    hgt = smooth(d, -0.5, 2.0) * 0.6 + 0.04 * n3 + 0.015 * n1
    save_set(name, bc, hgt, 1.6, np.clip(0.8 + 0.05 * n1, 0, 1))
    return dict(tile_m=[tile_m, tile_m], size=N, note=f'ashlar {nx}x{ny} blocks')


def slate(name, N, tile_m, seed):
    """Natural slate, 30 cm wide, 20 cm exposure, half-lap stagger; image top = up-slope."""
    nx, ny = round(tile_m / 0.30), round(tile_m / 0.20)
    row, col, lx, ly, cw, ch = grid_cells(N, nx, ny)
    rng = np.random.default_rng(seed)
    uk, inv = np.unique(row * 64 + col, return_inverse=True)
    inv = inv.reshape(row.shape)
    tone = rng.normal(0, 1, len(uk))[inv]
    hue = rng.normal(0, 1, len(uk))[inv]
    # slightly irregular lower edges
    jag = rng.normal(0, 0.06, len(uk))[inv] * ch
    gap = 0.004 * N / tile_m
    n1, n2 = fnoise(N, N, 1.1, seed), fnoise(N, N, 2.2, seed + 1)
    # cleavage texture: stretched noise
    cleave = fnoise(N, N * 4, 1.6, seed + 2)[:, ::4]
    base = srgb(62, 66, 74)
    rgb = base[None, None] * (1 + 0.10 * tone[..., None]) + np.stack([0.0 * hue, 0.012 * hue, 0.02 * hue], -1)
    rgb = rgb * (1 + 0.05 * n1[..., None] + 0.04 * cleave[..., None])
    side = np.minimum(lx - gap, cw - lx)
    bot = ch + jag - ly                                       # distance to this slate's lower (exposed) edge
    in_gap = side < 0
    t = ly / ch                                               # 0 top (tucked under the course above) .. 1 butt
    shadow = smooth(t, 0.0, 0.18)                             # shadow cast by the butt of the course above
    rgb = rgb * (0.55 + 0.45 * shadow[..., None])
    rgb = np.where(in_gap[..., None], rgb * 0.35, rgb)
    hgt = t * 0.9 + 0.05 * cleave + 0.02 * n2
    hgt = np.where(in_gap, 0.0, hgt)
    hgt = np.where(bot < 0, 0.0, hgt)
    rough = 0.62 + 0.08 * n1 - 0.06 * tone
    save_set(name, rgb, hgt, 2.4, np.clip(rough, 0, 1))
    return dict(tile_m=[tile_m, tile_m], size=N, note=f'{nx} slates x {ny} courses, 30 x 20 cm exposure')


def shingle(name, N, tile_m, seed, base=(70, 66, 64)):
    """3-tab asphalt shingle, 1/3 m tabs, 12.5 cm exposure; image top = up-slope."""
    nx, ny = round(tile_m / (1 / 3)), round(tile_m / 0.125)
    row, col, lx, ly, cw, ch = grid_cells(N, nx, ny, stagger=0.5)
    rng = np.random.default_rng(seed)
    uk, inv = np.unique(row * 64 + col, return_inverse=True)
    inv = inv.reshape(row.shape)
    tone = rng.normal(0, 1, len(uk))[inv]
    grit = fnoise(N, N, 0.3, seed)                           # granules: nearly white noise
    grit2 = fnoise(N, N, 1.0, seed + 1)
    slot = 0.006 * N / tile_m
    t = ly / ch
    rgb = srgb(*base)[None, None] * (1 + 0.04 * tone[..., None] + 0.12 * grit[..., None] + 0.04 * grit2[..., None])
    # occasional lighter/darker granule blends
    rgb *= (1 + 0.05 * np.sign(fnoise(N, N, 1.8, seed + 2))[..., None] * 0.5)
    rgb *= (0.45 + 0.55 * smooth(t, 0.0, 0.16))[..., None]
    gap = (lx < slot) | (lx > cw - slot)
    rgb = np.where(gap[..., None], rgb * 0.45, rgb)
    hgt = 0.3 + 0.7 * t + 0.05 * grit
    hgt = np.where(gap, 0.0, hgt)
    save_set(name, rgb, hgt, 1.6, np.clip(0.9 + 0.04 * grit, 0, 1))
    return dict(tile_m=[tile_m, tile_m], size=N, note='3-tab, 1/3 m tabs, 12.5 cm exposure')


def standing_seam(name, N, tile_m, seed, base=(64, 70, 72)):
    """Standing-seam metal, seams every 45 cm along U (seams run up the slope, i.e. along V)."""
    ns = round(tile_m / 0.45)
    j = np.arange(N, dtype=np.float64)
    pw = N / ns
    lx = j % pw
    dc = np.minimum(lx, pw - lx)                              # distance to the seam
    seam = np.exp(-(dc / (0.012 * N / tile_m)) ** 2)
    pan = 0.03 * np.cos((lx / pw) * 2 * np.pi * 3)           # faint stiffening ribs in the pan
    h1 = np.tile(seam + pan * 0.3, (N, 1))
    n1 = fnoise(N, N, 2.0, seed)
    stretch = np.repeat(fnoise(N, N // 8, 1.6, seed + 1), 8, axis=1)
    hgt = h1 + 0.003 * n1
    rgb = srgb(*base)[None, None] * (1 + 0.05 * stretch[..., None] + 0.03 * n1[..., None])
    rgb = rgb * (1 + 0.12 * h1[..., None])
    save_set(name, rgb, hgt, 6.0, np.clip(0.42 + 0.06 * stretch, 0, 1), 0.25)
    return dict(tile_m=[tile_m, tile_m], size=N, note=f'{ns} pans, 45 cm seams along V')


def render(name, N, tile_m, base, seed, rough=0.88, fine=0.07, coarse=0.04):
    n1, n2, n3 = fnoise(N, N, 0.4, seed), fnoise(N, N, 1.4, seed + 1), fnoise(N, N, 2.6, seed + 2)
    rgb = srgb(*base)[None, None] * (1 + fine * 0.5 * n1[..., None] + coarse * n2[..., None] + 0.04 * n3[..., None])
    hgt = 0.5 * n1 + 0.3 * n2
    save_set(name, rgb, hgt, 0.35, np.clip(rough + 0.03 * n2, 0, 1))
    return dict(tile_m=[tile_m, tile_m], size=N, note='trowelled render / stucco')


def concrete(name, N, tile_m, seed, base=(150, 148, 142)):
    """Board-marked concrete: 1.2 x 0.6 m form panels (2 x 4 per tile) with tie holes."""
    nx, ny = round(tile_m / 1.2), round(tile_m / 0.6)
    i, j = np.mgrid[0:N, 0:N].astype(np.float64)
    pw, ph = N / nx, N / ny
    lx, ly = j % pw, i % ph
    d = np.minimum(np.minimum(lx, pw - lx), np.minimum(ly, ph - ly))
    joint = d < 0.004 * N / tile_m
    ties = (np.hypot(np.minimum(lx, pw - lx) - pw * 0.25 * 0 - 0.15 * N / tile_m,
                     ly - ph / 2) < 0.012 * N / tile_m)
    n1, n2, n3 = fnoise(N, N, 0.5, seed), fnoise(N, N, 1.6, seed + 1), fnoise(N, N, 2.8, seed + 2)
    boards = np.repeat(fnoise(N, 16, 1.0, seed + 3), N // 16, axis=1)[:, :N]   # vertical board grain
    pores = n1 > 2.2
    rgb = srgb(*base)[None, None] * (1 + 0.04 * n2[..., None] + 0.05 * n3[..., None] + 0.025 * boards[..., None])
    rgb = np.where((joint | ties)[..., None], rgb * 0.6, rgb)
    rgb = np.where(pores[..., None], rgb * 0.7, rgb)
    hgt = 0.04 * n2 + 0.02 * boards - 0.3 * joint - 0.5 * ties - 0.3 * pores
    save_set(name, rgb, hgt, 1.6, np.clip(0.85 + 0.05 * n2, 0, 1))
    return dict(tile_m=[tile_m, tile_m], size=N, note='board-marked, 1.2 x 0.6 m form panels')


def curtain(name, N, tile_m, seed):
    """Curtain wall: 1.5 m panels (2 x 2 per 3 m tile), 6 cm mullions; image top = up."""
    n = round(tile_m / 1.5)
    i, j = np.mgrid[0:N, 0:N].astype(np.float64)
    pw = N / n
    lx, ly = j % pw, i % pw
    mw = 0.03 * N / tile_m
    dm = np.minimum(np.minimum(lx, pw - lx), np.minimum(ly, pw - ly))
    mull = dm < mw
    # sky reflection: vertical gradient per panel with a soft diagonal sheen, different per panel
    rng = np.random.default_rng(seed)
    pid = (np.floor(i / pw) * 8 + np.floor(j / pw)).astype(int)
    shift = rng.normal(0, 1, 64)[pid]
    g = (ly / pw)
    sky = np.stack([0.30 + 0.10 * g, 0.38 + 0.10 * g, 0.44 + 0.08 * g], -1)
    sheen = smooth(np.sin((lx + ly * 0.6) / pw * 2.4 + shift), 0.6, 1.0) * 0.08
    rgb = sky * (1 + 0.06 * shift[..., None]) + sheen[..., None]
    rgb = np.where(mull[..., None], srgb(46, 48, 50), rgb)
    hgt = np.where(mull, 1.0, 0.0) * smooth(mw - dm + 1, 0, 2)
    rough = np.where(mull, 0.45, 0.06)
    metal = np.where(mull, 0.3, 0.0)
    save_set(name, rgb, hgt, 3.0, rough, metal)
    return dict(tile_m=[tile_m, tile_m], size=N, note='1.5 m panels, 6 cm dark mullions')


def siding(name, N, tile_m, seed, base):
    """Painted lap (bevel) siding, 15 cm exposure; image top = up."""
    nb = round(tile_m / 0.15)
    i, j = np.mgrid[0:N, 0:N].astype(np.float64)
    bh = N / nb
    ly = i % bh
    t = ly / bh                                               # 0 top of exposed board .. 1 bottom lip
    grain = np.repeat(fnoise(N // 8, N, 1.4, seed), 8, axis=0)
    n2 = fnoise(N, N, 2.2, seed + 1)
    rgb = srgb(*base)[None, None] * (1 + 0.02 * grain[..., None] + 0.025 * n2[..., None])
    rgb *= (0.68 + 0.32 * smooth(t, 0.0, 0.10))[..., None]   # shadow under the lip above
    # butt joints between boards, staggered
    row = np.floor(i / bh).astype(int)
    rng = np.random.default_rng(seed)
    offs = rng.random(nb) * N
    butt = ((j + offs[row]) % (N / 1.0)) < 0.003 * N / tile_m
    rgb = np.where(butt[..., None], rgb * 0.7, rgb)
    hgt = 0.2 + 0.8 * t + 0.01 * grain
    save_set(name, rgb, hgt, 2.0, np.clip(0.7 + 0.03 * n2, 0, 1))
    return dict(tile_m=[tile_m, tile_m], size=N, note=f'{nb} boards, 15 cm exposure')


def gravel_roof(name, N, tile_m, seed):
    n1, n2 = fnoise(N, N, 0.2, seed), fnoise(N, N, 1.8, seed + 1)
    rgb = srgb(92, 92, 90)[None, None] * (1 + 0.10 * n1[..., None] + 0.035 * n2[..., None])
    save_set(name, rgb, 0.6 * n1 + 0.4 * n2, 0.7, np.clip(0.93 + 0.03 * n1, 0, 1))
    return dict(tile_m=[tile_m, tile_m], size=N, note='ballasted membrane (flat roofs)')


def glass(name, N, tile_m, base, seed, lead=None):
    """Window glass. lead=(cols, rows) adds leaded quarries (Gothic) with dark cames."""
    i, j = np.mgrid[0:N, 0:N].astype(np.float64)
    n1 = fnoise(N, N, 2.4, seed)
    g = 0.5 + 0.5 * np.cos(2 * np.pi * i / N)
    rgb = srgb(*base)[None, None] * (1 + 0.06 * n1[..., None]) + (0.05 * g)[..., None]
    hgt = 0.02 * n1
    rough = np.full((N, N), 0.05)
    metal = np.zeros((N, N))
    if lead:
        cx, cy = lead
        pw, ph = N / cx, N / cy
        lx, ly = j % pw, i % ph
        d = np.minimum(np.minimum(lx, pw - lx), np.minimum(ly, ph - ly))
        came = d < 0.008 * N / tile_m
        # each quarry slightly different in tint and tilt (old cylinder glass)
        rng = np.random.default_rng(seed + 9)
        q = (np.floor(i / ph) * 64 + np.floor(j / pw)).astype(int)
        tint = rng.normal(0, 1, 64 * 64)[q]
        rgb = rgb * (1 + 0.08 * tint[..., None])
        rgb = np.where(came[..., None], srgb(40, 40, 42), rgb)
        hgt = hgt + np.where(came, 1.0, 0.0) + 0.05 * tint * (lx / pw)
        rough = np.where(came, 0.6, 0.05)
        metal = np.where(came, 0.3, 0.0)
    save_set(name, rgb, hgt, 2.0, rough, metal)
    return dict(tile_m=[tile_m, tile_m], size=N, note='leaded quarries' if lead else 'clear glass')


def paint(name, N, tile_m, base, seed, rough=0.6, metal=0.0):
    n1, n2 = fnoise(N, N, 1.5, seed), fnoise(N, N, 2.5, seed + 1)
    rgb = srgb(*base)[None, None] * (1 + 0.025 * n1[..., None] + 0.02 * n2[..., None])
    save_set(name, rgb, 0.1 * n1, 0.5, np.clip(rough + 0.03 * n1, 0, 1), metal)
    return dict(tile_m=[tile_m, tile_m], size=N, note='painted / finished')


def window_decal(name, W, H, frame_rgb, glass_rgb, seed, kind='double_hung'):
    """A whole window as one texture (0..1 over the window quad), for the neighbourhood. Image top = top."""
    i, j = np.mgrid[0:H, 0:W].astype(np.float64)
    u, v = j / W, i / H
    fw = 0.07
    n1 = fnoise(H, W, 2.2, seed)
    glass = np.stack([glass_rgb[0] + 0.10 * (1 - v), glass_rgb[1] + 0.10 * (1 - v), glass_rgb[2] + 0.09 * (1 - v)], -1)
    glass = glass * (1 + 0.1 * n1[..., None])
    # sheen
    glass += (0.07 * smooth(np.sin((u + v * 0.7) * 7.0), 0.75, 1.0))[..., None]
    frame = (u < fw) | (u > 1 - fw) | (v < fw) | (v > 1 - fw * 1.4)
    if kind == 'double_hung':
        frame |= np.abs(v - 0.5) < 0.03                     # meeting rail
        frame |= (np.abs(u - 0.5) < 0.012) & (v < 0.5)       # upper sash muntin (2-over-1)
    else:                                                    # casement / modern: one mullion, transom
        frame |= np.abs(u - 0.5) < 0.02
        frame |= np.abs(v - 0.28) < 0.02
    # sill shadow / reveal shading
    shade = 1 - 0.35 * smooth(0.12 - v, 0, 0.12)
    rgb = np.where(frame[..., None], srgb(*frame_rgb) * (1 + 0.03 * n1[..., None]), glass * shade[..., None])
    hgt = np.where(frame, 1.0, 0.0)
    rough = np.where(frame, 0.55, 0.06)
    save_set(name, rgb, hgt, 2.5, rough)
    return dict(tile_m=None, size=[W, H], note=f'whole-window decal ({kind}), UV 0..1 over the window')


def door(name, W, H, seed, wood=(92, 52, 30)):
    """Panelled timber door, UV 0..1 over the leaf (1.0 x 2.2 m). Image top = top."""
    i, j = np.mgrid[0:H, 0:W].astype(np.float64)
    u, v = j / W, i / H
    grain = np.repeat(fnoise(H, W // 4, 1.3, seed), 4, axis=1)
    rgb = srgb(*wood)[None, None] * (1 + 0.10 * grain[..., None])
    hgt = np.zeros((H, W))
    for (u0, u1, v0, v1) in ((0.14, 0.86, 0.08, 0.40), (0.14, 0.86, 0.48, 0.92)):
        inside = (u > u0) & (u < u1) & (v > v0) & (v < v1)
        d = np.minimum(np.minimum(u - u0, u1 - u) * W, np.minimum(v - v0, v1 - v) * H)
        hgt -= inside * (0.6 - 0.6 * smooth(d, 0, 8))
        rgb = np.where(inside[..., None], rgb * 0.88, rgb)
    knob = np.hypot((u - 0.85) * W, (v - 0.55) * H) < W * 0.025
    rgb = np.where(knob[..., None], srgb(150, 120, 60), rgb)
    save_set(name, rgb, hgt + 0.02 * grain, 3.0, np.where(knob, 0.3, 0.55), np.where(knob, 0.9, 0.0))
    return dict(tile_m=None, size=[W, H], note='panelled door leaf, UV 0..1')


# ------------------------------------------------------------------ the kit
def build():
    M = {}
    M['Brick_Gothic'] = brick('Brick_Gothic', 1024, 2.10, srgb(128, 58, 42), 0.11, 0.07, srgb(82, 46, 44),
                              srgb(176, 166, 148), seed=11, headers=True)
    M['Brick_Common'] = brick('Brick_Common', 1024, 2.10, srgb(150, 78, 54), 0.07, 0.02, srgb(96, 52, 40),
                              srgb(160, 156, 148), seed=21, low=0.35)
    M['Trim_Stone'] = stone('Trim_Stone', 512, 1.2, srgb(198, 188, 166), seed=31, blocks=(2, 4))
    M['Roof_Slate'] = slate('Roof_Slate', 1024, 1.2, seed=41)
    M['Roof_Shingle'] = shingle('Roof_Shingle', 512, 1.0, seed=51)
    M['Roof_Metal'] = standing_seam('Roof_Metal', 512, 0.9, seed=61, base=(78, 86, 88))
    M['Roof_Flat'] = gravel_roof('Roof_Flat', 512, 2.0, seed=71)
    M['Stucco'] = render('Stucco', 512, 2.0, (214, 204, 182), seed=81)
    M['Concrete'] = concrete('Concrete', 512, 2.4, seed=91)
    M['Glass_Curtain'] = curtain('Glass_Curtain', 512, 3.0, seed=101)
    M['Siding_Wood'] = siding('Siding_Wood', 512, 1.2, seed=111, base=(222, 218, 204))
    M['Siding_Wood_Dark'] = siding('Siding_Wood_Dark', 512, 1.2, seed=112, base=(112, 122, 104))
    M['Glass'] = glass('Glass', 256, 1.0, (44, 56, 64), seed=121)
    M['Glass_Leaded'] = glass('Glass_Leaded', 512, 0.6, (52, 62, 66), seed=122, lead=(3, 3))
    M['Paint_Trim'] = paint('Paint_Trim', 256, 1.0, (232, 228, 216), seed=131)
    M['Metal_Dark'] = paint('Metal_Dark', 256, 1.0, (58, 60, 62), seed=141, rough=0.45, metal=0.3)
    M['Wood_Timber'] = paint('Wood_Timber', 256, 1.0, (70, 46, 30), seed=151, rough=0.7)
    M['Window_House'] = window_decal('Window_House', 256, 384, (236, 232, 222), (0.20, 0.25, 0.29), 161)
    M['Window_Dark'] = window_decal('Window_Dark', 256, 384, (48, 46, 44), (0.18, 0.23, 0.27), 162, kind='casement')
    M['Door_Wood'] = door('Door_Wood', 256, 512, 171)
    # PBR factors that go with the maps (the glTF material and the Unreal MI use them)
    for k, v in M.items():
        v.update(base_color=f'T_{k}_BC.png', normal=f'T_{k}_N.png', orm=f'T_{k}_ORM.png', slot='M_' + k)
    json.dump(dict(note='tile_m = real-world size covered by one texture repeat; mesh UVs = metres / tile_m. '
                        'Normals are OpenGL (+Y up). ORM = (AO, roughness, metallic).', materials=M),
              open(os.path.join(HERE, 'materials.json'), 'w'), indent=1)
    return M


if __name__ == '__main__':
    M = build()
    print('wrote', len(M), 'materials to', TEX)
