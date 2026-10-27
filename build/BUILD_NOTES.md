# Reed World: build notes

Integration pass, overnight 26–27 Oct 2026, branch `gb-overnight`; review fixes (docs/REVIEW.md) applied the same night.

## Open it in GB Studio 4.3.2

1. In the GB Studio 4.3.2 app, choose **Open Project** and pick `game/reed-world/reed_world.gbsproj`.
2. It should open with **no migration prompt**. GB Studio's own loader, run headless by `check_project`, reports file version `4.2.0 r10` (what 4.3.2 writes), `isMigrated: false`, no sprites needing re-detection and no schema problems. The GUI itself could not be exercised headless.
3. Expect 12 scenes (start: **Front Lawn**), 61 signposts, 182 exit triggers, 2 sprites (`player`, `signpost`), Monochrome mode, 8×16 sprite mode.
4. **Play** runs it in the app's emulator. **Export ROM** and **Export Web** produce the same ROM as the headless build.

> **Who owns what.** `game/reed-world/` is the hand-owned copy: edit it in the app freely. `scripts/build_game.py` writes the generated project to `build/gbs-project/` (overwritten on every run, and what the ROM and web build are made from) and creates `game/reed-world/` only if it is missing. `--sync-game` replaces it after moving the old copy to `game/reed-world.bak-<timestamp>/`. The `art/` PNGs are likewise left alone unless you pass `--regen-art`, so an Aseprite pass survives a rebuild. To build the ROM from your hand-edited copy, use `python3 -m tools.gbs` on `game/reed-world` (or Export ROM in the app).

## Outputs

| Path | What |
|---|---|
| `build/gbs-project/` | Generated GB Studio project (not committed; rebuilt every run) |
| `build/reed_world.gb` | ROM, 512 KB MBC5 (+ `.sym`; `.map`, `.noi`, `.build.log` are generated locally and git-ignored) |
| `build/web/index.html` | HTML5 build (binjgb), defaulting to the DMG green palette. Serve the folder over HTTP, e.g. `python3 -m http.server -d build/web` |
| `build/playtest/RESULTS.md` | Pass/fail per check |
| `build/playtest/contact_sheet.png` | One screenshot per scene (first arrival) |
| `build/playtest/contact_sheet_signs.png` | Every signpost's dialogue |
| `build/playtest/contact_sheet_walls.png` | Every wall push |
| `build/playtest/scene_NN_<id>.png`, `sign_<id>_<k>.png` | Individual screenshots |

## Rebuild

```sh
python3 scripts/build_game.py                 # export -> validate -> build/gbs-project -> check -> ROM -> web  (~70 s); art/ used as is
python3 scripts/build_game.py --regen-art     # also redraw art/ with art/make_art.py (overwrites art/ PNGs)
python3 scripts/build_game.py --sync-game     # also replace game/reed-world/ (old copy backed up first)
python3 scripts/build_game.py --playtest      # ... then the scripted walkthrough (~15 s more)
python3 scripts/build_game.py --skip-export --no-web   # project + ROM only, from current world/gb
python3 scripts/playtest.py                   # walkthrough against the current build/reed_world.gb
```

This needs the GB Studio 4.3.2 checkout at `$GBS_SRC`, default `../gbs-src` next to this repo (create it with `tools/gbs/setup_gbstudio.sh`, which honours the same variable), plus PyBoy 2.8.1, numpy, scipy, rasterio, shapely and Pillow. `gb_export.py` now also imports `scipy.ndimage`.

## How the playtest works

`scripts/playtest.py` models the GB Studio 4.3.2 top-down engine, as read from gbvm `states/topdown.c`, `core/trigger.c` and `core/collision.c`:
- 8 px grid moves
- the player's 16×8 box (row y, columns x..x+1)
- signposts are solid 16×16 actors
- a trigger fires when the box overlaps it
- tiles outside the scene are solid

It plans with BFS over every scene's collision grid and the trigger graph, nothing hand-tuned. It then tours greedily to the nearest unfinished goal: read every signpost, and cross every scene boundary pair.

Each grid step is driven closed-loop: press until the player starts moving, release, settle, and compare the RAM position with the plan. After every warp it asserts the destination scene and landing tile from RAM. Exits land proportionally, so the expected tile depends on where the player crossed (see "Exit landings" in `world/gb/REPORT.md`).

In every scene, for each solid metatile class present, it walks to a reachable tile beside that class, pushes into it 3 times and asserts in RAM that the player neither moved nor failed to face it, then steps the other way as a positive control. A ROM with no collisions fails these. After every A press it asserts that a text box opened (window layer up, `WY < 144`), and that it closed after the expected number of boxes.

## What passes: 248 / 248 (plus 19 info rows, not counted)

- **Static (8).** `validate()`; dialogue ASCII and ≤ 18 chars; spawn clear; every possible exit landing clear of collision and triggers; all 12 scenes and all 61 signposts reachable and readable; no landing that cannot walk to an exit.
- **Limits (36).** Every scene ≤ 180 unique tiles (actual 62–98), ≤ 20 actors (max 11), ≤ 30 triggers (max 28).
- **Play.**
  - Boot into Front Lawn at (92,47).
  - All 20 scene-boundary pairs crossed, each landing asserted.
  - All 61 signposts read (text box opened, closed after the expected box count).
  - **Walls (122 = 61 pushes + 61 controls).** Every reachable solid class in every scene: ROOF, FACADE, TREE, LONETREE, WATER, STEEP.
  - 0 stuck steps.
