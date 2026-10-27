# GB Studio 4.3.2: project format, headless build, sprite sheets

Written by the Engine agent, overnight 26–27 Oct 2026. Everything here was read from the **v4.3.2 tag** of `chrismaltby/gb-studio` (commit `ccb891b`) and checked by building and playing ROMs.

## Versions used

| Thing | Version |
|---|---|
| GB Studio source | tag `v4.3.2` (`package.json` 4.3.2, CLI `--version` prints 4.3.2) |
| Engine (gbvm submodule) | `bd6f41cc`, `engine.json` version `4.3.0-e1` |
| GBDK-2020 | `gbdk-linux64.tar.gz` sha256 `d7857a5f…605c`, which is exactly the hash pinned in 4.3.2's `buildTools/dependencies.lock` (`lcc` dated 2025-12-28); `tools_version` 4.2.3-v3 |
| Yarn / Node | Yarn 4.4.1 via corepack (from `packageManager`), `--immutable` against the shipped lockfile; Node 22.23 |
| PyBoy | 2.8.1 (numpy 2.5.3, Pillow 12.3) |
| Project file version | `_version "4.2.0"`, `_release "10"` (4.3.2's last migration is 4.2.0 r9→r10, so 4.3.2 writes and expects this) |

## Python API (`tools/gbs`)

```python
from tools.gbs import make_project, build_rom, build_web, play, validate, check_project

validate(spec: dict, base_dir=None) -> dict
    # {"ok": bool, "errors": [str], "warnings": [str],
    #  "scenes": {id: {"unique_tiles", "unique_tiles_with_flips", "sprite_tiles",
    #                  "sprite_tile_budget", "actors", "triggers", ...}}}
make_project(spec: dict, out_dir, base_dir=None, seed=None, strict=True) -> Path  # the .gbsproj
check_project(project_dir) -> dict          # GB Studio's own loader + strict schema check
build_rom(project_dir, out_rom, verbose=False) -> Path   # also writes <rom>.sym/.map/.noi/.build.log
build_web(project_dir, out_dir, verbose=False) -> Path   # index.html
play(rom, inputs, out_dir, boot_frames=None, scale=1) -> list[str]   # screenshot paths
```

* `base_dir` is where relative paths in the spec resolve. The default is the repo root.
* `seed` set gives deterministic uuid5 ids, so diffs stay stable. Unset gives fresh uuid4s.
* `strict=True` raises `SpecError` if `validate` finds errors.
* `play` input steps: `["wait", N]`; `["up"|"down"|"left"|"right"|"a"|"b"|"start"|"select", N]` holds the button N frames, then releases it for 1 frame; `["screenshot", "name"]`. With `boot_frames=None`, `play` runs until the engine reports its first scene loaded (read via `<rom>.sym`), then waits 40 more frames. PyBoy's own boot logo lasts ~150 frames. `<out_dir>/play_log.json` records, for each screenshot, the frame, the scene symbol and the player's tile x/y and facing.
* `GBRunner(rom)` does the same thing step by step, with `.state()` for asserts.
* CLI: `python3 -m tools.gbs {validate|make|rom|web|check|play|all} ...` (see `tools/gbs/__main__.py`).

## Scene spec: additions to the contract (all optional, nothing removed)

* `spec["sprites"] = {name: png}`. An actor's `"sprite": "signpost"` resolves through this map. Failing that, a value ending `.png` is used as a path, and otherwise it falls back to `art/sprites/<name>.png`. The player's `sprite` resolves the same way.
* `spec["settings"] = {"topdown_grid": 8|16, "lines_per_box": 3, "author": ""}`.
* `collisions` may be a list of int rows or a list of strings (`"0011"`). `0` becomes `0x00` and `1` becomes `0x0F` (solid on all sides). Values ≥2 pass through as raw GB Studio collision bytes.
* `actor["direction"]` sets the facing (default `down`). `actor["estimated"]: true` is noted on the actor.
* `trigger["direction"]` may be `""` to keep the player's facing.
* **Positions:** a 16×16 actor or player at tile `(x, y)` occupies columns `x..x+1` and rows `y-1..y`. So `(x, y)` is the **bottom-left 8×8 tile** of the sprite. The player's collision box is the bottom row only (16×8). `x` must be ≤ width−2. Verified in RAM: start `(4,10)` reads back as px (32,80).
* Dialogue: each group of `lines_per_box` lines (default 3) becomes one text box. Keep lines to **18 characters** or fewer and **ASCII** only (the default font). Curly quotes and dashes are converted. One A press closes a box; another A while still facing the sign opens it again.

## Project layout (what `make_project` writes)

```
<title>.gbsproj                     {"_resourceType":"project","name","author","notes","_version":"4.2.0","_release":"10"}
project/settings.gbsres             startSceneId/startX/startY/startDirection, defaultPlayerSprites{TOPDOWN,...}, colorMode "mono", spriteMode "8x16"
project/engine_field_values.gbsres  topdown_grid only when ≠ 8
project/variables.gbsres            {"variables":[],"constants":[]}
project/palettes/*.gbsres           GB Studio's 8 default palettes (unused in mono)
project/scenes/<folder>/scene.gbsres
project/scenes/<folder>/actors/<file>.gbsres
project/scenes/<folder>/triggers/<file>.gbsres
assets/backgrounds/<id>.png + <id>.png.gbsres
assets/sprites/<name>.png + <name>.png.gbsres
assets/fonts/gbs-mono.{png,json,png.gbsres}   GB Studio's default font
assets/ui/frame.png, cursor.png                GB Studio's default dialogue frame
validation.json                                the validate() report (GB Studio ignores it)
```

How GB Studio loads this (`src/lib/project/loadProjectResources.ts`): it globs `{project,assets,plugins}/**/*.gbsres`, routes each file by `_resourceType`, and attaches actors and triggers to the scene whose folder contains them. Each file is passed through TypeBox `Value.Cast`, which **silently repairs** missing or invalid fields. That is why `check_project` runs `Value.Errors` instead. The folder and file names are derived from `name` (`entityToFilePath`: lowercase, spaces become `_`, `/` makes subfolders). GB Studio would rename files to match on its next save. We write the same names it would.

### scene.gbsres
`_resourceType "scene"`, `_index`, `id`, `type "TOPDOWN"`, `name`, `symbol` (unique C symbol, ≤27 chars), `notes`, `x`,`y` (world-editor px), `width`,`height` (tiles), `backgroundId`, `tilesetId ""`, `colorModeOverride "none"`, `paletteIds []`, `spritePaletteIds []`, `autoFadeSpeed 1`, `script`, `playerHit1..3Script` (all `[]`), and `collisions`.

`collisions` is a row-major byte array, RLE-compressed by `compress8bitNumberArray`. Each run is a 2-hex-digit value followed by `!` (one copy) or `<hex count>+`. Example: `"0f14+00!"` is twenty 0x0F then one 0x00. Collision bits: TOP 1, BOTTOM 2, LEFT 4, RIGHT 8, ALL `0x0F`.

### actor .gbsres
`_resourceType "actor"`, `_index`, `id`, `symbol`, `prefabId ""`, `name`, `notes` (we put the OSM id here), `coordinateType "tiles"`, `x`,`y`, `frame 0`, `animate false`, `spriteSheetId`, `paletteId ""`, `direction`, `moveSpeed 1`, `animSpeed 15`, `isPinned false`, `persistent false`, `collisionGroup ""`, `collisionExtraFlags []`, `prefabScriptOverrides {}`, `script` (On Interact), `startScript`, `updateScript`, `hit1..3Script`.

The signpost's script is `{"command":"EVENT_TEXT","args":{"text":["page 1","page 2"],"avatarId":"","minHeight":4,"maxHeight":7},"id":…}`. `text` is an array: one string per box, lines separated by `\n`.

### trigger .gbsres
`_resourceType "trigger"`, `_index`, `id`, `symbol`, `prefabId ""`, `name`, `notes`, `x`,`y`,`width`,`height`, `prefabScriptOverrides {}`, `leaveScript []`, and `script`. The script is `{"command":"EVENT_SWITCH_SCENE","args":{"sceneId":…,"x":{"type":"number","value":10},"y":{"type":"number","value":70},"direction":"up","fadeSpeed":"2"}}`.

### background .png.gbsres
`{"_resourceType":"background","id","name","symbol","tileColors":"","filename","width","height" (tiles),"imageWidth","imageHeight" (px),"autoColor":false}`.

### sprite .png.gbsres
`id`, `name`, `symbol`, `states[{id,name:"",animationType,flipLeft,animations[8]{id,frames[{id,tiles[{id,x,y,sliceX,sliceY,flipX,flipY,palette,paletteIndex,objPalette:"OBP0",priority}]}]}]}`. Also `numTiles`, `canvasOriginX/Y 0`, `canvasWidth/Height 16`, `boundsX/Y/Width/Height`, `animSpeed`, `filename`, `width`, `height`, and `checksum` (sha1 of the PNG).

**The CLI does not auto-detect sprite frames. Only the editor does.** A sprite without `states` and `numTiles` builds with no tiles and shows up in `modifiedSpriteIds`, which means the editor would rewrite it on open. `make_project` writes the frames out in full.

Animation slots, in order: 0 idle right, 1 idle left, 2 idle up, 3 idle down, 4 move right, 5 move left, 6 move up, 7 move down. In 8×16 sprite mode, each 16×16 frame is two tiles: `{x:0, sliceX:f*16}` and `{x:8, sliceX:f*16+8}`.

### settings.gbsres
This is the blank-template settings with these fields changed: `startSceneId`, `startX`, `startY`, `startDirection`, `defaultPlayerSprites` (every scene type points at the player sprite), `colorMode "mono"`, `sgbEnabled false`, `spriteMode "8x16"`, `defaultSceneTypeId "TOPDOWN"`, `defaultMonoBGP [0,1,2,3]`, `defaultMonoOBP0 [0,1,3]`, `defaultMonoOBP1 [0,2,3]`, and `customColorsWhite/Light/Dark/Black = E0F8CF/86C06C/306850/071821`. 21 UI-only keys (zoom, debugger…) are missing on purpose; GB Studio's own template omits them too, and they are filled by defaults or user_settings.

## Sprite sheets (what the Art agent must deliver)

* One row of **16×16 frames**, PNG height exactly **16 px**.
* **Player: 96×16**, frames left to right: `down0, down1, up0, up1, right0, right1`. Left is drawn by flipping right. Frame 0 of each direction is the idle pose; walking alternates `1, 0`. This is GB Studio's own `player.png` / `actor_animated.png` layout.
* If the left side must not be a mirror (for example, a backpack worn on one shoulder), use **128×16**, with `left0, left1` appended after `right1`.
* **Signpost / static prop: 16×16**, one frame.
* Also accepted: 48×16 (`down, up, right`, no walk) and 64×16 (`down, up, right, left`).
* **Colours:** transparent (alpha < 200), `#e0f8cf`, `#86c06c`, `#071821`. Sprites get **3 colours**: GB Studio maps by green channel, and `#306850` lands in the same index as `#071821`, so it renders black. `validate` warns about this.
* The player's collision box is the bottom 16×8 (head can overlap walls). Props use the full 16×16.

## Backgrounds

* PNG width and height must be multiples of 8, at most 2040 px per side and at most 1,048,320 px² (`compileImages.ts`), and must equal the scene's `width*8 × height*8`.
* Colours: exactly `#e0f8cf #86c06c #306850 #071821`. GB Studio actually indexes by the green channel (<65, <130, <205, else), but `validate` requires the exact four.
* **≤192 unique 8×8 tiles** per background (`MAX_BACKGROUND_TILES`). Identical tiles are deduped. **Flipped tiles are NOT deduped in Monochrome**: flip dedupe (`autoTileFlipEnabled`) applies only when `colorMode` is `"color"`, because DMG cannot flip BG tiles. I verified this: a test image with 160 exact / 40 flip-canonical tiles gave `n_tiles = 160` in GB Studio's exported `bg_*_tileset.c`, the same as `count_unique_tiles`.
* Sprite VRAM budget: with 192 BG tiles, sprites get 64 8×16 tiles; with 128 or fewer, 96. `validate` reports this per scene.
* Actors: 20 per scene, but only **10 if the scene is ≤ 20×18 tiles**; no more than 10 near each other on screen. Triggers: 30.

## Headless build

`tools/gbs/setup_gbstudio.sh` reproduces everything (~3 min). The steps:

1. `git clone --depth 1 --branch v4.3.2 https://github.com/chrismaltby/gb-studio /home/claude/gbs-src`, then `git submodule update --init` (pulls in gbvm).
2. GBDK: `fetchDependencies.ts` downloads `gbdk-2020/releases/latest/download/gbdk-linux64.tar.gz` and checks it against `buildTools/dependencies.lock`. The script does the same with curl and `sha256sum -c`, and extracts into `buildTools/linux-x64/` (giving `buildTools/linux-x64/gbdk/bin/lcc`).
3. `COREPACK_NPM_REGISTRY=https://registry.npmjs.org ELECTRON_SKIP_BINARY_DOWNLOAD=1 corepack yarn install --immutable`.
4. `printf electron-not-installed > node_modules/electron/path.txt`. The CLI bundle does `require("electron")`; in plain Node that returns a path string, `electron.app` is undefined, and the CLI code guards for that.
5. `NO_TYPE_CHECKING=1 node_modules/.bin/webpack --config ./src/apps/gb-studio-cli/webpack.cli.config.js` (this is the `make:cli` script) produces `out/cli/gb-studio-cli.js`.
6. `tools/gbs/node/build_checker.sh` produces `out/cli/gbs-check.js`.

The CLI commands are `node out/cli/gb-studio-cli.js {make:rom|make:web|make:pocket|export} [-v] <project.gbsproj> <dest>`.

Timings: smoke ROM (2 scenes) **~22 s**; web export ~20 s. A 12-scene stress project (84×74 tiles, 20 signs and 10 exits per scene) built in 30 s to a 512 KB MBC5 ROM and played correctly.

The web export (`make:web`) writes `index.html`, `js/binjgb.{js,wasm}`, `rom/<name>.gb` and `gbstudio.json`. Its ROM differs from `make:rom` by 5 bytes (header and build-type bytes) and plays identically in PyBoy.

## Gotchas

* **Shared temp dir.** The CLI always builds in `os.tmpdir()/_gbsbuild`, so parallel builds clobber each other. `build_rom` and `build_web` set a private `TMPDIR` per call.
* **`yarn make:cli` fails** with "command not found: webpack" under Yarn 4, because webpack is not a direct dependency. Call `node_modules/.bin/webpack` instead. **Yarn 1 cannot read the lockfile** (it is Berry v8) and silently re-resolves every version, so use corepack Yarn 4.4.1 with `--immutable`. `repo.yarnpkg.com` is blocked here; `COREPACK_NPM_REGISTRY` fixes that.
* `src/consts.ts` finds `appData/` and `buildTools/` by looking for `out/cli` in `__dirname`, so any extra bundle must be written to `out/cli/`.
* **Mono colours are the player's palette, not the ROM's.** In Monochrome mode the ROM holds only shades 0–3. PyBoy screenshots use the DMG palette above (`color_palette`). The web export uses binjgb's built-in palette 83 and lets players switch palettes. `customColors*` only matter if the project is switched to Color mode.
* Text boxes: a second A while facing the sign re-opens it. Scripts must press A once to close, then move.
* Font and dialogue frame are GB Studio's defaults (MIT, shipped in every new project). Art can replace `assets/ui/frame.png`, `assets/ui/cursor.png`, or the font after `make_project` runs. They are not original Falsework art.
* No logo or title scene is generated. The ROM boots straight into the player's start scene (the first scene is live ~120 frames after power-on in PyBoy, including its boot ROM).
* Opening in GB Studio 4.3.2: `check_project` confirms no migration prompt (`isMigrated: false`) and that no sprite needs re-detection. Exercising the GUI itself was **not** possible headless.

## Smoke test

`python3 tests/gbs_smoke/run_smoke.py --web` makes placeholder art, writes `tests/gbs_smoke/spec.json`, validates it, runs `make_project` and `check_project`, builds the ROM and web export, and plays the ROM. It then asserts on RAM state.

Screenshots are in `tests/gbs_smoke/screens/`: `a_scene1_renders`, `b_wall_blocks_player` (x stops at 10, the wall is column 12), `c_signpost_dialogue`, `c2_dialogue_closed`, `d_scene2_loaded`, plus `contact_sheet.png`.

`python3 tests/gbs_smoke/test_validate.py` runs 11 negative cases for `validate`.
