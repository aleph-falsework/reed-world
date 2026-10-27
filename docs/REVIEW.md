# Reed World: independent review

Reviewer: a fresh-eyes agent that did not build any of this. Reviewed on 27 Oct 2026, branch `gb-overnight` at `b314763`. I made no changes to code or assets. Everything I wrote is in `build/review/` and in this file.

## Verdict

**Ship it for the talk, but fix two process issues first.** The ROM boots and plays. Collisions work against buildings, trees and water. Every limit holds, and I counted them myself. All 59 signposts carry real OSM ids and CSV-sourced text. Building footprints land where the data puts them. The project loads cleanly in GB Studio 4.3.2's own loader, and the committed ROM is what the committed project builds.

The "148 / 148" headline is honest but generous. About 24 of those checks cannot meaningfully fail, and none of them tests that a wall blocks the player. My own session covers that gap.

| Severity | Count |
|---|---|
| Blocker | 0 |
| Major | 2 |
| Minor | 6 |
| Nit | 4 |

## Evidence by area

### 1. The ROM boots and plays

I wrote my own session, `build/review/review_play.py`. It does not reuse `scripts/playtest.py` routes. It builds its own model from `game/reed-world/` (it decodes the RLE collisions in `scene.gbsres`, the actor files and the trigger files), not from `world/gb/project_spec.json`. Results are in `build/review/review_play.json`: **35/35 pass**.

- **Boot.** It boots into `front_lawn` at (92,47), checked in RAM (`boot.png`).
- **Scenes.** It visited 5 scenes: front_lawn, old_campus, science_quad, reed_lake and cross_canyon (`scene_*.png`).
- **Walls.** It pushed into 8 obstacles, 3 times each. In every case RAM showed no movement and the player facing the obstacle. A step the opposite way then moved the player, as a positive control. The obstacles were:
  - trees and the library façade in front_lawn
  - water and Eliot Hall's roof in old_campus
  - water and trees in science_quad
  - water in reed_lake
  - Bragdon's roof in cross_canyon

  Screenshots are `wall_*.png`. In `wall_front_lawn_building.png` the player stands flush against the Hauser Library façade.
- **Signposts.** It read 3 signposts: Hauser Library, Eliot Hall and Chemistry. Each text box opened and closed. The rendered text matches the CSV, as shown in `sign_*.png`.
- **Contact sheet.** `sheet_review.png` collects all of the above.

I also checked collision against the exporter's surface classes across all 12 scenes, from the project files. Every ROOF, FACADE, TREE, FOREST, LONETREE, WATER and STEEP tile is solid. Every LAWN, PATH, ROAD, PAVED, BRIDGE and DOOR tile is open. There were zero exceptions.

**Do I agree with 148/148?** Yes, nothing it asserts is false. But it asserts less than the count suggests (see Major 1).

### 2. GB Studio limits (my own count: `build/review/count_limits.py`)

I counted the PNGs in `game/reed-world/assets/backgrounds/`, not the spec copies.

- **Unique 8×8 tiles.** Exact-match counts per scene are 62–98, which matches REPORT.md for all 12 scenes.
  - Flip-canonical counts are 0–3 lower. 4.3.2 does not dedupe flipped tiles in Monochrome (`docs/gbstudio-4.3.2-format.md`), so the exact count is the binding one.
  - Everything is far under 192.
- **Actors and triggers.** Every scene is ≤ 11 actors (northwest) and ≤ 25 triggers (the_grove).
  - No scene is a single screen (the smallest is 54×52 tiles), so the 10-actor rule never applies.
  - At most 5 actors fall in any one 20×18 window.
- **Background palettes.** All 12 backgrounds are RGB with exactly the 4 DMG colours. Sizes are multiples of 8 and match each scene's `width × height`.
- **Sprite palettes.** `player.png`, `signpost.png` and `art/sprites/*.png` use only `#071821 #86c06c #e0f8cf` plus full transparency, with no semi-alpha. That follows the doc's 3-colour sprite rule.
- **GB Studio's own files.** `frame.png` is GB Studio's default and uses all 4 shades, which is fine on the BG/window layer.

### 3. Fidelity to the data (`build/review/check_fidelity.py`, `check_actors.py`)

- **OSM ids.** All 59 actor ids (58 ways and 1 node) exist in `world/lowfi/reed_world.geojson`, and the names match. In the GB Studio project the id sits in each actor's `notes` and `symbol`.
- **Footprints.** For every signposted polygon, the metatiles whose centre falls inside the OSM footprint are drawn as ROOF, FACADE or DOOR:
  - 100 % of them in 48 of 56 buildings, and ≥ 87 % in the other 8
  - Eliot Circle (a lawn) and The Horrid Art Project (a sub-metatile sliver, reported in REPORT.md) are correctly not drawn as buildings
  - Signs stand 0–16 m from their building
