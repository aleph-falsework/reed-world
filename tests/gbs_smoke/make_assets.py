"""Throwaway placeholder art for the GB Studio smoke test (4-colour DMG only).

Writes into tests/gbs_smoke/assets/:
  scene1.png  160x144  one screen: border, a solid wall at column 12, a door at the top, big "1"
  scene2.png  192x160  scrolls a little: stripes, big "2"
  player.png  96x16    GB Studio's 6-frame layout: down0 down1 up0 up1 right0 right1
  signpost.png 16x16
"""
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parent / "assets"
PAL = {0: (0xE0, 0xF8, 0xCF), 1: (0x86, 0xC0, 0x6C), 2: (0x30, 0x68, 0x50), 3: (0x07, 0x18, 0x21)}

DIGITS = {
    "1": ["0110", "1110", "0110", "0110", "0110", "0110", "1111"],
    "2": ["1110", "0001", "0001", "0110", "1000", "1000", "1111"],
}


def save_idx(idx, path):
    """Background: index 0..3 -> lightest..darkest."""
    rgb = np.zeros(idx.shape + (3,), np.uint8)
    for k, c in PAL.items():
        rgb[idx == k] = c
    Image.fromarray(rgb, "RGB").save(path)


def save_sprite(idx, path):
    """Sprite: 0 transparent, 1 #e0f8cf, 2 #86c06c, 3 #071821 (sprites have 3 colours)."""
    rgba = np.zeros(idx.shape + (4,), np.uint8)
    for k, c in ((1, PAL[0]), (2, PAL[1]), (3, PAL[3])):
        rgba[idx == k, :3] = c
        rgba[idx == k, 3] = 255
    Image.fromarray(rgba, "RGBA").save(path)


def tile_fill(img, tx, ty, tile):
    img[ty * 8 : ty * 8 + 8, tx * 8 : tx * 8 + 8] = tile


def grass_tile():
    t = np.zeros((8, 8), np.uint8)
    t[2, 1] = t[5, 5] = t[6, 2] = 1
    return t


def brick_tile():
    t = np.full((8, 8), 3, np.uint8)
    t[3, :] = 2
    t[7, :] = 2
    t[0:3, 3] = 2
    t[4:7, 7] = 2
    return t


def border_tile():
    t = np.full((8, 8), 2, np.uint8)
    t[::2, ::2] = 3
    return t


def door_tile():
    t = np.full((8, 8), 3, np.uint8)
    t[1:, 1:7] = 0
    return t


def stripe_tile():
    t = np.zeros((8, 8), np.uint8)
    t[:, :] = 1
    t[::4, :] = 0
    return t


def big_digit(img, ch, px, py, scale=4, colour=3):
    for r, row in enumerate(DIGITS[ch]):
        for c, v in enumerate(row):
            if v == "1":
                img[py + r * scale : py + (r + 1) * scale, px + c * scale : px + (c + 1) * scale] = colour


def scene1():
    w, h = 20, 18
    img = np.zeros((h * 8, w * 8), np.uint8)
    col = np.zeros((h, w), int)
    for ty in range(h):
        for tx in range(w):
            tile_fill(img, tx, ty, grass_tile())
    for ty in range(h):
        for tx in range(w):
            if tx in (0, w - 1) or ty in (0, 1, h - 1):
                tile_fill(img, tx, ty, border_tile())
                col[ty, tx] = 1
    for ty in range(2, h - 1):  # the wall to test collisions
        tile_fill(img, 12, ty, brick_tile())
        col[ty, 12] = 1
    for tx in (7, 8):  # the door in the top border (exit trigger sits below it)
        tile_fill(img, tx, 1, door_tile())
    big_digit(img, "1", 15 * 8, 4 * 8)
    return img, col


def scene2():
    w, h = 24, 20
    img = np.zeros((h * 8, w * 8), np.uint8)
    col = np.zeros((h, w), int)
    for ty in range(h):
        for tx in range(w):
            tile_fill(img, tx, ty, stripe_tile())
            if tx in (0, w - 1) or ty in (0, h - 1):
                tile_fill(img, tx, ty, brick_tile())
                col[ty, tx] = 1
    big_digit(img, "2", 4 * 8, 4 * 8, scale=6)
    return img, col


# ---- sprites: 0 = transparent, 1 = light, 2 = mid (#86c06c), 3 = dark
def person(facing, step):
    s = np.zeros((16, 16), np.uint8)
    # head
    s[1:7, 4:12] = 3
    s[2:6, 5:11] = 1  # light face
    if facing == "down":
        s[3, 6] = s[3, 9] = 3  # eyes
    elif facing == "up":
        s[2:6, 5:11] = 3  # back of head (hair)
    elif facing == "right":
        s[3, 9] = 3
        s[2:4, 5:7] = 3  # hair at back
    # body + backpack
    s[7:12, 4:12] = 2
    s[7:12, 4] = s[7:12, 11] = 3
    if facing == "up":
        s[7:11, 5:11] = 3  # backpack seen from behind
        s[8:10, 6:10] = 2
    elif facing == "right":
        s[7:11, 3:5] = 3  # backpack on the back (left side when facing right)
    # legs
    if step == 0:
        s[12:15, 5:7] = 3
        s[12:15, 9:11] = 3
    else:
        s[12:15, 4:6] = 3
        s[12:14, 10:12] = 3
    s[15, :] = 0
    return s


def player_sheet():
    frames = [person(f, st) for f in ("down", "up", "right") for st in (0, 1)]
    return np.concatenate(frames, axis=1)


def signpost():
    s = np.zeros((16, 16), np.uint8)
    s[2:9, 1:15] = 3
    s[3:8, 2:14] = 1
    s[4, 4:12] = 3
    s[6, 4:10] = 3
    s[9:16, 7:9] = 3
    return s


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    img1, col1 = scene1()
    img2, col2 = scene2()
    save_idx(img1, OUT / "scene1.png")
    save_idx(img2, OUT / "scene2.png")
    save_sprite(player_sheet(), OUT / "player.png")
    save_sprite(signpost(), OUT / "signpost.png")
    return col1.tolist(), col2.tolist()


if __name__ == "__main__":
    main()
