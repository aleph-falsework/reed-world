"""Write a GB Studio 4.3.2 project from a scene spec.

    make_project(spec, out_dir, base_dir=None, seed=None, strict=True) -> Path (the .gbsproj)

Layout written (GB Studio 4.3.2 "split" format):

    <out_dir>/<slug>.gbsproj                 project metadata (_version 4.2.0, _release 10)
    <out_dir>/assets/backgrounds/<id>.png    + .png.gbsres (fixed id/symbol)
    <out_dir>/assets/sprites/<name>.png      + .png.gbsres (states/frames written out)
    <out_dir>/assets/fonts, assets/ui        GB Studio's default font + dialogue frame
    <out_dir>/project/settings.gbsres        start scene/pos/dir, player sprite, mono mode, DMG colours
    <out_dir>/project/scenes/<folder>/scene.gbsres
    <out_dir>/project/scenes/<folder>/actors/<file>.gbsres
    <out_dir>/project/scenes/<folder>/triggers/<file>.gbsres
    <out_dir>/project/palettes/*.gbsres, variables.gbsres, engine_field_values.gbsres
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from .constants import (
    DMG_HEX,
    PROJECT_RELEASE,
    PROJECT_VERSION,
    TEMPLATE_DIR,
)
from .spec import dialogue_boxes, gbs_collision_bytes, resolve_path, resolve_sprite
from .sprites import BOUNDS_PLAYER, BOUNDS_PROP, sprite_resource
from .validate import validate


class SpecError(ValueError):
    pass


# ---------------------------------------------------------------- helpers
def compress_8bit(arr: list[int]) -> str:
    """Port of compress8bitNumberArray (src/shared/lib/resources/compression.ts)."""
    out, last, count = [], -1, 0

    def flush():
        if count == 1:
            out.append("!")
        elif count > 0:
            out.append(f"{count:x}+")

    for v in arr:
        if v != last:
            flush()
            count = 0
            last = v
            out.append(f"{v % 256:02x}")
        count += 1
    flush()
    return "".join(out)


def decompress_8bit(s: str) -> list[int]:
    arr, i = [], 0
    while i < len(s):
        v = int(s[i : i + 2], 16)
        i += 2
        if s[i] == "!":
            n = 1
            i += 1
        else:
            j = s.index("+", i)
            n = int(s[i:j], 16)
            i = j + 1
        arr += [v] * n
    return arr


def to_symbol(s: str) -> str:
    """toValidSymbol (src/shared/lib/helpers/symbols.ts)."""
    sym = re.sub(r"[^a-z0-9_]", "_", str(s or "symbol").lower())
    sym = re.sub(r"_+", "_", sym)[:27]
    return f"_{sym}" if re.match(r"^\d", sym) else sym


class _Symbols:
    def __init__(self):
        self.used: set[str] = set()

    def __call__(self, s: str) -> str:
        base = to_symbol(s)
        sym, n = base, 0
        while sym in self.used:
            sym = f"{re.sub(r'_[0-9]+', '', base)}_{n}"
            n += 1
        self.used.add(sym)
        return sym


def to_path_part(name: str, fallback: str) -> str:
    """entityToFilePath (src/shared/lib/resources/paths.ts), minus folder support."""
    p = re.sub(r"[:?*|\"><\[\]#%&{}\b\0$!'@‘`“+^./\\]", "", name or "")
    p = re.sub(r"\s+", "_", p.lower())
    return p or fallback


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _num(v: int) -> dict:
    return {"type": "number", "value": int(v)}


# ---------------------------------------------------------------- main
def make_project(spec: dict, out_dir, base_dir=None, seed=None, strict: bool = True) -> Path:
    """Write a complete GB Studio 4.3.2 project for `spec` into `out_dir`.

    base_dir: where relative asset paths in the spec are resolved (default: repo root).
    seed:     if given, ids are deterministic uuid5s (stable diffs); else fresh uuid4s.
    strict:   raise SpecError if validate(spec) reports errors.
    Returns the path to the .gbsproj file.
    """
    report = validate(spec, base_dir)
    if strict and not report["ok"]:
        raise SpecError("spec failed validation:\n  " + "\n  ".join(report["errors"]))

    out = Path(out_dir)
    if out.exists():
        # Only wipe things we own, so a user's build/ or notes survive.
        for sub in ("project", "assets"):
            shutil.rmtree(out / sub, ignore_errors=True)
        for f in out.glob("*.gbsproj"):
            f.unlink()
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TEMPLATE_DIR / "assets", out / "assets")
    shutil.copytree(TEMPLATE_DIR / "project", out / "project")
    if not (out / ".gitignore").exists():
        shutil.copy(TEMPLATE_DIR / "gitignore", out / ".gitignore")
    for d in ("backgrounds", "sprites"):
        (out / "assets" / d).mkdir(parents=True, exist_ok=True)

    counter = [0]
    ns = uuid.uuid5(uuid.NAMESPACE_URL, f"reed-world/{seed}") if seed is not None else None

    def uid() -> str:
        if ns is None:
            return str(uuid.uuid4())
        counter[0] += 1
        return str(uuid.uuid5(ns, str(counter[0])))

    sym = _Symbols()
    settings_extra = spec.get("settings") or {}
    lines_per_box = int(settings_extra.get("lines_per_box", 3))

    # ---- sprites
    sprite_ids: dict[Path, str] = {}

    def add_sprite(ref: str, bounds: dict) -> str:
        src = resolve_sprite(spec, ref, base_dir)
        if src in sprite_ids:
            return sprite_ids[src]
        name = to_path_part(src.stem, "sprite")
        dst = out / "assets" / "sprites" / f"{name}.png"
        i = 1
        while dst.exists():
            dst = out / "assets" / "sprites" / f"{name}_{i}.png"
            i += 1
        shutil.copyfile(src, dst)
        res = sprite_resource(dst, dst.stem, sym(f"sprite_{dst.stem}"), uid, bounds)
        _write_json(dst.with_name(dst.name + ".gbsres"), res)
        sprite_ids[src] = res["id"]
        return res["id"]

    player = spec["player"]
    player_sprite_id = add_sprite(player.get("sprite", "player"), BOUNDS_PLAYER)

    # ---- scenes: ids first so triggers can reference any scene
    scene_ids = {s["id"]: uid() for s in spec["scenes"]}
    folders: set[str] = set()
    world_x, world_y, row_h = 0, 0, 0

    for index, s in enumerate(spec["scenes"]):
        w, h = int(s["width"]), int(s["height"])
        # background
        bg_src = resolve_path(s["background"], base_dir)
        bg_name = to_path_part(s["id"], "background")
        bg_dst = out / "assets" / "backgrounds" / f"{bg_name}.png"
        shutil.copyfile(bg_src, bg_dst)
        bg = {
            "_resourceType": "background",
            "id": uid(),
            "name": bg_name,
            "symbol": sym(f"bg_{bg_name}"),
            "tileColors": "",
            "filename": bg_dst.name,
            "width": w,
            "height": h,
            "imageWidth": w * 8,
            "imageHeight": h * 8,
            "autoColor": False,
        }
        _write_json(bg_dst.with_name(bg_dst.name + ".gbsres"), bg)

        # world-editor position: lay scenes out left to right, wrapping
        if world_x > 0 and world_x + w * 8 > 4000:
            world_x, world_y, row_h = 0, world_y + row_h + 80, 0
        sx, sy = world_x + 60, world_y + 30  # MIN_WORLD_ENTITY_X/Y
        world_x += w * 8 + 80
        row_h = max(row_h, h * 8)

        folder = to_path_part(s.get("name") or s["id"], "scene")
        base_folder, n = folder, 1
        while folder in folders:
            folder = f"{base_folder}_{n}"
            n += 1
        folders.add(folder)
        scene_dir = out / "project" / "scenes" / folder

        scene = {
            "_resourceType": "scene",
            "_index": index,
            "id": scene_ids[s["id"]],
            "type": "TOPDOWN",
            "name": s.get("name") or s["id"],
            "symbol": sym(f"scene_{s['id']}"),
            "notes": f"spec id: {s['id']}",
            "x": sx,
            "y": sy,
            "width": w,
            "height": h,
            "backgroundId": bg["id"],
            "tilesetId": "",
            "colorModeOverride": "none",
            "paletteIds": [],
            "spritePaletteIds": [],
            "autoFadeSpeed": 1,
            "script": [],
            "playerHit1Script": [],
            "playerHit2Script": [],
            "playerHit3Script": [],
            "collisions": compress_8bit(gbs_collision_bytes(s)),
        }
        _write_json(scene_dir / "scene.gbsres", scene)

        files: set[str] = set()

        def unique_file(name: str, fallback: str) -> str:
            f = to_path_part(name, fallback)
            b, k = f, 1
            while f in files:
                f = f"{b}_{k}"
                k += 1
            files.add(f)
            return f

        for ai, a in enumerate(s.get("actors") or []):
            sprite_id = add_sprite(a.get("sprite", "signpost"), BOUNDS_PROP)
            boxes = dialogue_boxes(a.get("dialogue"), lines_per_box)
            script = []
            if a.get("dialogue"):
                script.append(
                    {
                        "command": "EVENT_TEXT",
                        "args": {"text": boxes, "avatarId": "", "minHeight": 4, "maxHeight": 7},
                        "id": uid(),
                    }
                )
            label = a.get("name") or a["id"]
            actor = {
                "_resourceType": "actor",
                "_index": ai,
                "id": uid(),
                "symbol": sym(f"actor_{a['id']}"),
                "prefabId": "",
                "name": label,
                "notes": f"id: {a['id']}" + (" (estimated)" if a.get("estimated") else ""),
                "coordinateType": "tiles",
                "x": int(a["x"]),
                "y": int(a["y"]),
                "frame": 0,
                "animate": False,
                "spriteSheetId": sprite_id,
                "paletteId": "",
                "direction": a.get("direction", "down"),
                "moveSpeed": 1,
                "animSpeed": 15,
                "isPinned": False,
                "persistent": False,
                "collisionGroup": "",
                "collisionExtraFlags": [],
                "prefabScriptOverrides": {},
                "script": script,
                "startScript": [],
                "updateScript": [],
                "hit1Script": [],
                "hit2Script": [],
                "hit3Script": [],
            }
            _write_json(scene_dir / "actors" / f"{unique_file(label + '_' + a['id'], 'actor')}.gbsres", actor)

        for ti, t in enumerate(s.get("triggers") or []):
            trig = {
                "_resourceType": "trigger",
                "_index": ti,
                "id": uid(),
                "symbol": sym(f"trigger_{s['id']}_{t['id']}"),
                "prefabId": "",
                "name": t.get("name") or t["id"],
                "notes": f"id: {t['id']} -> {t['to']}",
                "x": int(t["x"]),
                "y": int(t["y"]),
                "width": int(t["w"]),
                "height": int(t["h"]),
                "prefabScriptOverrides": {},
                "script": [
                    {
                        "command": "EVENT_SWITCH_SCENE",
                        "args": {
                            "sceneId": scene_ids[t["to"]],
                            "x": _num(t["to_x"]),
                            "y": _num(t["to_y"]),
                            "direction": t.get("direction") or "",
                            "fadeSpeed": "2",
                        },
                        "id": uid(),
                    }
                ],
                "leaveScript": [],
            }
            _write_json(scene_dir / "triggers" / f"{unique_file(t['id'], 'trigger')}.gbsres", trig)

    # ---- settings
    settings_path = out / "project" / "settings.gbsres"
    settings = json.loads(settings_path.read_text())
    settings.update(
        startSceneId=scene_ids[player["scene"]],
        startX=int(player["x"]),
        startY=int(player["y"]),
        startDirection=player.get("direction", "down"),
        startMoveSpeed=1,
        startAnimSpeed=15,
        colorMode="mono",
        sgbEnabled=False,
        spriteMode="8x16",
        defaultSceneTypeId="TOPDOWN",
        customColorsWhite=DMG_HEX[0].upper(),
        customColorsLight=DMG_HEX[1].upper(),
        customColorsDark=DMG_HEX[2].upper(),
        customColorsBlack=DMG_HEX[3].upper(),
        defaultMonoBGP=[0, 1, 2, 3],
        defaultMonoOBP0=[0, 1, 3],
        defaultMonoOBP1=[0, 2, 3],
        openBuildFolderOnExport=False,
    )
    settings["defaultPlayerSprites"] = {k: player_sprite_id for k in settings["defaultPlayerSprites"]}
    _write_json(settings_path, settings)

    # ---- engine fields
    grid = int(settings_extra.get("topdown_grid", 8))
    efv_path = out / "project" / "engine_field_values.gbsres"
    efv = json.loads(efv_path.read_text())
    efv["engineFieldValues"] = [f for f in efv["engineFieldValues"] if f["id"] != "topdown_grid"]
    if grid != 8:
        efv["engineFieldValues"].append({"id": "topdown_grid", "value": grid})
    _write_json(efv_path, efv)

    # ---- project file
    title = spec.get("title") or "Untitled"
    proj = out / f"{to_path_part(title, 'project')}.gbsproj"
    _write_json(
        proj,
        {
            "_resourceType": "project",
            "name": title,
            "author": settings_extra.get("author", ""),
            "notes": "Generated by tools/gbs (reed-world). Edit freely in GB Studio 4.3.2.",
            "_version": PROJECT_VERSION,
            "_release": PROJECT_RELEASE,
        },
    )
    _write_json(out / "validation.json", {k: v for k, v in report.items()})
    return proj
