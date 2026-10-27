"""Command line:  python3 -m tools.gbs <command> ...   (run from the repo root)

  validate SPEC.json                         print the limits report, exit 1 on errors
  make     SPEC.json OUT_DIR [--seed S]      write the GB Studio project
  rom      PROJECT_DIR OUT.gb                build a ROM (+ .sym/.map next to it)
  web      PROJECT_DIR OUT_DIR               build the HTML5 export
  check    PROJECT_DIR                       strict check with GB Studio's own loader
  play     ROM INPUTS.json OUT_DIR           run scripted input, save screenshots
  all      SPEC.json OUT_DIR                 make + check + rom (+ web with --web)
"""
import json
import sys
import time
from pathlib import Path

from . import build_rom, build_web, check_project, make_project, play, validate


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "validate":
        rep = validate(json.loads(Path(args[0]).read_text()))
        print(json.dumps(rep, indent=1))
        return 0 if rep["ok"] else 1
    if cmd == "make":
        seed = args[args.index("--seed") + 1] if "--seed" in args else None
        print(make_project(json.loads(Path(args[0]).read_text()), args[1], seed=seed))
        return 0
    if cmd == "rom":
        t = time.time()
        print(build_rom(args[0], args[1]), f"{time.time() - t:.1f}s")
        return 0
    if cmd == "web":
        print(build_web(args[0], args[1]))
        return 0
    if cmd == "check":
        rep = check_project(args[0])
        print(json.dumps(rep, indent=1))
        return 0 if rep["ok"] else 1
    if cmd == "play":
        for p in play(args[0], json.loads(Path(args[1]).read_text()), args[2]):
            print(p)
        return 0
    if cmd == "all":
        spec = json.loads(Path(args[0]).read_text())
        out = Path(args[1])
        proj = make_project(spec, out / "project")
        rep = check_project(proj.parent)
        if not rep["ok"]:
            print(json.dumps(rep["problems"], indent=1))
            return 1
        t = time.time()
        rom = build_rom(proj.parent, out / "game.gb")
        print(rom, f"{time.time() - t:.1f}s")
        if "--web" in args:
            print(build_web(proj.parent, out / "web"))
        return 0
    print(__doc__)
    return 2


sys.exit(main(sys.argv[1:]))