- **Info rows (not counted).** Reachable ground per scene; exits unreachable from the spawn; exit landing shift; wall classes that cannot be tested (FOREST interiors are ringed by TREE everywhere; Reed Lake's roofs are in the walled-off neighbourhood; two STEEP patches have no open tile beside them).
- The dialogue text itself is checked by eye in `contact_sheet_signs.png`, not by the script: GB Studio draws text into runtime tiles, so the window tile map does not spell the line.

## Review fixes (docs/REVIEW.md)

- **M2.** Art regeneration is opt-in (`--regen-art`); the generated project goes to `build/gbs-project/`; `game/reed-world/` is hand-owned (see above). It was synced once with `--sync-game` for this build; it had no hand edits.
- **M1.** Wall tests in every scene, info rows reported separately, the 22 rows that could not fail ("at least one signpost read", "screenshot") dropped.
- **Exits (Minor 2).** Landing = clamp(entry + offset) along the edge, as a GB Studio script value. 764 of 790 entry tiles shift ≤ 1 tile; the worst is 6 tiles at one Science Quad → Studio Arts span whose far side has no standing room nearer. Trigger `_index` order documented in `tools/gbs/project.py`.
- **Signs.** Hand-fixed text for Old Dorm Block, Dorothy Johansen House and Prexy Hall (`SIGN_TEXT` in `gb_export.py`, CSV facts only). New name-only signs for Reed Canyon (way/787034614) and Reed Lake (way/39516063). The CSV has no row for either, so they say "Reed College's canyon/lake."
- **Notes.** Actor notes now carry `door estimated` and `mounted on facade`.
- **Paths.** No `/home/claude` defaults; logs are repo-relative; generated `.map`, `.noi`, `.build.log` and `tests/gbs_smoke/out/` are git-ignored.

## What changed tonight

- **Forest edges are solid.** Every tree metatile (edge, interior, lone) is solid. Paths through woods stay open.
- **Roads vs water.** Roads now have a dark edge, a 2 px pale kerb, a gutter, dark asphalt grain and dashed centre lines. Water keeps its light waves and foam.
- **Paved blobs.**
  - Paved is drawn only on OSM parking.
  - Lidar-only path becomes lawn.
  - Path tiles now join only along the OSM way that links them (`PATH_LINKS`), so Old Campus shows separate footways instead of a pale slab.
- **Signposts.**
  - A sign never stands on a cell whose removal splits the walkable area. Several signs on one-metatile paths had walled off most of Science Quad.
  - A sign never stands on an exit landing spot.
  - With no free ground beside a door, the sign is mounted on the façade beside it, read from the doorway: Biology, Psychology, Dorothy Johansen House and Canyon House. No sign stands on a door now.
- **Doors in sealed clearings.** For 7 buildings, a 1–3 metatile path was carved through canopy to the main area. These are flagged as estimated in `world/gb/REPORT.md`.
- **Exits.**
  - The solid outer tile ring is gone, because the engine already treats off-scene tiles as solid.
  - Landing spots are searched a few tiles around the span, and a spot is accepted only if the player can walk back onto the partner trigger.
  - 8 exit pairs are now dropped: 10 before tonight, and 29 once trees became solid. The canyon trail through Reed Lake is now walkable end to end.
- **Dialogue.** Dialogue can now run to 6 lines (two full text boxes). Before, 4 lines left a lone fourth line in box 2.

## Known issues

- **Reed Lake is only 25 % reachable.** The off-campus neighbourhood north of the lake is walled in by canopy, with no exit or door. The trail and the new Reed Lake sign are reachable. Other scenes are 87–98 % reachable; the rest is small pockets behind trees. Per-scene figures are in RESULTS.md.
- **Unused exits.** 9 exit triggers can't be reached from either side, which is harmless. 8 exit pairs were dropped for lack of room (REPORT.md, Notes).
- **Lidar canopy is dense.** The Science Quad and the canyon read as forest with paths through them. That matches the data, but it is darker than the real quad.
- **Estimated content.** The carved paths and most doors (estimated, as before) are gameplay choices, not survey data.
- **`tools/gbs` scene lookup (Engine-owned, not changed).** `GBRunner.scene()` reads `<rom>.sym`, but GB Studio's `.sym` lists only 2 of the 12 scene headers, so it returns `bankN:addr` for the rest. `scripts/playtest.py` reads scene addresses from `<rom>.noi` instead. The same fix belongs in `tools/gbs/play.py`.
- **Dialogue wording.** It is cut to whole clauses, but some wraps are awkward (for example "mostly" alone on a line). Several apartment blocks share identical text.
- **Defaults.** The font and dialogue frame are GB Studio's defaults. There is no title screen or music.
- **Web colours.** The web build defaults to binjgb built-in palette 62, within 3.5 RGB units of the project's DMG greens (the template's `DEFAULT_PALETTE_IDX`; palette switching still works).
- **Exit shift.** A few left/right spans still shift the player 2–6 tiles where the far side has no room for the 16 px player nearer (RESULTS.md info row).
