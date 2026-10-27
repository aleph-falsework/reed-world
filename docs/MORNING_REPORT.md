# Morning report: the overnight run

**The result: a playable Game Boy ROM of Reed College**, built and tested overnight. It has 12 scenes, 61 signposts and an original player character, all on top of OSM and DOGAMI lidar data. It's on branch `gb-overnight`. Nothing is merged yet; the PR is for you to review.

## Try it
- **On a Game Boy emulator:** `build/reed_world.gb`. Any emulator works, e.g. SameBoy or BGB, or an Analogue Pocket or flash cart.
- **In GB Studio 4.3.2:** open `game/reed-world/reed_world.gbsproj`, then press Play. GB Studio's own loader accepts the file with no migration. The app's window itself was never opened, because it can't run in the cloud. **This is the first thing to check this morning.**
- **In a browser:** serve `build/web/` with `python3 -m http.server -d build/web` and open http://localhost:8000.
- **Controls:** arrows or WASD to walk, Z or Alt for A (read a signpost).

## Timeline (Pacific time, 26–27 Oct)
| Time | Commit / event |
|---|---|
| 23:45 | Decisions locked: 6 m per tile, 4-green DMG, a student with a backpack, a branch for overnight commits. The lidar crop was made on your computer. |
| 23:47 | Runbook committed. **Wave 1:** the Engine, Data and Art agents start in parallel. |
| 23:54 | **Data** done: surface map, canopy, heights, entrances. The Exporter starts early. |
| 00:06 | **Art** done: 128-tile kit, player and signpost. |
| 00:13 | **Engine** done: GB Studio 4.3.2 builds headlessly, and the smoke ROM proves walls, signs and exits in PyBoy. **Exporter** done: 12 scenes. |
| 00:34 | **Integration** done: full project, ROM and web build. Playtest 148/148. |
| 02:20 | **Independent reviewer** done: 0 blockers, 2 major, 6 minor and 4 nit issues, plus its own 35-check session. The cloud workspace restarted around here; nothing was lost, because every stage was already pushed. |
| 02:40 | **Fixer** done: every finding addressed. The playtest is now **248 pass, 0 fail**, with wall tests in every scene. |
| 03:55 | This report, the Jira updates and the PR. |

## What's verified
- **The ROM plays.** It boots on the Front Lawn. The scripted playtest crosses every scene boundary, reads all 61 signposts and pushes into 61 walls (buildings, trees, water, slopes), with each step asserted from the emulator's RAM, not just screenshots. A second, independent harness by the reviewer agrees.
- **Limits:** 62–98 unique tiles per scene (limit 192), at most 11 actors (limit 20), at most 28 triggers (limit 30).
- **Fidelity:** every signpost carries a real OSM id. 48 of 56 buildings are drawn on 100% of their footprint, and the rest on at least 87%. Sign text comes only from your CSV.
- **Originality:** all tiles and sprites are drawn from code, and the reviewer found nothing derived from Nintendo or Game Freak art. The dialogue font and frame are GB Studio's defaults (MIT licence): **credit GB Studio in the talk.**

## What's estimated or still rough
- **Doors:** 48 of 61 doors are estimated, because OSM only has entrances for 9 buildings. Your photos at the Reed visit (RW-9) fix this.
- **Exits:** 26 of 790 crossing points still shift the player 2–6 tiles sideways.
- **Reed Lake** is only about 25% reachable; the lakeside neighbourhood is walled in by trees.
- **Hero buildings** are generic Tudor or flat blocks, not drawn individually. That's RW-11, your Aseprite pass.
- **The player's up-facing frames** read a bit like a face. That's also for Aseprite.
- **Lidar:** it's from 2014. Trillium (2019) comes from OSM only.

## Your morning checklist
1. Open `game/reed-world/` in GB Studio 4.3.2 and press **Play** (5 min).
2. Skim `build/playtest/contact_sheet.png` and `contact_sheet_signs.png`.
3. Review and merge the PR `gb-overnight → main`.
4. **Aseprite pass:** edit `art/tiles/kit.png` and `art/sprites/*.png`, then rebuild with `python3 scripts/build_game.py`. Your edits are safe: art is only regenerated with `--regen-art`.

## Where things are
- `docs/OVERNIGHT.md`: the runbook and agent roles.
- `docs/REVIEW.md`: the independent review.
- `build/BUILD_NOTES.md`: rebuild commands and how the playtest works.
- `world/gb/REPORT.md`: every scene, with its estimated doors and exits.
- `docs/gbstudio-4.3.2-format.md`: the GB Studio project format, as reverse-engineered from the source.

## Jira
- **In Review:** RW-3, RW-4, RW-6, RW-7, RW-10, RW-12, RW-13, RW-14, RW-16, RW-17.
- **Still open:** RW-5 (three.js tile prototype, skipped in favour of the real ROM), RW-8 (NAIP, optional now), RW-9 (photos), RW-11 (hero buildings) and RW-15 (Reed permission).
