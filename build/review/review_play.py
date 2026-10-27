#!/usr/bin/env python3
"""Reviewer's own PyBoy session (independent of scripts/playtest.py).

Reads collisions, actors and triggers from game/reed-world/ (the GB Studio project), not the
exporter spec. Visits several scenes, deliberately walks into buildings, trees and water and
asserts from RAM that the player did not move, steps away as a positive control, and reads
three signposts. Screenshots go to build/review/.

    python3 build/review/review_play.py
"""
import glob, json, sys
from collections import deque
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "build/review"
ROM = REPO / "build/reed_world.gb"
PROJ = REPO / "game/reed-world/project/scenes"
DXY = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
DIRS = {0: "down", 1: "right", 2: "up", 3: "left"}
PAL = (0xE0F8CF, 0x86C06C, 0x306850, 0x071821)


def rle(s):
    out, i = [], 0
    while i < len(s):
        v = int(s[i:i + 2], 16); i += 2
        if s[i] == "!":
            out.append(v); i += 1
        else:
            j = s.index("+", i); out += [v] * int(s[i:j], 16); i = j + 1
    return out


# ---------------- model from the GB Studio project
scenes, by_id = {}, {}
for d in sorted(glob.glob(str(PROJ) + "/*/")):
    s = json.load(open(d + "scene.gbsres"))
    sym = s["symbol"][len("scene_"):]
    col = rle(s["collisions"])
    acts = [json.load(open(f)) for f in glob.glob(d + "actors/*.gbsres")]
    trs = sorted((json.load(open(f)) for f in glob.glob(d + "triggers/*.gbsres")), key=lambda t: t["_index"])
    scenes[sym] = dict(w=s["width"], h=s["height"], col=col, actors=acts, triggers=trs, id=s["id"])
    by_id[s["id"]] = sym
cls = {sid: json.load(open(REPO / f"world/gb/{sid}/meta.json"))["metatile_classes"] for sid in scenes}


def solid(sid, x, y):
    S = scenes[sid]
    if not (0 <= x < S["w"] and 0 <= y < S["h"]):
        return True
    return S["col"][y * S["w"] + x] != 0


def actor_at(sid, x, y):
    for a in scenes[sid]["actors"]:
        if abs(x - a["x"]) <= 1 and a["y"] - 1 <= y <= a["y"]:
            return a


def free(sid, x, y):
    return not solid(sid, x, y) and not solid(sid, x + 1, y) and x + 1 < scenes[sid]["w"] and not actor_at(sid, x, y)


def trig_at(sid, x, y):
    for t in scenes[sid]["triggers"]:
        if t["y"] <= y < t["y"] + t["height"] and t["x"] <= x + 1 and x < t["x"] + t["width"]:
            return t


def dest(t):
    a = [e for e in t["script"] if e["command"] == "EVENT_SWITCH_SCENE"][0]["args"]
    return (by_id[a["sceneId"]], a["x"]["value"], a["y"]["value"])


def moves(n):
    sid, x, y = n
    for d, (dx, dy) in DXY.items():
        nx, ny = x + dx, y + dy
        if not free(sid, nx, ny):
            continue
        t = trig_at(sid, nx, ny)
        yield d, (dest(t) if t else (sid, nx, ny)), bool(t)


def bfs(start, goal):
    prev, q = {start: None}, deque([start])
    while q:
        n = q.popleft()
        if goal(n) and n != start:
            path = []
            while prev[n]:
                p, d, w = prev[n]; path.append((d, n, w)); n = p
            return path[::-1]
        for d, m, w in moves(n):
            if m not in prev:
                prev[m] = (n, d, w); q.append(m)
    return None


def mclass(sid, x, y):
    c = cls[sid]
    my, mx = y // 2, x // 2
    return c[my][mx] if 0 <= my < len(c) and 0 <= mx < len(c[0]) else None


# ---------------- emulator
from pyboy import PyBoy  # noqa: E402


def read_sym(p):
    out = {}
    for line in Path(p).read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and ":" in parts[0]:
            b, a = parts[0].split(":"); out[parts[1]] = (int(b, 16), int(a, 16))
    return out


sym = read_sym(ROM.with_suffix(".sym"))
scene_of = {}
for line in ROM.with_suffix(".noi").read_text().splitlines():
    p = line.split()
    if len(p) == 3 and p[0] == "DEF" and p[1].startswith("_scene_") and not p[1].endswith(("_actors", "_triggers", "_collisions", "_sprites", "_init", "_projectiles", "_script")):
        v = int(p[2], 16); scene_of.setdefault((v >> 16, v & 0xFFFF), p[1][len("_scene_"):])
pb = PyBoy(str(ROM), window="null", sound_emulated=False, color_palette=PAL)
pb.set_emulation_speed(0)
A, SC = sym["_actors"][1], sym["_current_scene"][1]
results = []


def tick(n=1):
    for _ in range(n):
        pb.tick(1, True)


def pos():
    m = pb.memory
    return ((m[A + 1] | m[A + 2] << 8) >> 5, (m[A + 3] | m[A + 4] << 8) >> 5, DIRS.get(m[A + 5]))


def scene():
    m = pb.memory
    return scene_of.get((m[SC], m[SC + 1] | m[SC + 2] << 8), "?")


