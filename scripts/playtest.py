#!/usr/bin/env python3
"""Scripted PyBoy walkthrough of build/reed_world.gb, asserted from RAM.

    python3 scripts/playtest.py              # needs build/reed_world.gb (+ .sym) from scripts/build_game.py

What it does
  * Static checks on world/gb/project_spec.json (the spec the ROM was built from): limits, dialogue
    width, spawn and every exit landing clear of collision, every scene / signpost / boundary
    reachable from the spawn.
  * Plans with BFS over a model of the GB Studio 4.3.2 top-down engine (8 px grid; the player's
    16x8 box is row y, columns x..x+1; collision byte != 0 blocks; signposts are solid 16x16 boxes;
    a trigger fires when the player's box overlaps it). Nodes are (scene, x, y); triggers are
    edges to their landing tile. Nothing is hand-tuned.
  * Greedy tour: repeatedly go to the nearest unfinished goal (read one signpost in every scene
    that has one; cross every scene boundary at least once). Each tile step is driven closed-loop:
    hold the direction until RAM shows the player on the target tile, else record the step as
    stuck, block that edge in the model and re-plan from where RAM says the player is.
  * Asserts after every warp that RAM shows the destination scene and landing tile (exits land
    proportionally: clamp(entry + offset, min, max), see `land` in scripts/gb_export.py), and after
    every A press that a text box is up (window layer raised, WY < 144) and later dismissed.
  * Wall tests: in every scene, for each solid metatile class present (ROOF, FACADE, TREE, FOREST,
    LONETREE, WATER, STEEP), walks to a reachable tile beside it, pushes into it 3 times and asserts in
    RAM that the player did not move and faces the wall, then steps the other way as a positive
    control. A ROM without collisions fails these.
  * "info" rows (reachability %, unreachable exits, landing shift, untestable wall classes) are
    reported separately and are not counted as passes or failures.
  * Screenshots: first arrival in each scene, every signpost read, every wall push;
    build/playtest/contact_sheet.png (+ _signs, _walls);
    build/playtest/RESULTS.md with pass/fail per check; build/playtest/results.json.
"""
from __future__ import annotations

import json
import sys
import time
from collections import deque
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.gbs.play import DIRS, DMG_PALETTE, SUBPIXEL_BITS, _read_sym  # noqa: E402

SPEC = REPO / "world/gb/project_spec.json"
ROM = REPO / "build/reed_world.gb"
OUT = REPO / "build/playtest"
MAX_LINE = 18
LINES_PER_BOX = 3
DXY = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


