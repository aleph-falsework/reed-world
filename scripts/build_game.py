#!/usr/bin/env python3
"""One command for the whole Reed World chain.

    python3 scripts/build_game.py            # art -> export -> validate -> project -> check -> ROM -> web
    python3 scripts/build_game.py --no-web   # skip the HTML5 export
    python3 scripts/build_game.py --skip-art --skip-export   # reuse art/ and world/gb/
    python3 scripts/build_game.py --playtest # also run scripts/playtest.py at the end

Outputs:
    game/reed-world/      the GB Studio 4.3.2 project (open Reed World.gbsproj in the app)
    build/reed_world.gb   the ROM (+ .sym/.map/.noi/.build.log)
    build/web/            the HTML5 export (index.html)
    build/build_log.json  timings and the validate summary
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.gbs import build_rom, build_web, check_project, make_project, validate  # noqa: E402

SPEC = REPO / "world/gb/project_spec.json"
PROJECT = REPO / "game/reed-world"
BUILD = REPO / "build"
ROM = BUILD / "reed_world.gb"
WEB = BUILD / "web"
SEED = "reed-world"  # deterministic ids, so project diffs stay small between builds


def step(name, log):
    print(f"== {name}", flush=True)
    log.append({"step": name, "t0": time.time()})


def done(log, **extra):
    log[-1]["seconds"] = round(time.time() - log[-1].pop("t0"), 1)
    log[-1].update(extra)


def run_py(script, log):
    r = subprocess.run([sys.executable, str(REPO / script)], cwd=REPO, capture_output=True, text=True)
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])
        raise SystemExit(f"{script} failed ({r.returncode})")
    tail = r.stdout.strip().splitlines()[-3:]
    for line in tail:
        print("   ", line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-art", action="store_true")
    ap.add_argument("--skip-export", action="store_true")
    ap.add_argument("--no-web", action="store_true")
    ap.add_argument("--playtest", action="store_true")
    a = ap.parse_args()
    BUILD.mkdir(exist_ok=True)
    log: list = []

    if not a.skip_art:
        step("art: art/make_art.py", log)
        run_py("art/make_art.py", log)
        done(log)
    if not a.skip_export:
        step("export: scripts/gb_export.py", log)
        run_py("scripts/gb_export.py", log)
        done(log)

    spec = json.loads(SPEC.read_text())

    step("validate", log)
    rep = validate(spec, REPO)
    summary = {
        sid: {k: s.get(k) for k in ("unique_tiles", "actors", "triggers", "sprite_tile_budget")}
        for sid, s in rep["scenes"].items()
    }
    for sid, s in summary.items():
        print(f"    {sid:16s} tiles {s['unique_tiles']:>3}  actors {s['actors']:>2}  triggers {s['triggers']:>2}")
    for w in rep["warnings"]:
        print("    warning:", w)
    if not rep["ok"]:
        for e in rep["errors"]:
            print("    ERROR:", e)
        raise SystemExit("validate failed")
    over = [sid for sid, s in summary.items() if (s["unique_tiles"] or 0) > 180]
    done(log, ok=True, warnings=rep["warnings"], scenes=summary, over_180=over)

    step("make_project -> game/reed-world", log)
    if PROJECT.exists():
        shutil.rmtree(PROJECT)  # stale scenes/actors from older exports would otherwise linger
    proj = make_project(spec, PROJECT, REPO, seed=SEED)
    print("   ", proj.relative_to(REPO))
    done(log, project=str(proj.relative_to(REPO)))

    step("check_project", log)
    chk = check_project(PROJECT)
    if not chk["ok"]:
        print(json.dumps(chk, indent=1)[:4000])
        raise SystemExit("check_project failed")
    done(log, ok=True, **{k: chk[k] for k in ("isMigrated",) if k in chk})

    step("build_rom -> build/reed_world.gb", log)
    rom = build_rom(PROJECT, ROM)
    done(log, rom=str(Path(rom).relative_to(REPO)), bytes=Path(rom).stat().st_size)
    print(f"    {log[-1]['bytes']} bytes in {log[-1]['seconds']} s")

    if not a.no_web:
        step("build_web -> build/web", log)
        if WEB.exists():
            shutil.rmtree(WEB)
        idx = build_web(PROJECT, WEB)
        done(log, index=str(Path(idx).relative_to(REPO)))

    (BUILD / "build_log.json").write_text(json.dumps(log, indent=1))

    if a.playtest:
        step("playtest", log)
        r = subprocess.run([sys.executable, str(REPO / "scripts/playtest.py")], cwd=REPO)
        done(log, returncode=r.returncode)
        (BUILD / "build_log.json").write_text(json.dumps(log, indent=1))
        return r.returncode
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