- **Six buildings checked against `world/lidar/surface_preview.png`** (overlay: `overlay_old_campus.png`):
  - In Old Campus, Old Dorm Block is SW of Eliot Hall, Eliot Circle and Vollum are east, Gray Campus Center is far west, and the lake with its footbridge is north. All of this matches the lidar.
  - Science Quad (Chemistry, Physics, Biology, Psychology) and West Campus (Foster, MacNaughton, Scholz) also match (`overlay_science_quad.png`, `overlay_west_campus.png`).
- **Sign text.** Every description is a clause-cut excerpt of the CSV's "What happens there" column. No facts were added.
  - Buildings missing from the CSV show their name only: Gyms I and II, Reed Research Reactor, Childcare Center, Greenwood Hall and The Horrid Art Project.
  - The cuts change the meaning in three places (Minor 3).
- **Invented content.** Doors (48 estimated), the 7 carved canopy paths, the scene names, the sign positions and the exit landing spots are gameplay inventions. All of them are listed in REPORT.md. The flag does not reach the GB Studio project (Minor 1).

### 4. Originality

`art/preview.png`, `metatiles_sheet.png` and the sprites (`build/review/sprite_*_x8.png`) are in the generic top-down GB RPG idiom: gabled roofs, scalloped canopy and dotted water. **No tile or character is a recognisable copy of Pokémon or other Nintendo or Game Freak art.**

The player is an original chibi student in a pointed cap with a backpack, not Red, Gold or Ethan. `art/make_art.py` draws everything from code.

**Disclosed:** the font (`gbs-mono`), the dialogue frame and the cursor are GB Studio's defaults. BUILD_NOTES and the format doc say so. Credit GB Studio (MIT) in the talk.

### 5. Will it open in GB Studio 4.3.2?

These checks passed:

- **Loader.** `python3 -m tools.gbs check game/reed-world` runs GB Studio's own loader:
  - `ok: true`, file version `4.2.0 r10`, `isMigrated: false`
  - no modified sprites, `problems: []`
  - 12 scenes, 59 actors, 176 triggers
- **No absolute paths** anywhere under `game/`.
- **References.** Every `filename` resolves. Every `backgroundId`, `spriteSheetId`, `sceneId` and `startSceneId` resolves.
- **Sprite checksums** (sha1) match.
- **Symbols.** All 262 symbols are unique. The longest is 30 characters, which is GB Studio's own 27 + `_NN` scheme.
- **Scene layout.** Scenes don't overlap in the world editor.
- **ROM matches the project.** Rebuilding from a copy of `game/reed-world` gives a ROM that differs from `build/reed_world.gb` in 5 bytes only: the header checksum and a 4-byte build stamp.

Residual risk: the GUI itself was never opened, as BUILD_NOTES also says.

### 6. Repo hygiene

- **Secrets.** A grep for keys, tokens and private keys found none.
- **File size.** The largest tracked file is the 4.3 MB Overpass export. The `.git` folder is 25 MB.
- **Absolute paths and generated files.** Some are committed (Minor 6, Nit 2).

## Findings

### Major

**M1. The playtest never asserts that collision works, and about 24 of its 148 checks cannot really fail.**
- **No wall tests.** `scripts/playtest.py` only walks BFS paths over tiles the spec says are free. It never pushes into a solid tile, so a ROM with *no* collisions would still score 148/148.
- **Checks that cannot fail:**
  - 2 are hard-coded `True` (the "info" rows at lines 348 and 374).
  - 10 "at least one signpost read" rows (line 472) are implied by the 59 per-sign rows.
  - 12 "screenshot" rows (line 476) only check that a PNG was written.
- **Sign checks.** They assert that a text box opened and closed with the right box count, not *which* text appeared.
- **Fix:**
  - Fold my wall-push and step-back test (`build/review/review_play.py`, `wall_test`) into playtest.py, covering every scene and every solid class.
  - Report "info" rows separately from the pass count.
  - Optionally compare the window tile map against the expected first line.

**M2. The default rebuild overwrites hand edits in `art/` and `game/reed-world/`.**
- **Repro:** `python3 scripts/build_game.py` with no flags:
  - runs `art/make_art.py` (lines 68–70), which regenerates every PNG in `art/`
  - then runs `shutil.rmtree(game/reed-world)` (line 98)
- **Why it matters.** The locked plan puts an Aseprite art pass in the morning, and the obvious next step after that is to rebuild. That would silently throw away both the Aseprite work and any edits made in GB Studio. BUILD_NOTES warns about `game/` but not about `art/`.
- **Fix:**
  - Make `--skip-art` the default and require `--regen-art`.
  - Or refuse to overwrite files whose mtime or hash changed since the last generation.
  - Write the project to `build/` and treat `game/` as hand-owned.

