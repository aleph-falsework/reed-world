"""Negative tests for tools.gbs.validate: each broken spec must report the
expected error. Run from the repo root: python3 tests/gbs_smoke/test_validate.py"""
import copy
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
from tools.gbs import validate  # noqa: E402
from tools.gbs.constants import DMG_RGB  # noqa: E402

BASE = json.loads((HERE / "spec.json").read_text())  # written by run_smoke.py


def img(path, w, h, fn):
    idx = fn(w, h)
    rgb = np.zeros((h, w, 3), np.uint8)
    for k, c in enumerate(DMG_RGB):
        rgb[idx == k] = c
    Image.fromarray(rgb).save(path)
    return str(path)


def expect(name, spec, needle):
    rep = validate(spec, REPO)
    hit = any(needle in e for e in rep["errors"])
    print(("PASS " if hit else "FAIL ") + name + ("" if hit else f"  errors={rep['errors']}"))
    return hit


def main():
    tmp = Path(tempfile.mkdtemp())
    ok = []
    ok.append(validate(BASE, REPO)["ok"])
    print(("PASS " if ok[-1] else "FAIL ") + "base smoke spec is valid")

    s = copy.deepcopy(BASE)
    rng = np.random.default_rng(0)
    s["scenes"][0]["background"] = img(tmp / "noisy.png", 160, 144, lambda w, h: rng.integers(0, 4, (h, w)))
    ok.append(expect("more than 192 unique tiles", s, "unique 8x8 tiles > 192"))

    s = copy.deepcopy(BASE)
    p = tmp / "offpal.png"
    Image.new("RGB", (160, 144), (255, 0, 0)).save(p)
    s["scenes"][0]["background"] = str(p)
    ok.append(expect("off-palette colour", s, "off-palette"))

    s = copy.deepcopy(BASE)
    s["scenes"][0]["background"] = img(tmp / "odd.png", 164, 144, lambda w, h: np.zeros((h, w), int))
    ok.append(expect("not a multiple of 8", s, "not a multiple of 8"))

    s = copy.deepcopy(BASE)
    s["scenes"][0]["background"] = img(tmp / "wide.png", 2048, 144, lambda w, h: np.zeros((h, w), int))
    s["scenes"][0]["width"] = 256
    ok.append(expect("wider than 2040 px", s, "exceeds 2040"))

    s = copy.deepcopy(BASE)
    s["scenes"][0]["background"] = img(tmp / "big.png", 1600, 1600, lambda w, h: np.zeros((h, w), int))
    s["scenes"][0]["width"] = s["scenes"][0]["height"] = 200
    ok.append(expect("more than 1,048,320 px", s, "px^2 exceeds"))

    s = copy.deepcopy(BASE)
    a = s["scenes"][0]["actors"][0]
    s["scenes"][0]["actors"] = [dict(a, id=f"n/{i}") for i in range(11)]
    ok.append(expect("11 actors on a one-screen scene", s, "actors > 10"))

    s = copy.deepcopy(BASE)
    t = s["scenes"][0]["triggers"][0]
    s["scenes"][0]["triggers"] = [dict(t, id=f"t{i}") for i in range(31)]
    ok.append(expect("31 triggers", s, "triggers > 30"))

    s = copy.deepcopy(BASE)
    s["scenes"][0]["triggers"][0]["to"] = "nowhere"
    ok.append(expect("trigger to unknown scene", s, "is not a scene id"))

    s = copy.deepcopy(BASE)
    s["player"]["x"] = 12
    ok.append(expect("player starts inside the wall", s, "solid collision"))

    s = copy.deepcopy(BASE)
    s["scenes"][0]["collisions"] = s["scenes"][0]["collisions"][:-1]
    ok.append(expect("collision grid wrong size", s, "collisions must be"))
    return all(ok)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