# ============================================================ model
class Scene:
    def __init__(self, s):
        self.id, self.name, self.w, self.h = s["id"], s["name"], s["width"], s["height"]
        rows = s["collisions"]
        self.col = [[int(c) for c in r] if isinstance(r, str) else list(r) for r in rows]
        self.actors = s["actors"]
        self.triggers = s["triggers"]
        self.blocked = set()  # (x, y, dir) found stuck in play
        meta = REPO / "world/gb" / self.id / "meta.json"
        self.mclass = json.loads(meta.read_text())["metatile_classes"] if meta.exists() else []

    def cls_at(self, x, y):
        my, mx = y // 2, x // 2
        return self.mclass[my][mx] if 0 <= my < len(self.mclass) and 0 <= mx < len(self.mclass[my]) else None

    def solid_at(self, x, y):
        return 0 <= x < self.w and 0 <= y < self.h and bool(self.col[y][x])

    def wall_spots(self, cls):
        """(x, y, dir): standing at (x, y), a step `dir` pushes the player's box into solid tiles that are all
        of metatile class `cls` (no signpost, not off-scene), and the opposite step is free (positive control)."""
        out = []
        for y in range(self.h):
            for x in range(self.w - 1):
                if not self.box_free(x, y) or self.trigger_at(x, y):
                    continue
                for d, (dx, dy) in DXY.items():
                    nx, ny = x + dx, y + dy
                    if not (0 <= nx <= self.w - 2 and 0 <= ny < self.h) or self.actor_at(nx, ny):
                        continue
                    hit = [(cx, ny) for cx in (nx, nx + 1) if self.solid_at(cx, ny)]
                    if not hit or any(self.cls_at(cx, cy) != cls for cx, cy in hit):
                        continue
                    bx, by = x - dx, y - dy
                    if self.box_free(bx, by) and not self.trigger_at(bx, by):
                        out.append((x, y, d))
        return out

    def box_free(self, x, y):
        if not (0 <= x <= self.w - 2 and 0 <= y < self.h):
            return False
        if self.col[y][x] or self.col[y][x + 1]:
            return False
        return not self.actor_at(x, y)

    def actor_at(self, x, y):
        for a in self.actors:  # sign occupies columns ax..ax+1, rows ay-1..ay
            if abs(x - a["x"]) <= 1 and a["y"] - 1 <= y <= a["y"]:
                return a
        return None

    def trigger_at(self, x, y):
        for t in self.triggers:
            if t["y"] <= y < t["y"] + t["h"] and t["x"] <= x + 1 and x < t["x"] + t["w"]:
                return t
        return None

    def landings(self, t):
        """Every (x, y) trigger t can land the player on (see land in scripts/gb_export.py)."""
        L = t.get("land")
        if not L:
            return [(t["to_x"], t["to_y"])]
        rng = range(L["min"], L["max"] + 1)
        return [(v, t["to_y"]) for v in rng] if L["axis"] == "x" else [(t["to_x"], v) for v in rng]

    def read_spots(self, a):
        """(x, y, facing) tiles from which A reads signpost `a` (player box shifted 8 px overlaps it)."""
        sx, sy = a["x"], a["y"]
        out = []
        for dx in (0, -1, 1):
            out.append((sx + dx, sy + 1, "up"))
        for dx in (0, -1, 1):
            out.append((sx + dx, sy - 2, "down"))
        for yy in (sy, sy - 1):
            out.append((sx - 2, yy, "right"))
            out.append((sx + 2, yy, "left"))
        return [(x, y, d) for x, y, d in out if self.box_free(x, y) and not self.trigger_at(x, y)]


def landing(t, x, y):
    """Where trigger t drops a player whose box arrived at (x, y): GB Studio evaluates
    clamp(player pos + offset, min, max) on the land axis (tools/gbs/project.py _landing)."""
    L = t.get("land")
    if not L:
        return t["to_x"], t["to_y"]
    v = min(max((x if L["axis"] == "x" else y) + L["offset"], L["min"]), L["max"])
    return (v, t["to_y"]) if L["axis"] == "x" else (t["to_x"], v)


SOLID_CLASSES = ("ROOF", "FACADE", "TREE", "FOREST", "LONETREE", "WATER", "STEEP")  # gb_export.py SOLID
BACK = {"up": "down", "down": "up", "left": "right", "right": "left"}


class World:
    def __init__(self, spec):
        self.spec = spec
        self.scenes = {s["id"]: Scene(s) for s in spec["scenes"]}

    def moves(self, node):
        """Yield (direction, next_node, trigger_or_None)."""
        sid, x, y = node
        sc = self.scenes[sid]
        for d, (dx, dy) in DXY.items():
            if (x, y, d) in sc.blocked:
                continue
            nx, ny = x + dx, y + dy
            if not sc.box_free(nx, ny):
                continue
            t = sc.trigger_at(nx, ny)
            if t:
                yield d, (t["to"], *landing(t, nx, ny)), (t, (nx, ny))
            else:
                yield d, (sid, nx, ny), None

    def bfs(self, start, goal):
        """goal(node, crossed_pair_or_None) -> bool. Returns list of (dir, node, trig) or None."""
        prev = {start: None}
        q = deque([start])
        while q:
            n = q.popleft()
            for d, m, t in self.moves(n):
                if m in prev:
                    continue
                prev[m] = (n, d, t)
                pair = (n[0], m[0]) if t else None
                if goal(m, pair):
                    path = []
                    k = m
                    while prev[k]:
                        p, dd, tt = prev[k]
                        path.append((dd, k, tt))
                        k = p
                    return path[::-1]
                q.append(m)
        return None

    def reachable(self, start):
        seen = {start}
        q = deque([start])
        pairs = set()
        while q:
            n = q.popleft()
            for _, m, t in self.moves(n):
                if t:
                    pairs.add(frozenset((n[0], m[0])))
                if m not in seen:
                    seen.add(m)
                    q.append(m)
        return seen, pairs


