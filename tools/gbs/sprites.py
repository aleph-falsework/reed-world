"""Sprite-sheet layouts and the GB Studio 4.3.2 sprite resource (.png.gbsres).

GB Studio's CLI does NOT auto-detect sprite frames (the editor does that on
first open), so the build needs `states/animations/frames/tiles` written out.
These layouts are the ones GB Studio itself uses in its templates.

All sheets are a single row of 16x16 frames, height exactly 16 px:

  width  type             frames, left to right                       left
  -----  ---------------  ------------------------------------------  -----------
   16    fixed            [still]                                      n/a
   48    multi            [down, up, right]                            flip right
   64    multi            [down, up, right, left]                      own frames
   96    multi_movement   [down0, down1, up0, up1, right0, right1]     flip right
  128    multi_movement   [down0, down1, up0, up1, right0, right1,     own frames
                           left0, left1]

`96` is GB Studio's own `actor_animated.png` / `player.png` layout. Frame 0 of
each direction is the idle pose; walking alternates frame 1 and frame 0.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from .constants import (
    ANIM_IDLE_DOWN,
    ANIM_IDLE_LEFT,
    ANIM_IDLE_RIGHT,
    ANIM_IDLE_UP,
    ANIM_MOVE_DOWN,
    ANIM_MOVE_LEFT,
    ANIM_MOVE_RIGHT,
    ANIM_MOVE_UP,
)

FRAME = 16

# width -> (animationType, flipLeft, {anim_slot: [frame indices]})
LAYOUTS = {
    16: ("fixed", False, {0: [0]}),
    48: ("multi", True, {ANIM_IDLE_RIGHT: [2], ANIM_IDLE_UP: [1], ANIM_IDLE_DOWN: [0]}),
    64: (
        "multi",
        False,
        {ANIM_IDLE_RIGHT: [2], ANIM_IDLE_LEFT: [3], ANIM_IDLE_UP: [1], ANIM_IDLE_DOWN: [0]},
    ),
    96: (
        "multi_movement",
        True,
        {
            ANIM_IDLE_RIGHT: [4],
            ANIM_IDLE_UP: [2],
            ANIM_IDLE_DOWN: [0],
            ANIM_MOVE_RIGHT: [5, 4],
            ANIM_MOVE_UP: [3, 2],
            ANIM_MOVE_DOWN: [1, 0],
        },
    ),
    128: (
        "multi_movement",
        False,
        {
            ANIM_IDLE_RIGHT: [4],
            ANIM_IDLE_LEFT: [6],
            ANIM_IDLE_UP: [2],
            ANIM_IDLE_DOWN: [0],
            ANIM_MOVE_RIGHT: [5, 4],
            ANIM_MOVE_LEFT: [7, 6],
            ANIM_MOVE_UP: [3, 2],
            ANIM_MOVE_DOWN: [1, 0],
        },
    ),
}

# Collision bounds, relative to the actor's anchor tile. GB Studio's own
# top-down player uses the bottom half (16x8): heads may overlap walls, feet may
# not. Static props (signposts) use the full 16x16 (GB Studio's default).
BOUNDS_PLAYER = {"boundsX": 0, "boundsY": 0, "boundsWidth": 16, "boundsHeight": 8}
BOUNDS_PROP = {"boundsX": 0, "boundsY": -8, "boundsWidth": 16, "boundsHeight": 16}


def sha1_file(path: Path) -> str:
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()


def sheet_layout(png: Path):
    with Image.open(png) as im:
        w, h = im.size
    if h != FRAME or w not in LAYOUTS:
        raise ValueError(
            f"{png}: sprite sheet must be 16 px tall and 16/48/64/96/128 px wide, got {w}x{h}"
        )
    return w, h, LAYOUTS[w]


def unique_8x16_tiles(png: Path) -> int:
    """Count unique 8x16 hardware tiles in a sheet (what VRAM actually holds)."""
    from .validate import sprite_index_array

    idx = sprite_index_array(png)
    h, w = idx.shape
    cols = {idx[:, x : x + 8].tobytes() for x in range(0, w, 8)}
    return len(cols)


def sprite_resource(png: Path, name: str, symbol: str, uid, bounds: dict) -> dict:
    """Build the .png.gbsres dict for a sprite sheet. `uid()` returns a new id."""
    w, h, (anim_type, flip_left, slots) = sheet_layout(png)
    animations = []
    for slot in range(8):
        frame_ids = slots.get(slot)
        frames = []
        if frame_ids:
            for f in frame_ids:
                frames.append(
                    {
                        "id": uid(),
                        "tiles": [
                            _tile(uid(), 0, f * FRAME),
                            _tile(uid(), 8, f * FRAME + 8),
                        ],
                    }
                )
        else:
            frames.append({"id": uid(), "tiles": []})
        animations.append({"id": uid(), "frames": frames})
    return {
        "_resourceType": "sprite",
        "id": uid(),
        "name": name,
        "symbol": symbol,
        "states": [
            {
                "id": uid(),
                "name": "",
                "animationType": anim_type,
                "flipLeft": flip_left,
                "animations": animations,
            }
        ],
        "numTiles": unique_8x16_tiles(png),
        "canvasOriginX": 0,
        "canvasOriginY": 0,
        "canvasWidth": 16,
        "canvasHeight": 16,
        **bounds,
        "animSpeed": 15,
        "filename": Path(png).name,
        "width": w,
        "height": h,
        "checksum": sha1_file(png),
    }


def _tile(tid: str, x: int, slice_x: int) -> dict:
    return {
        "id": tid,
        "x": x,
        "y": 0,
        "sliceX": slice_x,
        "sliceY": 0,
        "flipX": False,
        "flipY": False,
        "palette": 0,
        "paletteIndex": 0,
        "objPalette": "OBP0",
        "priority": False,
    }
