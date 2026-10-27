"""Constants mirrored from GB Studio 4.3.2 source (src/consts.ts and friends).

Every number here was read from the v4.3.2 tag of
https://github.com/chrismaltby/gb-studio; the file it came from is noted.
"""
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
REPO_ROOT = PKG_DIR.parents[1]
TEMPLATE_DIR = PKG_DIR / "template"

# Where the headless GB Studio build lives (see tools/gbs/setup_gbstudio.sh): $GBS_SRC if set,
# else a gbs-src/ folder next to this repo's checkout. tools/gbs/build.py reads $GBS_SRC at call time.
DEFAULT_GBS_SRC = REPO_ROOT.parent / "gbs-src"

# Project format written by GB Studio 4.3.2. The last migration in
# src/lib/project/migration/migrateProjectResources.ts is 4.2.0 r9 -> r10,
# so a 4.3.2 save carries _version "4.2.0" / _release "10".
PROJECT_VERSION = "4.2.0"
PROJECT_RELEASE = "10"

# src/consts.ts
TILE_SIZE = 8
SCREEN_WIDTH = 20  # tiles
SCREEN_HEIGHT = 18  # tiles
SCENE_MAX_SIZE_PX = 2040
MAX_PIXELS = 16380 * 64  # = 1,048,320; lib/compiler/compileImages.ts
MAX_ACTORS = 20
MAX_ACTORS_SMALL = 10  # scenes no bigger than one screen (20x18 tiles)
MAX_TRIGGERS = 30
MAX_ONSCREEN = 10
MAX_BACKGROUND_TILES = 16 * 12  # 192, DMG / mixed mode
COLLISION_ALL = 0x0F  # COLLISION_TOP|BOTTOM|LEFT|RIGHT

# The project palette (darkest last, the order GB Studio uses for
# customColorsWhite/Light/Dark/Black).
DMG_HEX = ["e0f8cf", "86c06c", "306850", "071821"]
DMG_RGB = [tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) for h in DMG_HEX]

# Engine animation slots (src/shared/lib/sprites/helpers.ts)
ANIM_IDLE_RIGHT, ANIM_IDLE_LEFT, ANIM_IDLE_UP, ANIM_IDLE_DOWN = 0, 1, 2, 3
ANIM_MOVE_RIGHT, ANIM_MOVE_LEFT, ANIM_MOVE_UP, ANIM_MOVE_DOWN = 4, 5, 6, 7

DIRECTIONS = ("up", "down", "left", "right")
