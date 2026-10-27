"""Drive a ROM headless in PyBoy with scripted input.

    play(rom, inputs, out_dir, boot_frames=60, scale=1) -> list[str]   screenshot paths

`inputs` is a list of steps:
    ["wait", N]                 advance N frames with no buttons held
    ["right", N] / ["left", N] / ["up", N] / ["down", N]
    ["a", N] / ["b", N] / ["start", N] / ["select", N]
                                hold the button for N frames, then release it
                                (one extra released frame is ticked so the
                                next press registers as a new press)
    ["screenshot", "name"]      save <out_dir>/<name>.png (160x144, x`scale`)

A JSON log of every screenshot (frame number and, when <rom>.sym exists,
the player's tile position, facing and current scene symbol) is written
to <out_dir>/play_log.json. `GBRunner` is the same machinery for tests
that want to assert on state between steps.
"""
from __future__ import annotations

import json
from pathlib import Path

BUTTONS = {"up", "down", "left", "right", "a", "b", "start", "select"}
SUBPIXEL_BITS = 5  # GB Studio 4.x: actor pos is in 1/32 px (NUM_SUBPIXEL_BITS, src/consts.ts)
DIRS = {0: "down", 1: "right", 2: "up", 3: "left"}  # direction_e in gbvm include/gbs_types.h
DMG_PALETTE = (0xE0F8CF, 0x86C06C, 0x306850, 0x071821)


class GBRunner:
    def __init__(self, rom, sym=None):
        from pyboy import PyBoy

        rom = Path(rom)
        sym = Path(sym) if sym else rom.with_suffix(".sym")
        self.sym = _read_sym(sym) if sym.exists() else {}
        # Render with the project's DMG palette so screenshots match GB Studio.
        self.pb = PyBoy(
            str(rom), window="null", sound_emulated=False, color_palette=DMG_PALETTE
        )
        self.pb.set_emulation_speed(0)
        self.frame = 0

    # -- control
    def tick(self, n=1):
        for _ in range(int(n)):
            self.pb.tick(1, True)
        self.frame += int(n)

    def hold(self, button, frames):
        self.pb.button_press(button)
        self.tick(frames)
        self.pb.button_release(button)
        self.tick(1)

    def boot(self, max_frames=900, settle=40):
        """Run until the engine has loaded its first scene, then `settle` frames
        for the fade-in. This skips PyBoy's boot-ROM logo. A GB Studio
        logo/title scene would NOT be skipped (make_project never adds one,
        and the start scene is the spec's player scene). Without a .sym file
        it just runs max_frames // 4 frames. Returns the frame count."""
        if not self.sym:
            self.tick(max_frames // 4)
            return self.frame
        while self.frame < max_frames:
            self.tick(5)
            sc = self.scene()
            if sc and sc.startswith("scene_"):
                p = self.player()
                if p and p["flags"]:
                    break
        self.tick(settle)
        return self.frame

    def screenshot(self, path, scale=1):
        img = self.pb.screen.image.copy().convert("RGB")
        if scale != 1:
            img = img.resize((160 * scale, 144 * scale), 0)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
        return str(path)

    def stop(self):
        self.pb.stop(save=False)

    # -- state (needs the .sym file written by build_rom)
    def _addr(self, name):
        v = self.sym.get(name)
        return None if v is None else v[1]

    def player(self):
        """{'x','y'} in 8px tiles (top-left of the 16x16 sprite's bottom row), px, dir."""
        a = self._addr("_actors")
        if a is None:
            return None
        m = self.pb.memory
        flags = m[a]
        px = (m[a + 1] | m[a + 2] << 8) >> SUBPIXEL_BITS
        py = (m[a + 3] | m[a + 4] << 8) >> SUBPIXEL_BITS
        d = m[a + 5]
        return {"px": px, "py": py, "x": px // 8, "y": py // 8, "dir": DIRS.get(d, d), "flags": flags}

    def scene(self):
        a = self._addr("_current_scene")
        if a is None:
            return None
        m = self.pb.memory
        bank, ptr = m[a], m[a + 1] | m[a + 2] << 8
        for name, (b, addr) in self.sym.items():
            if addr == ptr and b == bank and name.startswith("_scene_"):
                return name[1:]
        return f"bank{bank}:{ptr:04x}"

    def state(self):
        return {"frame": self.frame, "player": self.player(), "scene": self.scene()}


def _read_sym(path):
    out = {}
    for line in Path(path).read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and ":" in parts[0] and not line.startswith(";"):
            bank, addr = parts[0].split(":")
            try:
                out[parts[1]] = (int(bank, 16), int(addr, 16))
            except ValueError:
                pass
    return out


def run_steps(runner: GBRunner, inputs, out_dir, scale=1, log=None):
    shots = []
    for step in inputs:
        op = str(step[0]).lower()
        arg = step[1] if len(step) > 1 else 1
        if op == "wait":
            runner.tick(arg)
        elif op in BUTTONS:
            runner.hold(op, int(arg))
        elif op == "screenshot":
            path = runner.screenshot(Path(out_dir) / f"{arg}.png", scale)
            shots.append(path)
            if log is not None:
                log.append({"name": arg, "path": path, **runner.state()})
        else:
            raise ValueError(f"unknown input step {step!r}")
    return shots


def play(rom, inputs, out_dir, boot_frames: int | None = None, scale: int = 1) -> list[str]:
    """Boot `rom`, run `inputs`, return screenshot paths.

    boot_frames=None (default): run until the engine reports the first scene
    loaded (via <rom>.sym), plus 40 frames of fade-in. PyBoy's own boot ROM
    logo takes ~150 frames before the game starts. Pass an int to use a fixed
    count instead. make_project adds no logo/title scene, so after boot the
    player is live; for projects with a title screen add ["start", 2] steps.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    runner = GBRunner(rom)
    log: list = []
    try:
        if boot_frames is None:
            runner.boot()
        else:
            runner.tick(boot_frames)
        shots = run_steps(runner, inputs, out_dir, scale, log)
    finally:
        runner.stop()
    (out_dir / "play_log.json").write_text(json.dumps(log, indent=2))
    return shots
