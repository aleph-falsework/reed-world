"""Headless GB Studio 4.3.2 builds via its own CLI (src/apps/gb-studio-cli).

    build_rom(project_dir, out_rom, verbose=False) -> Path
    build_web(project_dir, out_dir, verbose=False) -> Path (index.html)

The CLI is `out/cli/gb-studio-cli.js` in a GB Studio checkout built by
tools/gbs/setup_gbstudio.sh. Override its location with $GBS_SRC.

Each build gets a private TMPDIR: the CLI always builds in
`os.tmpdir()/_gbsbuild`, so two builds sharing a TMPDIR would clobber each
other. The ROM's .sym/.map/.noi files are copied next to the ROM (PyBoy
picks up `<rom>.sym` automatically; tools/gbs/play.py uses it to read state).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .constants import DEFAULT_GBS_SRC


class BuildError(RuntimeError):
    pass


def gbs_src() -> Path:
    return Path(os.environ.get("GBS_SRC", DEFAULT_GBS_SRC))


def cli_path() -> Path:
    cli = gbs_src() / "out" / "cli" / "gb-studio-cli.js"
    if not cli.exists():
        raise BuildError(f"GB Studio CLI not found at {cli}; run tools/gbs/setup_gbstudio.sh")
    return cli


def _project_file(project_dir) -> Path:
    p = Path(project_dir)
    if p.suffix == ".gbsproj":
        return p
    found = sorted(p.glob("*.gbsproj"))
    if not found:
        raise BuildError(f"no .gbsproj in {p}")
    return found[0]


def _run(command: str, project_dir, dest: Path, verbose: bool, tmp: Path):
    proj = _project_file(project_dir).resolve()
    env = dict(os.environ, TMPDIR=str(tmp), TMP=str(tmp), TEMP=str(tmp))
    cmd = ["node", str(cli_path()), command, "-v", str(proj), str(dest)]
    t0 = time.time()
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd=gbs_src())
    log = r.stdout + r.stderr
    (tmp / "build.log").write_text(log)
    if verbose:
        print(log)
    if r.returncode != 0:
        tail = "\n".join(log.strip().splitlines()[-25:])
        raise BuildError(f"gb-studio-cli {command} failed (exit {r.returncode}):\n{tail}")
    # The CLI prints compiler warnings with -v; surface the important ones.
    warnings = [l for l in log.splitlines() if "warning" in l.lower() or "Warning" in l]
    return time.time() - t0, warnings, log


def build_rom(project_dir, out_rom, verbose: bool = False) -> Path:
    """Compile the project to a .gb ROM. Also writes <rom>.sym/.map/.noi and <rom>.build.log."""
    out_rom = Path(out_rom).resolve()
    out_rom.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="gbsbuild_"))
    try:
        secs, warnings, log = _run("make:rom", project_dir, out_rom, verbose, tmp)
        if not out_rom.exists():
            raise BuildError(f"CLI exited 0 but no ROM at {out_rom}")
        romdir = tmp / "_gbsbuild" / "build" / "rom"
        for ext in (".sym", ".map", ".noi"):
            for f in romdir.glob(f"*{ext}"):
                shutil.copyfile(f, out_rom.with_suffix(ext))
        out_rom.with_suffix(".build.log").write_text(
            f"# built in {secs:.1f}s\n" + log
        )
        build_rom.last_seconds = secs  # type: ignore[attr-defined]
        build_rom.last_warnings = warnings  # type: ignore[attr-defined]
        return out_rom
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build_web(project_dir, out_dir, verbose: bool = False) -> Path:
    """Build the HTML5 export (binjgb wasm emulator + ROM). Returns index.html."""
    out_dir = Path(out_dir).resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    tmp = Path(tempfile.mkdtemp(prefix="gbsweb_"))
    try:
        secs, warnings, log = _run("make:web", project_dir, out_dir, verbose, tmp)
        index = out_dir / "index.html"
        if not index.exists():
            raise BuildError(f"CLI exited 0 but no {index}")
        build_web.last_seconds = secs  # type: ignore[attr-defined]
        return index
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
