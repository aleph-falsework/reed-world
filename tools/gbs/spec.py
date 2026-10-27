"""Helpers for reading the scene spec (docs/OVERNIGHT.md, "Scene spec").

Small, documented extensions to the contract (all optional):
  spec["sprites"]       {name: png path}. An actor's "sprite" may be a name in
                        this map, a path to a .png, or a bare name that resolves
                        to art/sprites/<name>.png.
  spec["settings"]      {"topdown_grid": 8|16, "lines_per_box": 3, "author": str}
  collisions            list of rows (height x width) of ints, or list of
                        strings like "0011". 0 walkable, 1 solid (written as
                        GB Studio 0x0F). Values >= 2 pass through as raw GB
                        Studio collision bytes.
  actor["direction"]    facing for the actor sprite, default "down".
  trigger["direction"]  may be omitted or "" to keep the player's facing.
"""
from __future__ import annotations

from pathlib import Path

from .constants import COLLISION_ALL, REPO_ROOT


def base_path(base_dir) -> Path:
    return Path(base_dir) if base_dir is not None else REPO_ROOT


def resolve_path(p, base_dir) -> Path:
    p = Path(p)
    return p if p.is_absolute() else base_path(base_dir) / p


def resolve_sprite(spec: dict, ref: str, base_dir) -> Path:
    sprites = spec.get("sprites") or {}
    if ref in sprites:
        return resolve_path(sprites[ref], base_dir)
    if str(ref).lower().endswith(".png"):
        return resolve_path(ref, base_dir)
    return resolve_path(f"art/sprites/{ref}.png", base_dir)


def collision_grid(scene: dict) -> list[list[int]]:
    """Return the spec collisions as a height x width list of ints (spec values)."""
    rows = scene.get("collisions")
    w, h = int(scene["width"]), int(scene["height"])
    if rows is None:
        return [[0] * w for _ in range(h)]
    out = []
    for r in rows:
        if isinstance(r, str):
            out.append([int(c) for c in r.strip()])
        else:
            out.append([int(v) for v in r])
    return out


def gbs_collision_bytes(scene: dict) -> list[int]:
    """Flatten to GB Studio's row-major collision byte array."""
    flat = []
    for row in collision_grid(scene):
        for v in row:
            flat.append(0 if v == 0 else COLLISION_ALL if v == 1 else v & 0xFF)
    return flat


def dialogue_boxes(lines, lines_per_box: int = 3) -> list[str]:
    """Group dialogue lines into GB Studio text boxes ("\\n"-joined pages)."""
    lines = [ascii_text(l) for l in (lines or [])]
    return [
        "\n".join(lines[i : i + lines_per_box]) for i in range(0, len(lines), lines_per_box)
    ] or [""]


_ASCII_MAP = {
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    "…": "...",
    " ": " ",
}


def ascii_text(s: str) -> str:
    """The default GB Studio font only covers ASCII; map common typography."""
    return "".join(_ASCII_MAP.get(c, c) for c in str(s))
