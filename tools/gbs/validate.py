"""Limit checks for a scene spec, mirroring GB Studio 4.3.2's own checks.

    report = validate(spec, base_dir=None)
    report["ok"]        True when there are no errors
    report["errors"]    list[str]  things GB Studio rejects or that break the game
    report["warnings"]  list[str]  things GB Studio warns about or that look wrong
    report["scenes"]    {scene_id: {"unique_tiles": int, "unique_tiles_with_flips": int,
                                    "sprite_tiles": int, "sprite_tile_budget": int, ...}}

Tile counting: GB Studio maps each pixel to a colour index by its green
channel (tileDataIndexFn in src/shared/lib/tiles/tileData.ts) and dedupes
identical 8x8 tiles. Flipped-tile dedupe (autoTileFlipEnabled) only applies
when the project's colour mode is "color" (CGB only, compileImages.ts:
`imgTileFlipEnabled = cgbOnly && ...`); DMG hardware cannot flip background
tiles. This project is Monochrome, so `unique_tiles` (exact dedupe) is the
number that must be <= 192. `unique_tiles_with_flips` is reported for info.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from .constants import (
    DIRECTIONS,
    DMG_HEX,
    DMG_RGB,
    MAX_ACTORS,
    MAX_ACTORS_SMALL,
    MAX_BACKGROUND_TILES,
    MAX_PIXELS,
    MAX_TRIGGERS,
    SCENE_MAX_SIZE_PX,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    TILE_SIZE,
)
from .spec import collision_grid, resolve_path, resolve_sprite

MAX_LINE_CHARS = 18  # 20-tile wide dialogue box minus the frame, default mono font


# ---------------------------------------------------------------- images
def load_rgba(png) -> np.ndarray:
    with Image.open(png) as im:
        return np.asarray(im.convert("RGBA"))


def palette_violations(rgba: np.ndarray, allow_transparent=False) -> list[str]:
    px = rgba.reshape(-1, 4)
    if allow_transparent:
        px = px[px[:, 3] >= 200]
    else:
        if (px[:, 3] < 255).any():
            return ["has transparent pixels"]
    cols = {tuple(c) for c in np.unique(px[:, :3], axis=0)}
    bad = sorted(cols - set(DMG_RGB))
    return ["#%02x%02x%02x" % c for c in bad[:8]] + (["..."] if len(bad) > 8 else [])


def bg_index_array(png) -> np.ndarray:
    """Colour index per pixel exactly as GB Studio computes it (green channel)."""
    g = load_rgba(png)[:, :, 1].astype(np.int32)
    idx = np.zeros(g.shape, np.uint8)
    idx[g < 205] = 1
    idx[g < 130] = 2
    idx[g < 65] = 3
    return idx


def sprite_index_array(png) -> np.ndarray:
    """spriteDataIndexFn: 0 transparent, 1 light, 2 mid, 3 dark."""
    a = load_rgba(png).astype(np.int32)
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    out = np.full(g.shape, 3, np.uint8)
    out[g >= 130] = 2
    out[g >= 205] = 1
    transparent = ((g > 249) & (r < 180) & (b < 20)) | ((b >= 200) & (g < 20)) | (al < 200)
    out[transparent] = 0
    return out


def tiles_of(idx: np.ndarray) -> np.ndarray:
    h, w = idx.shape
    t = idx[: h - h % 8, : w - w % 8].reshape(h // 8, 8, w // 8, 8).swapaxes(1, 2)
    return t.reshape(-1, 8, 8)


def count_unique_tiles(png) -> tuple[int, int]:
    """(exact unique 8x8 tiles, unique when H/V/HV flips also dedupe)."""
    tiles = tiles_of(bg_index_array(png))
    exact = np.unique(tiles.reshape(len(tiles), -1), axis=0)
    variants = np.stack(
        [exact.reshape(-1, 8, 8), exact.reshape(-1, 8, 8)[:, :, ::-1],
         exact.reshape(-1, 8, 8)[:, ::-1, :], exact.reshape(-1, 8, 8)[:, ::-1, ::-1]]
    ).reshape(4, len(exact), 64)
    # canonical form = lexicographically smallest variant
    canon = set()
    for i in range(len(exact)):
        canon.add(min(variants[k, i].tobytes() for k in range(4)))
    return len(exact), len(canon)


def sprite_tile_budget(bg_tiles: int) -> int:
    """maxSpriteTilesForBackgroundTilesLength (DMG, 8x16 sprite mode)."""
    if bg_tiles <= 128:
        return 96
    if bg_tiles < 192:
        return 96 - -(-(bg_tiles - 128) // 2)
    return 64


# ---------------------------------------------------------------- validate
def validate(spec: dict, base_dir=None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    scenes_report: dict = {}
    scenes = spec.get("scenes") or []
    by_id = {}
    for s in scenes:
        if s.get("id") in by_id:
            errors.append(f"duplicate scene id {s.get('id')!r}")
        by_id[s.get("id")] = s
    if not scenes:
        errors.append("spec has no scenes")

    sprite_cache: dict = {}

    def check_sprite(ref, where, player=False):
        path = resolve_sprite(spec, ref, base_dir)
        if path in sprite_cache:
            return sprite_cache[path]
        info = None
        if not path.exists():
            errors.append(f"{where}: sprite {ref!r} not found at {path}")
        else:
            from .sprites import LAYOUTS, unique_8x16_tiles

            with Image.open(path) as im:
                w, h = im.size
            if h != 16 or w not in LAYOUTS:
                errors.append(f"{where}: sprite {path.name} is {w}x{h}; need 16 tall and 16/48/64/96/128 wide")
            else:
                bad = palette_violations(load_rgba(path), allow_transparent=True)
                if bad:
                    errors.append(f"{where}: sprite {path.name} uses off-palette colours {bad}")
                if (sprite_index_array(path) == 3).any() and (
                    (load_rgba(path)[..., :3] == DMG_RGB[2]).all(-1) & (load_rgba(path)[..., 3] >= 200)
                ).any():
                    warnings.append(
                        f"{where}: sprite {path.name} uses #{DMG_HEX[2]}; sprites have 3 colours and "
                        f"#{DMG_HEX[2]} renders as #{DMG_HEX[3]} (OBP0 = 0,1,3)"
                    )
                if player and w < 96:
                    warnings.append(f"{where}: player sprite {path.name} has no walk frames (width {w})")
                info = {"path": path, "tiles": unique_8x16_tiles(path)}
        sprite_cache[path] = info
        return info

    player = spec.get("player") or {}
    if not player:
        errors.append("spec has no player")
    else:
        if player.get("scene") not in by_id:
            errors.append(f"player.scene {player.get('scene')!r} is not a scene id")
        if player.get("direction", "down") not in DIRECTIONS:
            errors.append(f"player.direction {player.get('direction')!r} not one of {DIRECTIONS}")
        player_sprite = check_sprite(player.get("sprite", "player"), "player", player=True)
        ps = by_id.get(player.get("scene"))
        if ps:
            _check_pos(player, ps, "player start", errors, footprint=True)

    for s in scenes:
        sid = s.get("id")
        where = f"scene {sid}"
        rep = scenes_report.setdefault(sid, {})
        try:
            w, h = int(s["width"]), int(s["height"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{where}: width/height missing")
            continue
        # background
        bg = resolve_path(s.get("background", ""), base_dir)
        if not s.get("background") or not bg.exists():
            errors.append(f"{where}: background not found at {bg}")
        else:
            with Image.open(bg) as im:
                iw, ih = im.size
            rep.update(image_width=iw, image_height=ih)
            if iw % TILE_SIZE or ih % TILE_SIZE:
                errors.append(f"{where}: background {iw}x{ih} px is not a multiple of 8")
            if iw > SCENE_MAX_SIZE_PX or ih > SCENE_MAX_SIZE_PX:
                errors.append(f"{where}: background {iw}x{ih} px exceeds {SCENE_MAX_SIZE_PX} px per side")
            if iw * ih > MAX_PIXELS:
                errors.append(f"{where}: background {iw * ih} px^2 exceeds {MAX_PIXELS}")
            if (iw, ih) != (w * TILE_SIZE, h * TILE_SIZE):
                errors.append(
                    f"{where}: background is {iw}x{ih} px but scene is {w}x{h} tiles ({w * 8}x{h * 8} px)"
                )
            if iw < SCREEN_WIDTH * 8 or ih < SCREEN_HEIGHT * 8:
                warnings.append(f"{where}: background smaller than one screen (160x144)")
            bad = palette_violations(load_rgba(bg))
            if bad:
                errors.append(f"{where}: background uses off-palette colours {bad}")
            exact, flips = count_unique_tiles(bg)
            rep.update(unique_tiles=exact, unique_tiles_with_flips=flips)
            if exact > MAX_BACKGROUND_TILES:
                errors.append(f"{where}: {exact} unique 8x8 tiles > {MAX_BACKGROUND_TILES}")
        # collisions
        grid = collision_grid(s)
        if len(grid) != h or any(len(r) != w for r in grid):
            errors.append(
                f"{where}: collisions must be {h} rows x {w} cols, got {len(grid)} rows"
                f" (row lengths {sorted({len(r) for r in grid})})"
            )
        # actors
        actors = s.get("actors") or []
        small = w <= SCREEN_WIDTH and h <= SCREEN_HEIGHT
        max_actors = MAX_ACTORS_SMALL if small else MAX_ACTORS
        if len(actors) > max_actors:
            errors.append(f"{where}: {len(actors)} actors > {max_actors}")
        seen = set()
        sprite_tiles = {}
        if player:
            pinfo = check_sprite(player.get("sprite", "player"), "player", player=True)
            if pinfo:
                sprite_tiles[pinfo["path"]] = pinfo["tiles"]
        for a in actors:
            aw = f"{where} actor {a.get('id')}"
            if a.get("id") in seen:
                errors.append(f"{aw}: duplicate actor id")
            seen.add(a.get("id"))
            _check_pos(a, s, aw, errors, footprint=True, check_solid=False)
            info = check_sprite(a.get("sprite", "signpost"), aw)
            if info:
                sprite_tiles[info["path"]] = info["tiles"]
            for line in a.get("dialogue") or []:
                if len(str(line)) > MAX_LINE_CHARS:
                    warnings.append(f"{aw}: dialogue line longer than {MAX_LINE_CHARS} chars: {line!r}")
                from .spec import ascii_text

                if any(ord(c) > 126 for c in ascii_text(line)):
                    warnings.append(f"{aw}: non-ASCII text the default font cannot draw: {line!r}")
            if a.get("direction", "down") not in DIRECTIONS:
                errors.append(f"{aw}: direction {a.get('direction')!r} not one of {DIRECTIONS}")
        rep["sprite_tiles"] = sum(sprite_tiles.values())
        if "unique_tiles" in rep:
            rep["sprite_tile_budget"] = sprite_tile_budget(rep["unique_tiles"])
            if rep["sprite_tiles"] > rep["sprite_tile_budget"]:
                warnings.append(
                    f"{where}: sprites use {rep['sprite_tiles']} 8x16 tiles > budget {rep['sprite_tile_budget']}"
                )
        # triggers
        triggers = s.get("triggers") or []
        if len(triggers) > MAX_TRIGGERS:
            errors.append(f"{where}: {len(triggers)} triggers > {MAX_TRIGGERS}")
        tseen = set()
        for t in triggers:
            tw = f"{where} trigger {t.get('id')}"
            if t.get("id") in tseen:
                errors.append(f"{tw}: duplicate trigger id")
            tseen.add(t.get("id"))
            tx, ty, tW, tH = (int(t.get(k, 0)) for k in ("x", "y", "w", "h"))
            if tW < 1 or tH < 1:
                errors.append(f"{tw}: w/h must be >= 1")
            if tx < 0 or ty < 0 or tx + tW > w or ty + tH > h:
                errors.append(f"{tw}: rect {tx},{ty} {tW}x{tH} outside the {w}x{h} scene")
            dest = by_id.get(t.get("to"))
            if dest is None:
                errors.append(f"{tw}: 'to' {t.get('to')!r} is not a scene id")
            else:
                _check_pos({"x": t.get("to_x"), "y": t.get("to_y")}, dest, f"{tw} destination", errors, footprint=True)
            if t.get("direction") not in (None, "", *DIRECTIONS):
                errors.append(f"{tw}: direction {t.get('direction')!r} not one of {DIRECTIONS}")
        rep.update(actors=len(actors), triggers=len(triggers), max_actors=max_actors)

    return {"ok": not errors, "errors": errors, "warnings": warnings, "scenes": scenes_report}


def _check_pos(obj, scene, where, errors, footprint=False, check_solid=True):
    try:
        x, y = int(obj.get("x")), int(obj.get("y"))
    except (TypeError, ValueError):
        errors.append(f"{where}: x/y missing")
        return
    w, h = int(scene["width"]), int(scene["height"])
    # A 16x16 actor at tile (x, y) covers columns x..x+1 and rows y-1..y.
    max_x = w - 2 if footprint else w - 1
    if not (0 <= x <= max_x and 0 <= y <= h - 1):
        errors.append(f"{where}: ({x},{y}) outside scene {scene.get('id')} ({w}x{h})")
        return
    grid = collision_grid(scene)
    if check_solid and footprint and len(grid) == h and all(len(r) == w for r in grid):
        if grid[y][x] == 1 or grid[y][x + 1] == 1:
            errors.append(f"{where}: ({x},{y}) stands on a solid collision tile in {scene.get('id')}")
