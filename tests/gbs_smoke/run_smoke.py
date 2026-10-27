"""End-to-end smoke test: spec -> GB Studio 4.3.2 project -> ROM -> PyBoy.

Run from the repo root:   python3 tests/gbs_smoke/run_smoke.py [--web]

Proves, with screenshots in tests/gbs_smoke/screens/ and RAM checks:
  (a) the scene renders
  (b) the player cannot walk through the collision wall
  (c) the signpost dialogue appears
  (d) walking into the exit trigger loads scene 2
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from tests.gbs_smoke import make_assets  # noqa: E402
from tools.gbs import build_rom, build_web, check_project, make_project, play, validate  # noqa: E402

OUT = HERE / "out"
SCREENS = HERE / "screens"


def make_spec(col1, col2):
    return {
        "title": "GBS Smoke",
        "sprites": {
            "player": "tests/gbs_smoke/assets/player.png",
            "signpost": "tests/gbs_smoke/assets/signpost.png",
        },
        "player": {"sprite": "player", "scene": "scene1", "x": 4, "y": 10, "direction": "right"},
        "scenes": [
            {
                "id": "scene1",
                "name": "Smoke One",
                "background": "tests/gbs_smoke/assets/scene1.png",
                "width": 20,
                "height": 18,
                "collisions": col1,
                "actors": [
                    {
                        "id": "node/1",
                        "name": "Test Sign",
                        "sprite": "signpost",
                        "x": 4,
                        "y": 6,
                        "dialogue": ["HELLO FROM SIGN", "Smoke test", "dialogue works."],
                    }
                ],
                "triggers": [
                    {"id": "exit_n_0", "x": 7, "y": 2, "w": 2, "h": 1,
                     "to": "scene2", "to_x": 11, "to_y": 16, "direction": "up"}
                ],
            },
            {
                "id": "scene2",
                "name": "Smoke Two",
                "background": "tests/gbs_smoke/assets/scene2.png",
                "width": 24,
                "height": 20,
                "collisions": col2,
                "actors": [],
                "triggers": [
                    {"id": "exit_s_0", "x": 10, "y": 18, "w": 4, "h": 1,
                     "to": "scene1", "to_x": 7, "to_y": 4, "direction": "down"}
                ],
            },
        ],
    }


INPUTS = [
    ["screenshot", "a_scene1_renders"],
    ["right", 90],  # 6 tiles of open ground, then the wall at column 12
    ["screenshot", "b_wall_blocks_player"],
    ["left", 48],  # back to x=4
    ["up", 40],  # stops under the signpost at (4,6)
    ["a", 2],
    ["wait", 90],  # text box slides in and types out
    ["screenshot", "c_signpost_dialogue"],
    ["a", 2],  # dismiss (one box; a second A would re-open the sign)
    ["wait", 40],
    ["screenshot", "c2_dialogue_closed"],
    ["right", 24],  # to x=7, under the door
    ["up", 48],  # into the trigger at y=2
    ["wait", 90],  # fade out / load / fade in
    ["screenshot", "d_scene2_loaded"],
]


def contact_sheet(paths, out, scale=3):
    from PIL import Image, ImageDraw

    ims = [Image.open(p).convert("RGB") for p in paths]
    pad, label = 6, 14
    W = len(ims) * (160 * scale + pad) + pad
    H = 144 * scale + pad * 2 + label
    sheet = Image.new("RGB", (W, H), (0x07, 0x18, 0x21))
    d = ImageDraw.Draw(sheet)
    for i, (im, p) in enumerate(zip(ims, paths)):
        x = pad + i * (160 * scale + pad)
        sheet.paste(im.resize((160 * scale, 144 * scale), 0), (x, pad + label))
        d.text((x, 3), Path(p).stem, fill=(0xE0, 0xF8, 0xCF))
    sheet.save(out)


def main(web=False):
    col1, col2 = make_assets.main()
    spec = make_spec(col1, col2)
    (HERE / "spec.json").write_text(json.dumps(spec, indent=1))

    report = validate(spec, REPO)
    print("validate:", json.dumps({k: report[k] for k in ("ok", "errors", "warnings")}, indent=1))
    print("tiles:", {k: v.get("unique_tiles") for k, v in report["scenes"].items()})
    assert report["ok"], report["errors"]

    proj = make_project(spec, OUT / "project", REPO, seed="smoke")
    print("project:", proj)
    chk = check_project(proj.parent)
    print("gb studio loader check:", "ok" if chk["ok"] else chk["problems"],
          "| migrated:", chk["isMigrated"], "| sprites needing re-detect:", chk["modifiedSpriteIds"])
    assert chk["ok"], chk

    t0 = time.time()
    rom = build_rom(proj.parent, OUT / "smoke.gb")
    build_secs = time.time() - t0
    print(f"rom: {rom} ({rom.stat().st_size} bytes) built in {build_secs:.1f}s")

    for old in SCREENS.glob("*.png"):
        old.unlink()
    shots = play(rom, INPUTS, SCREENS)
    log = {e["name"]: e for e in json.loads((SCREENS / "play_log.json").read_text())}
    for name, e in log.items():
        print(f"  {name:24s} scene={e['scene']} player={e['player']}")

    p = lambda n: log[n]["player"]  # noqa: E731
    checks = {
        "a: starts in scene1 at (4,10)": log["a_scene1_renders"]["scene"] == "scene_scene1"
        and (p("a_scene1_renders")["x"], p("a_scene1_renders")["y"]) == (4, 10),
        "b: wall at column 12 stops player at x=10": p("b_wall_blocks_player")["x"] == 10,
        "c: player stopped below signpost at y=7": p("c_signpost_dialogue")["y"] == 7,
        "d: scene2 loaded at (11,16)": log["d_scene2_loaded"]["scene"] == "scene_scene2"
        and (p("d_scene2_loaded")["x"], p("d_scene2_loaded")["y"]) == (11, 16),
    }
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    contact_sheet(shots, SCREENS / "contact_sheet.png")

    result = {"build_seconds": round(build_secs, 1), "checks": checks, "screens": shots}
    if web:
        t0 = time.time()
        index = build_web(proj.parent, OUT / "web")
        result["web_index"] = str(index)
        result["web_seconds"] = round(time.time() - t0, 1)
        print("web:", index)
    (HERE / "smoke_result.json").write_text(json.dumps(result, indent=1))
    return all(checks.values())


if __name__ == "__main__":
    sys.exit(0 if main(web="--web" in sys.argv) else 1)