# ============================================================ emulator
class Game:
    def __init__(self, rom):
        from pyboy import PyBoy

        self.sym = _read_sym(Path(rom).with_suffix(".sym"))
        self.pb = PyBoy(str(rom), window="null", sound_emulated=False, color_palette=DMG_PALETTE)
        self.pb.set_emulation_speed(0)
        self.frame = 0
        self.actors_addr = self.sym["_actors"][1]
        self.scene_addr = self.sym["_current_scene"][1]
        # The .sym that GB Studio's build emits omits most scene headers (only 2 of 12 here), so
        # tools/gbs GBRunner.scene() returns "bankN:addr" for the rest. The .noi linker list has them all.
        self.scene_of = {}
        for line in Path(rom).with_suffix(".noi").read_text().splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0] == "DEF" and parts[1].startswith("_scene_"):
                v = int(parts[2], 16)
                self.scene_of.setdefault((v >> 16, v & 0xFFFF), parts[1][1:])

    def tick(self, n=1, render=False):
        for _ in range(n):
            self.pb.tick(1, render)
        self.frame += n

    def player(self):
        m, a = self.pb.memory, self.actors_addr
        px = (m[a + 1] | m[a + 2] << 8) >> SUBPIXEL_BITS
        py = (m[a + 3] | m[a + 4] << 8) >> SUBPIXEL_BITS
        return px, py, DIRS.get(m[a + 5], m[a + 5])

    def scene(self):
        m, a = self.pb.memory, self.scene_addr
        key = (m[a], m[a + 1] | m[a + 2] << 8)
        name = self.scene_of.get(key, "")
        return name[len("scene_"):] if name.startswith("scene_") else name

    def node(self):
        px, py, _ = self.player()
        return (self.scene(), px // 8, py // 8)

    def aligned(self):
        px, py, _ = self.player()
        return px % 8 == 0 and py % 8 == 0

    def window_up(self):
        lcdc, wy = self.pb.memory[0xFF40], self.pb.memory[0xFF4A]
        return bool(lcdc & 0x20) and wy < 144

    def screenshot(self, path):
        self.tick(1, True)
        img = self.pb.screen.image.copy().convert("RGB")
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
        return path

    def boot(self):
        for _ in range(200):
            self.tick(5)
            if self.scene() and self.pb.memory[self.actors_addr]:
                break
        self.tick(40, True)

    def settle(self, max_frames=60):
        last, same = None, 0
        for _ in range(max_frames):
            self.tick(1)
            p = self.player()[:2]
            if p == last and self.aligned():
                same += 1
                if same >= 2:
                    return
            else:
                same = 0
            last = p
        return

    def step(self, d, target, timeout=30):
        """One grid step: hold d until the player starts moving (the engine then finishes the 8 px
        move on its own), release, let it settle, and report whether RAM shows `target`.
        Holding until arrival instead lets the engine sample the still-held button on the arrival
        frame and start a second step."""
        sc0, start = self.scene(), self.player()[:2]
        self.pb.button_press(d)
        for _ in range(timeout):
            self.tick(1)
            if self.player()[:2] != start or self.scene() != sc0:
                break
        self.pb.button_release(d)
        if self.scene() != sc0:
            return False
        self.settle()
        return self.scene() == target[0] and self.node() == target

    def push(self, d, times=3):
        """Press into a wall `times` times (holding long enough for a free step to complete)."""
        for _ in range(times):
            self.pb.button_press(d)
            self.tick(24)
            self.pb.button_release(d)
            self.tick(4)
        self.settle()

    def await_warp(self, dest, timeout=240):
        for _ in range(timeout):
            self.tick(1)
            if self.scene() == dest[0] and self.aligned() and self.node() == dest:
                self.tick(30)  # fade in
                return True
        return False

    def face(self, d):
        """Turn toward a blocking signpost (the engine turns the player but does not move it)."""
        self.pb.button_press(d)
        for _ in range(12):
            self.tick(1)
            if self.player()[2] == d:
                break
        self.pb.button_release(d)
        self.settle()
        return self.player()[2] == d

    def read_sign(self, shot):
        opened = False
        for _attempt in range(2):
            self.pb.button_press("a")
            self.tick(3)
            self.pb.button_release("a")
            for _ in range(90):
                self.tick(1)
                if self.window_up():
                    opened = True
            if opened:
                break
        self.tick(30)
        self.screenshot(shot)
        boxes = 0
        for _ in range(8):  # one A per text box
            if not self.window_up():
                break
            self.pb.button_press("a")
            self.tick(2)
            self.pb.button_release("a")
            self.tick(60)
            boxes += 1
        self.tick(20)
        return opened, not self.window_up(), boxes

    def stop(self):
        self.pb.stop(save=False)


# ============================================================ run
def main():
    t0 = time.time()
    spec = json.loads(SPEC.read_text())
    world = World(spec)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    checks = []  # (group, name, ok, detail)
    infos = []  # (group, name, detail): reported, never counted as a pass or a fail

    def check(group, name, ok, detail=""):
        checks.append((group, name, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + f"[{group}] {name}" + (f"  ({detail})" if detail else ""), flush=True)
        return ok

    def info(group, name, detail):
        infos.append((group, name, detail))
        print(f"INFO [{group}] {name}  ({detail})", flush=True)

    # ---------------- static
    from tools.gbs import validate

    rep = validate(spec, REPO)
    check("static", "validate() ok", rep["ok"], "; ".join(rep["errors"])[:200])
    for sid, s in rep["scenes"].items():
        sc = world.scenes[sid]
        small = sc.w <= 20 and sc.h <= 18
        check("limits", f"{sid}: tiles {s['unique_tiles']} <= 180 (hard 192)", s["unique_tiles"] <= 180)
        check("limits", f"{sid}: actors {s['actors']} <= {10 if small else 20}", s["actors"] <= (10 if small else 20))
        check("limits", f"{sid}: triggers {s['triggers']} <= 30", s["triggers"] <= 30)
    bad_lines = [(sc.id, a["name"], ln) for sc in world.scenes.values() for a in sc.actors for ln in a["dialogue"]
                 if len(ln) > MAX_LINE or not ln.isascii()]
    check("static", f"all dialogue lines ASCII and <= {MAX_LINE} chars", not bad_lines, str(bad_lines[:3]))
    p = spec["player"]
    start = (p["scene"], p["x"], p["y"])
    check("static", f"spawn {start} clear of collision and triggers",
          world.scenes[p["scene"]].box_free(p["x"], p["y"]) and not world.scenes[p["scene"]].trigger_at(p["x"], p["y"]))
    bad_land = []
    for sc in world.scenes.values():
        for t in sc.triggers:
            d = world.scenes[t["to"]]
            for lx, ly in sc.landings(t):
                if not d.box_free(lx, ly) or d.trigger_at(lx, ly):
                    bad_land.append(f"{sc.id}/{t['id']}->({lx},{ly})")
    check("static", "every exit lands clear of collision and triggers", not bad_land, ", ".join(bad_land[:5]))
    seen, all_pairs = world.reachable(start)
    reach_sc = {n[0] for n in seen}
    check("static", f"all {len(world.scenes)} scenes reachable from spawn", reach_sc == set(world.scenes),
          f"missing {sorted(set(world.scenes) - reach_sc)}")
    unread = []
    for sc in world.scenes.values():
        for a in sc.actors:
            if not any((sc.id, x, y) in seen for x, y, _ in sc.read_spots(a)):
                unread.append(f"{sc.id}:{a['name']}")
    check("static", "every signpost readable from a reachable tile", not unread, ", ".join(unread))
    # exits that lead nowhere: trigger cells the player can never step onto
    dead = []
    for sc in world.scenes.values():
        for t in sc.triggers:
            cells = [(x, y) for y in range(t["y"], t["y"] + t["h"]) for x in range(t["x"] - 1, t["x"] + t["w"])]
            if not any(sc.box_free(x, y) and any((sc.id, x - dx, y - dy) in seen for dx, dy in DXY.values())
                       for x, y in cells):
                dead.append(f"{sc.id}/{t['id']}")
    # a dead exit pair is harmless (no one gets there); report it, but only fail on the stuck case:
    # a landing from which no exit of that scene can be reached by walking
    info("static", "exit triggers unreachable from the spawn", ", ".join(dead) if dead else "none")
    trapped = []
    for sc in world.scenes.values():
        for t, (lx, ly) in ((t, q) for t in sc.triggers for q in sc.landings(t)):
            land = (t["to"], lx, ly)
            q, seen_l, out = deque([land]), {land}, False
            while q and not out:
                n = q.popleft()
                for _, m, tt in world.moves(n):
                    if tt:
                        out = True
                        break
                    if m not in seen_l:
                        seen_l.add(m)
                        q.append(m)
            if not out:
                trapped.append(f"{sc.id}/{t['id']}->({lx},{ly})")
    check("static", "no exit lands the player where no exit can be walked to (stuck)", not trapped,
          ", ".join(trapped[:6]))
    walk_total = sum(sc.box_free(x, y) for sc in world.scenes.values() for y in range(sc.h) for x in range(sc.w - 1))
    walk_seen = len(seen)
    reach_pct = {}
    for sid, sc in world.scenes.items():
        free = [(x, y) for y in range(sc.h) for x in range(sc.w - 1) if sc.box_free(x, y)]
        reach_pct[sid] = 100.0 * sum((sid, x, y) in seen for x, y in free) / max(1, len(free))
    info("static", "open ground reachable per scene", ", ".join(f"{sid} {p:.0f}%" for sid, p in reach_pct.items()))
    # sideways shift on crossing a border: landing vs the same world tile in the destination
    snaps = []
    for sc in world.scenes.values():
        for t in sc.triggers:
            along_x = t["land"]["axis"] == "x" if t.get("land") else t["h"] == 1
            cells = range(t["x"] - 1, t["x"] + t["w"]) if along_x else range(t["y"], t["y"] + t["h"])
            for e in cells:
                lx, ly = landing(t, e, t["y"]) if along_x else landing(t, t["x"], e)
                snaps.append((abs((lx if along_x else ly) - (e + t["along_offset"])), f"{sc.id}/{t['id']}@{e}"))
    big = sorted((s_ for s_ in snaps if s_[0] > 1), reverse=True)
    info("static", "exit landing sideways shift (tiles)",
         f"max {max(s_[0] for s_ in snaps)}; {sum(s_[0] <= 1 for s_ in snaps)}/{len(snaps)} entry tiles shift <= 1; "
         f"over 1: " + ", ".join(f"{n} by {k}" for k, n in big[:8]))
    print(f"reachable player positions: {walk_seen}/{walk_total}")

    # ---------------- play
    g = Game(ROM)
    g.boot()
    node = g.node()
    check("play", f"boot: player spawns in {start[0]} at ({start[1]},{start[2]})", node == start, f"RAM {node}")
    shots = {}
    wall_shots = []

    def arrive(sid):
        if sid not in shots:
            shots[sid] = g.screenshot(OUT / f"scene_{len(shots) + 1:02d}_{sid}.png")

    arrive(node[0])
    # every signpost (a superset of "one per scene"), keyed (scene, actor index)
    sign_goals = {(sid, k) for sid, sc in world.scenes.items() for k in range(len(sc.actors))}
    pair_goals = set(all_pairs)
    # wall tests: in every scene, one push into each solid metatile class present there, from a reachable tile
    wall_goals, wall_spots = set(), {}
    for sid, sc in world.scenes.items():
        present = {c for row in sc.mclass for c in row}
        for cls in SOLID_CLASSES:
            if cls not in present:
                continue
            every = sc.wall_spots(cls)
            spots = [q for q in every if (sid, q[0], q[1]) in seen]
            if spots:
                wall_goals.add((sid, cls))
                wall_spots[(sid, cls)] = spots
            elif every:
                info("wall", f"{sid}: {cls} not tested", "open ground beside it is unreachable from the spawn")
            else:
                info("wall", f"{sid}: {cls} not tested", "no open tile beside it (FOREST interior is ringed by "
                     "TREE; tested via TREE)" if cls == "FOREST" else "no open tile beside it with room to step back")
    walls_done = set()
    signs_done, pairs_done = {}, set()
    stuck = []
    steps = 0
    budget_s = 1500
    while (sign_goals - set(signs_done) or pair_goals - pairs_done or wall_goals - walls_done) \
            and time.time() - t0 < budget_s:
        cur = g.node()
        want_sign = sign_goals - set(signs_done)
        want_pair = pair_goals - pairs_done
        targets = {}
        for sid, k in want_sign:
            sc = world.scenes[sid]
            a = sc.actors[k]
            for x, y, d in sc.read_spots(a):
                targets.setdefault((sid, x, y), ((sid, k), a, d))
        for key in wall_goals - walls_done:
            for x, y, d in wall_spots[key]:
                targets.setdefault((key[0], x, y), (key, None, d))

        def goal(m, pair):
            return m in targets or (pair is not None and frozenset(pair) in want_pair)

        path = world.bfs(cur, goal)
        if path is None:
            if cur in targets:
                path = []
            else:
                check("play", f"route exists from {cur} to a remaining goal", False,
                      f"signs left {sorted(want_sign)}, boundaries left {[sorted(p) for p in want_pair]}")
                break
        replan = False
        for d, nxt, trig in path:
            here = g.node()
            if trig:
                t, cell = trig
                ok = g.step(d, (here[0], cell[0], cell[1]))
                warped = g.await_warp(nxt)
                steps += 1
                pair = frozenset((here[0], nxt[0]))
                got = g.node()
                if warped:
                    if pair not in pairs_done:
                        pairs_done.add(pair)
                        check("boundary", f"{here[0]} -> {nxt[0]} via {t['id']}: lands at ({nxt[1]},{nxt[2]})", True)
                    arrive(nxt[0])
                else:
                    stuck.append((here, d, "warp", got))
                    check("boundary", f"{here[0]} -> {nxt[0]} via {t['id']}", False, f"RAM {got}, expected {nxt}")
                    world.scenes[here[0]].blocked.add((here[1], here[2], d))
                    pair_goals.discard(pair) if len(stuck) > 40 else None
                    replan = True
                    break
            else:
                ok = g.step(d, nxt)
                steps += 1
                if not ok:
                    got = g.node()
                    stuck.append((here, d, "move", got))
                    print(f"  stuck: {here} {d} -> RAM {got}", flush=True)
                    world.scenes[here[0]].blocked.add((here[1], here[2], d))
                    replan = True
                    break
        if replan:
            if len(stuck) > 60:
                check("play", "too many stuck steps; giving up", False)
                break
            continue
        cur = g.node()
        if cur in targets and targets[cur][1] is None:   # wall test
            key, _, d = targets[cur]
            sid, cls = key
            sc = world.scenes[sid]
            dx, dy = DXY[d]
            hit = sorted({sc.cls_at(cx, cur[2] + dy) for cx in (cur[1] + dx, cur[1] + dx + 1)
                          if sc.solid_at(cx, cur[2] + dy)})
            g.push(d)
            after, facing = g.node(), g.player()[2]
            walls_done.add(key)
            shot = OUT / f"wall_{sid}_{cls.lower()}.png"
            g.screenshot(shot)
            wall_shots.append((f"{sid}: {cls} ({d})", shot))
            check("wall", f"{sid}: pushing {d} into {cls} at ({cur[1]},{cur[2]}) does not move the player",
                  after == cur and facing == d, f"RAM {after} facing {facing}; solid {hit}")
            back = (sid, cur[1] - dx, cur[2] - dy)
            moved = g.step(BACK[d], back)
            check("wall", f"{sid}: control step {BACK[d]} away from {cls} moves the player", moved,
                  f"RAM {g.node()}, expected {back}")
            if not moved:
                stuck.append((cur, BACK[d], "control", g.node()))
            continue
        if cur in targets:
            key, a, d = targets[cur]
            g.face(d)
            sid = cur[0]
            shot = OUT / f"sign_{sid}_{key[1]:02d}.png"
            opened, closed, boxes = g.read_sign(shot)
            exp_boxes = -(-len(a["dialogue"]) // LINES_PER_BOX)
            signs_done[key] = (a["name"], str(shot.relative_to(OUT)))
            check("sign", f"{sid}: read '{a['name']}' at ({cur[1]},{cur[2]}) facing {d}",
                  opened and closed and boxes == exp_boxes,
                  f"opened={opened} closed={closed} boxes={boxes}/{exp_boxes}")
    for sid, k in sorted(sign_goals - set(signs_done)):
        check("sign", f"{sid}: read '{world.scenes[sid].actors[k]['name']}'", False, "not reached")
    for pr in pair_goals - pairs_done:
        check("boundary", f"cross {' <-> '.join(sorted(pr))}", False, "not reached")
    for sid, cls in sorted(wall_goals - walls_done):
        check("wall", f"{sid}: push into {cls}", False, "not reached")
    missing = [sid for sid in world.scenes if sid not in shots]
    if missing:
        info("screens", "scenes never visited (no screenshot)", ", ".join(missing))
    check("play", "no stuck steps (RAM matched the planned tile at every step)", not stuck,
          "; ".join(f"{h} {d}->{got}" for h, d, _, got in stuck[:5]))
    g.stop()

    # ---------------- contact sheet
    contact_sheet([(sid, shots[sid]) for sid in world.scenes if sid in shots], OUT / "contact_sheet.png")
    sign_sheet = [(f"{sid}: {name}", OUT / f) for (sid, _k), (name, f) in sorted(signs_done.items())]
    if sign_sheet:
        contact_sheet(sign_sheet, OUT / "contact_sheet_signs.png", scale=1, cols=8)
    if wall_shots:
        contact_sheet(wall_shots, OUT / "contact_sheet_walls.png", scale=1, cols=8)

    # ---------------- results
    n_pass = sum(c[2] for c in checks)
    n_fail = len(checks) - n_pass
    lines = [
        "# Reed World playtest results", "",
        f"ROM `build/reed_world.gb`, spec `world/gb/project_spec.json`. Generated by `scripts/playtest.py`.", "",
        f"**{n_pass} passed, {n_fail} failed** ({len(checks)} checks), plus {len(infos)} info rows that are "
        f"reported but not counted. {steps} tile steps, {g.frame} frames, "
        f"{time.time() - t0:.0f} s. Reachable player positions: {walk_seen} of {walk_total} open ones.", "",
        "Screenshots: `scene_NN_<id>.png` (first arrival in each scene), `sign_<id>_<k>.png` (each dialogue), "
        "`wall_<id>_<class>.png` (each wall push), `contact_sheet.png`, `contact_sheet_signs.png`, "
        "`contact_sheet_walls.png`.", "",
        "| Group | Check | Result | Detail |", "|---|---|---|---|",
    ]
    for grp, name, ok, det in checks:
        lines.append(f"| {grp} | {name} | {'PASS' if ok else '**FAIL**'} | {det.replace('|', '/')} |")
    lines += ["", "## Info (not counted)", "", "| Group | Item | Detail |", "|---|---|---|"]
    lines += [f"| {grp} | {name} | {det.replace('|', '/')} |" for grp, name, det in infos]
    if stuck:
        lines += ["", "## Stuck steps", ""] + [f"- {h} pressing {d} ({k}); RAM then {got}" for h, d, k, got in stuck]
    (OUT / "RESULTS.md").write_text("\n".join(lines) + "\n")
    (OUT / "results.json").write_text(json.dumps(
        {"pass": n_pass, "fail": n_fail, "info": len(infos),
         "checks": [dict(group=a, name=b, ok=c, detail=d) for a, b, c, d in checks],
         "infos": [dict(group=a, name=b, detail=c) for a, b, c in infos],
         "signs": {f"{k[0]}/{k[1]}": v for k, v in signs_done.items()}, "stuck": [list(map(str, s)) for s in stuck]}, indent=1))
    print(f"\n{n_pass} passed, {n_fail} failed, {len(infos)} info -> {(OUT / 'RESULTS.md').relative_to(REPO)}")
    return 0 if n_fail == 0 else 1


def contact_sheet(items, out, scale=2, cols=4):
    from PIL import Image, ImageDraw

    pad, label = 8, 14
    cw, ch = 160 * scale + pad, 144 * scale + label + pad
    rows = -(-len(items) // cols)
    sheet = Image.new("RGB", (cols * cw + pad, rows * ch + pad), (0x07, 0x18, 0x21))
    d = ImageDraw.Draw(sheet)
    for k, (name, path) in enumerate(items):
        x, y = pad + (k % cols) * cw, pad + (k // cols) * ch
        d.text((x, y), name, fill=(0xE0, 0xF8, 0xCF))
        sheet.paste(Image.open(path).convert("RGB").resize((160 * scale, 144 * scale), 0), (x, y + label))
    sheet.save(out)


if __name__ == "__main__":
    sys.exit(main())