def node():
    x, y, _ = pos(); return (scene(), x // 8, y // 8)


def shot(name):
    tick(1); pb.screen.image.convert("RGB").resize((320, 288), 0).save(OUT / f"{name}.png")


def settle():
    last, same = None, 0
    for _ in range(80):
        tick(1); p = pos()[:2]
        if p == last and p[0] % 8 == 0 and p[1] % 8 == 0:
            same += 1
            if same >= 3:
                return
        else:
            same = 0
        last = p


def press(d, hold_until_move=True, frames=30):
    s0, p0 = scene(), pos()[:2]
    pb.button_press(d)
    for _ in range(frames):
        tick(1)
        if hold_until_move and (pos()[:2] != p0 or scene() != s0):
            break
    pb.button_release(d)


def walk(path):
    hist = []
    for d, nxt, warp in path:
        hist.append((node(), d, nxt, warp))
        press(d)
        if warp:
            for _ in range(300):
                tick(1)
                if node() == nxt:
                    break
            tick(40)
        settle()
        if node() != nxt:
            print("   DIVERGED; last steps:", hist[-3:], "RAM", node(), flush=True)
            return False, nxt
    return True, None


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def goto(goal, label):
    path = bfs(node(), goal)
    if path is None:
        check(f"route to {label}", False, f"from {node()}"); return False
    ok, exp = walk(path)
    check(f"walk to {label} ({len(path)} steps)", ok, f"RAM {node()}" + (f" expected {exp}" if exp else ""))
    return ok


def wall_test(sid, classes, label):
    """Go to a tile whose next step in some direction enters a solid tile of one of `classes`,
    press into it, assert no movement; then step back as a positive control."""
    def cand(n):
        if n[0] != sid:
            return False
        for d, (dx, dy) in DXY.items():
            nx, ny = n[1] + dx, n[2] + dy
            cells = [(nx, ny), (nx + 1, ny)]
            if any(solid(sid, cx, cy) and mclass(sid, cx, cy) in classes for cx, cy in cells) \
                    and not actor_at(sid, nx, ny) and not trig_at(sid, n[1], n[2]):
                back = (n[1] - dx, n[2] - dy)
                if free(sid, *back) and not trig_at(sid, *back):
                    return d
        return False
    if not goto(lambda n: bool(cand(n)), f"{label} edge in {sid}"):
        return
    d = cand(node())
    before = node()
    tgt = [mclass(sid, before[1] + DXY[d][0] + k, before[2] + DXY[d][1]) for k in (0, 1)]
    for _ in range(3):
        press(d, hold_until_move=False, frames=24); tick(4)
    settle()
    after = node()
    shot(f"wall_{sid}_{label.replace(' ', '_')}")
    check(f"{sid}: pushing {d} into {label} ({tgt}) blocked", after == before and pos()[2] == d,
          f"before {before} after {after} facing {pos()[2]}")
    back = {"up": "down", "down": "up", "left": "right", "right": "left"}[d]
    press(back); settle()
    exp = (sid, before[1] - DXY[d][0], before[2] - DXY[d][1])
    check(f"{sid}: control step {back} away from {label} moves", node() == exp, f"RAM {node()} expected {exp}")


def read_sign(sid, name):
    a = [x for x in scenes[sid]["actors"] if x["name"] == name][0]
    spot = (sid, a["x"], a["y"] + 1)  # directly below, facing up
    if not free(*spot):
        spot = (sid, a["x"] - 2, a["y"])
    face = "up" if spot[2] == a["y"] + 1 else "right"
    if not goto(lambda n: n == spot, f"sign {name}"):
        return
    press(face, hold_until_move=False, frames=6); settle()
    lcdc = lambda: bool(pb.memory[0xFF40] & 0x20) and pb.memory[0xFF4A] < 144
    pb.button_press("a"); tick(3); pb.button_release("a")
    up = False
    for _ in range(90):
        tick(1); up = up or lcdc()
    tick(30)
    shot(f"sign_{sid}_{name.replace(' ', '_')}")
    text = [e for e in a["script"] if e["command"] == "EVENT_TEXT"][0]["args"]["text"]
    for _ in range(len(text) + 2):
        if not lcdc():
            break
        pb.button_press("a"); tick(2); pb.button_release("a"); tick(60)
    check(f"{sid}: sign '{name}' opens and closes", up and not lcdc(), f"boxes expected {len(text)}; first box {text[0]!r}")


# ---------------- session
OUT.mkdir(parents=True, exist_ok=True)
for _ in range(300):
    tick(5)
    if scene() != "?" and pb.memory[A]:
        break
tick(60)
shot("boot")
check("boots into front_lawn at (92,47)", node() == ("front_lawn", 92, 47), f"RAM {node()}")

wall_test("front_lawn", {"TREE", "FOREST", "LONETREE"}, "trees")
wall_test("front_lawn", {"ROOF", "FACADE"}, "building")
shot("scene_front_lawn")
read_sign("front_lawn", "Eric V. Hauser Memorial Library")

goto(lambda n: n[0] == "old_campus", "old_campus")
shot("scene_old_campus")
wall_test("old_campus", {"WATER"}, "water")
wall_test("old_campus", {"ROOF", "FACADE"}, "building")
read_sign("old_campus", "Eliot Hall")

goto(lambda n: n[0] == "science_quad", "science_quad")
shot("scene_science_quad")
wall_test("science_quad", {"WATER"}, "water")
wall_test("science_quad", {"TREE", "FOREST"}, "trees")
read_sign("science_quad", "Chemistry")

goto(lambda n: n[0] == "reed_lake", "reed_lake")
shot("scene_reed_lake")
wall_test("reed_lake", {"WATER"}, "water")

goto(lambda n: n[0] == "cross_canyon", "cross_canyon")
shot("scene_cross_canyon")
wall_test("cross_canyon", {"ROOF", "FACADE"}, "building")

pb.stop(save=False)
n = sum(r[1] for r in results)
print(f"\n{n}/{len(results)} passed")
(OUT / "review_play.json").write_text(json.dumps([dict(check=a, ok=b, detail=c) for a, b, c in results], indent=1))