### Minor

1. **The estimated flag is lost in the GB Studio project.**
   - The spec marks 48 actors `door_estimated: true`.
   - `tools/gbs/project.py:277` looks for `a.get("estimated")`, so every actor's notes read just `id: way/…`.
   - **Fix:** honour `door_estimated` (and `on_facade`) in `notes`.
2. **Adjacent exit spans with different landing tiles.** There are 36 such pairs (for example `front_lawn` `exit_n_old_campus_5` and `_6`).
   - **Repro:** from front_lawn (65,1), walk up. The 16 px box straddles both triggers, the lower `_index` wins, and you land in old_campus at (62,41), not (68,41).
   - Crossing a boundary can therefore snap the player sideways by up to about 3 tiles.
   - playtest.py's model is right only because spec order equals the engine's `_index` order. That assumption isn't documented, and my first independent model got it wrong.
   - **Fix:** map the landing x proportionally across the span, or use one trigger per span with landing x = entry x plus an offset. At least document the ordering.
3. **Some sign text cuts change the meaning:**
   - **Old Dorm Block** "…divided into eight halls, Abington, Doyle." names 2 of the 8 halls.
   - **Dorothy Johansen House** "Former home of alumna." drops who she was and what the house is now (academic support).
   - **Prexy Hall** "Music building on this map…" refers to the printed 2010 map, and the CSV says music moved out in 2013.
   - **Fix:** end on a full clause, or hand-edit these three.
4. **Two scenes are thin.**
   - Reed Lake is 25 % reachable, and neither Reed Lake nor Reed Canyon East has a signpost.
   - This is disclosed, but these scenes play as empty.
   - **Fix:** add a canyon or lake sign (Reed Canyon is in OSM), or trim the walled-off neighbourhood from the scene.
5. **The README doesn't mention the game.**
   - Its Roadmap still shows "Game" unchecked, and nothing points to `build/reed_world.gb`, `build/BUILD_NOTES.md` or the web build.
   - **Fix:** add a "Game Boy build" section.
6. **Absolute `/home/claude` paths.**
   - They appear in `tools/gbs/constants.py:13` (`DEFAULT_GBS_SRC`), `setup_gbstudio.sh`, `node/build_checker.sh`, `build/BUILD_NOTES.md:34` and `docs/gbstudio-4.3.2-format.md:117`.
   - Generated files carry them too: `tests/gbs_smoke/smoke_result.json`, `tests/gbs_smoke/screens/play_log.json`, and the `/tmp/gbsbuild_*` paths in `build/reed_world.map`.
   - These break on Matthew's Mac.
   - **Fix:** default to a path relative to the repo or `~/gbs-src`. Document `$GBS_SRC` (already supported in `build.py:31`) in BUILD_NOTES. Make logged paths repo-relative.

### Nit

1. `build/BUILD_NOTES.md:3` says "(not committed)", but the notes are committed in `b314763`.
2. Generated output is in git: the whole `tests/gbs_smoke/out/` project, `build/reed_world.build.log`, `.map` and `.noi`. Consider ignoring these or regenerating them.
3. The web build uses binjgb's palette, not the DMG greens (disclosed). Consider setting the web template's default palette.
4. The player's up-facing frames read a little like a dark face with two eyes. This is for the Aseprite pass.

## What you can trust

- The ROM boots, walks, warps between scenes and shows dialogue, checked in RAM by two independent harnesses.
- Buildings, trees and water block the player. Lawns, paths, roads and bridges don't.
- My tile, actor and trigger counts agree with REPORT.md exactly, and every scene is far under GB Studio's limits.
- The palettes are correct on every background and sprite.
- All OSM ids are real. Every building is drawn on its true footprint in the right scene, with neighbours in the right relative positions.
- Sign text comes only from the CSV. Nothing was invented that REPORT.md doesn't list as estimated.
- The art is original. The default font and frame are GB Studio's, and that is disclosed.
- GB Studio 4.3.2's loader accepts the project with no migration, and the committed ROM is reproducible from it.
- No secrets are committed.

## Files in `build/review/`

- `review_play.py` and `review_play.json`: my PyBoy session, with 35 checks.
- `count_limits.py`, `check_actors.py` and `check_fidelity.py`: the independent checks.
- Screenshots: `boot.png`, `scene_*.png`, `wall_*.png` and `sign_*.png`, collected in `sheet_review.png`.
- Data overlays: `overlay_{old_campus,science_quad,west_campus,the_grove}.png`, which draw the OSM outlines in red on each scene background.
- Sprites at ×8: `sprite_*_x8.png`.
