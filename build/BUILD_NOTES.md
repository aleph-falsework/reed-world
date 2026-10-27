# Reed World: build notes

Integration pass, overnight 26–27 Oct 2026, branch `gb-overnight` (not committed).

## Open it in GB Studio 4.3.2

1. In the GB Studio 4.3.2 app, choose **Open Project** and pick `game/reed-world/reed_world.gbsproj`.
2. It should open with **no migration prompt**. GB Studio's own loader, run headless by `check_project`, reports file version `4.2.0 r10` (what 4.3.2 writes), `isMigrated: false`, no sprites needing re-detection and no schema problems. The GUI itself could not be exercised headless.
3. Expect 12 scenes (start: **Front Lawn**), 59 signposts, 176 exit triggers, 2 sprites (`player`, `signpost`), Monochrome mode, 8×16 sprite mode.
4. **Play** runs it in the app's emulator. **Export ROM** and **Export Web** produce the same ROM as the headless build.

> **Caution:** `scripts/build_game.py` deletes and regenerates `game/reed-world/` on every run. If you edit in the app, copy the project somewhere else first, or rebuild with `--skip-art --skip-export` only when you mean to overwrite it.

## Outputs

| Path | What |
|---|---|
| `build/reed_world.gb` | ROM, 512 KB MBC5 (+ `.sym`, `.map`, `.noi`, `.build.log`) |
| `build/web/index.html` | HTML5 build (binjgb). Serve the folder over HTTP, e.g. `python3 -m http.server -d build/web` |
| `build/playtest/RESULTS.md` | Pass/fail per check |
| `build/playtest/contact_sheet.png` | One screenshot per scene (first arrival) |
| `build/playtest/contact_sheet_signs.png` | Every signpost's dialogue |
| `build/playtest/scene_NN_<id>.png`, `sign_<id>_<k>.png` | Individual screenshots |

## Rebuild

```sh
python3 scripts/build_game.py                 # art -> export -> validate -> project -> check -> ROM -> web  (~70 s)
python3 scripts/build_game.py --playtest      # ... then the scripted walkthrough (~10 s more)
python3 scripts/build_game.py --skip-art --skip-export --no-web   # project + ROM only, from current world/gb
python3 scripts/playtest.py                   # walkthrough against the current build/reed_world.gb
```

This needs the GB Studio 4.3.2 checkout in `/home/claude/gbs-src` (recreate it with `tools/gbs/setup_gbstudio.sh`), plus PyBoy 2.8.1, numpy, scipy, rasterio, shapely and Pillow. `gb_export.py` now also imports `scipy.ndimage`.

## How the playtest works

`scripts/playtest.py` models the GB Studio 4.3.2 top-down engine, as read from gbvm `states/topdown.c`, `core/trigger.c` and `core/collision.c`:
- 8 px grid moves
- the player's 16×8 box (row y, columns x..x+1)
- signposts are solid 16×16 actors
- a trigger fires when the box overlaps it
- tiles outside the scene are solid

It plans with BFS over every scene's collision grid and the trigger graph, nothing hand-tuned. It then tours greedily to the nearest unfinished goal: read every signpost, and cross every scene boundary pair.

Each grid step is driven closed-loop: press until the player starts moving, release, settle, and compare the RAM position with the plan. After every warp it asserts the destination scene and landing tile from RAM. After every A press it asserts that a text box opened (window layer up, `WY < 144`), and that it closed after the expected number of boxes.

## What passes: 148 / 148

- **Static (9).**
  - `validate()` passes.
  - Dialogue is ASCII, ≤ 18 characters per line.
  - The spawn and every exit landing are clear of collision and triggers.
  - All 12 scenes and all 59 signposts are reachable from the spawn.
  - No exit drops the player where no exit can be walked to.
- **Limits (36).** Every scene is ≤ 180 unique tiles (actual 62–98), ≤ 20 actors (max 11) and ≤ 30 triggers (max 25).
- **Play.**
  - The ROM boots into Front Lawn at (92,47).
  - All 20 scene-boundary pairs are crossed, each landing asserted.
  - All 59 signposts are read.
  - All 12 scenes are screenshotted.
  - There were 0 stuck steps over about 3,000 tile steps.

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

- **Reed Lake is only 25 % reachable.** The off-campus neighbourhood north of the lake is walled in by canopy, with no exit, door or sign. The trail is reachable. Other scenes are 87–98 % reachable; the rest is small pockets behind trees. Per-scene figures are in RESULTS.md.
- **Unused exits.** 9 exit triggers can't be reached from either side, which is harmless. 8 exit pairs were dropped for lack of room (REPORT.md, Notes).
- **Lidar canopy is dense.** The Science Quad and the canyon read as forest with paths through them. That matches the data, but it is darker than the real quad.
- **Estimated content.** The carved paths and most doors (estimated, as before) are gameplay choices, not survey data.
- **`tools/gbs` scene lookup (Engine-owned, not changed).** `GBRunner.scene()` reads `<rom>.sym`, but GB Studio's `.sym` lists only 2 of the 12 scene headers, so it returns `bankN:addr` for the rest. `scripts/playtest.py` reads scene addresses from `<rom>.noi` instead. The same fix belongs in `tools/gbs/play.py`.
- **Dialogue wording.** It is cut to whole clauses, but some wraps are awkward (for example "mostly" alone on a line). Several apartment blocks share identical text.
- **Defaults.** The font and dialogue frame are GB Studio's defaults. There is no title screen or music.
- **Web colours.** The web build uses binjgb's palette, not the DMG greens in the screenshots.
