# Reed College in GB Studio: plan

**Goal:** a Game Boy–style, top-down campus walk with a 16×16 player character, in the style of early handheld RPGs. It's built from the world layer in this repo and runs as a real ROM in an emulator, a web build or on Game Boy hardware.

**Why it matters for the talk:** this is the step from *world* to *engine*. The world layer is true to scale and has no limits. A game engine imposes its own rules: 8×8 tiles, four shades of green, a fixed number of tiles per scene and 20 actors per scene. The interesting material for the talk is what those rules force us to change.

> **Style note:** "Pokémon-style" here describes the genre: top-down, a four-direction walk cycle, signposts, doors that lead to interiors. The player character, tiles, font and music must all be original. Use nothing from Nintendo or Game Freak games, which includes not tracing their sprites or tilesets.

---

## 1. three.js or GB Studio?

| | three.js (extending `world/walk/`) | GB Studio 4.x |
|---|---|---|
| **What you get for free** | Our geometry, true scale, the whole campus in one scene | A finished engine: movement, collision, signposts and dialogue, scene changes, menus, music, save files, a ROM and a web export |
| **What you build yourself** | A tile renderer, sprite animation, collision, dialogue boxes, scene changes, input on every device | A converter from our data to GB Studio, plus all the pixel art |
| **Hard limits** | None worth mentioning | Background ≤ 2040 px per side and ≤ 1,048,320 px² · **192 unique 8×8 tiles per background** (384 in Color Only mode) · 4 shades per tile · 20 actors and 30 triggers per scene |
| **Fidelity to the real campus** | Can be 1:1 | Must be compressed and abstracted, split into several scenes, with buildings drawn as stylised icons |
| **Where the effort goes** | Engineering | Pixel art and level design |
| **Engineering effort with AI help** | 1–2 days for a playable tile walk with collision and signposts | 1–2 days for the converter, plus about a day of scripting |
| **Art effort** | Optional: it can be drawn procedurally from our feature classes | Unavoidable, and the critical path (see §5) |
| **What it shows in the talk** | A game *made from* the world | The world *translated into* someone else's engine, and what that translation costs |

**Recommendation: both, in sequence.** First, spend half a day on a quick tile-grid prototype in three.js. Use it to settle the scale, decide where scene boundaries fall, and check that collision and walking feel right before anyone draws a pixel. Then build the GB Studio version as the real artefact.

Showing the two side by side in the talk makes the engine section concrete: the same campus with no limits, then with 192 tiles.

---

## 2. Scale and scenes

The world layer covers about 1,270 m × 900 m.

- **Scale:** 1 metatile (16×16 px, the player's footprint) = **8 m**. One screen (160×144 px) is then 80 m × 72 m, about the length of Eliot Hall. Like most games in this genre, this exaggerates the player's size and squeezes the buildings.
- **Whole campus:** about 159 × 112 metatiles, or 2,544 × 1,792 px. That's over the per-scene size limit, and far over the tile budget for a single scene. So the campus has to be split.

**Seven outdoor scenes**, following the campus's real districts. The scene boxes would go in `world/gb/zones.json`.

| # | Scene | Contains (2010 map numbers) |
|---|---|---|
| 1 | Front Lawn | Woodstock entrance, Eliot Circle (36), the South Lawn |
| 2 | Old Campus | Eliot (35), Old Dorm Block (26), Library (38), Vollum (37), Gray Campus Center (23), Student Union (24), Student Center (34), Anna Mann (25) |
| 3 | Science & Language Houses | Physics, Biology, Chemistry, Psychology (39–45), ETC (43), Greywood (42), the five language houses (46–50), Studio Art (51), Johansen House (52) |
| 4 | West Campus | Performing Arts, Sports Center (19), Kaul (22), Prexy (17), Scholz, Foster and MacNaughton (14–16), Parker House (18) |
| 5 | The Canyon | Reed Lake, the creek, the canyon bridges, Cerf Amphitheatre (21), Physical Plant (20) |
| 6 | Cross Canyon & North Fields | Griffin, McKinley, Woodbridge, Chittick, Bragdon (29–33), Naito and Sullivan (27–28), the sports fields |
| 7 | The Grove & Northwest | Trillium, the Grove houses (9–12), the Reed Apartments, 28 West (3), Health & Counseling (4), Garden, Canyon and Farm Houses (5–8) |

Scenes connect at their real edges through exit triggers.

**Interiors (stretch goal):** Eliot Hall chapel, Library stacks, Commons. Each is a small scene behind a door.

---

## 3. How this builds on the existing work

**Already done and reused directly**

| Existing piece | Its role in GB Studio |
|---|---|
| `reed_world.geojson` | Positions, OSM ids, names and uses for every building |
| `rasterize.py` | Collision maps: building and water cells become solid. It also gives surface classes per cell (path, grass, road, water, woods) for placing tiles automatically. |
| The CSV's use descriptions | Signpost text at each building's front door |
| The OSM ids | The key between the map, the game objects and every later layer |
| The gap report | Tells us which buildings need reference photos before they can be drawn |

**New pieces needed**

1. **A scene exporter (`scripts/gb_export.py`).** For each scene box it rasterises at 8 m per metatile and writes:
   - a placeholder background PNG in the four-green palette (grass, path, road, water, building-block tiles)
   - a collision mask
   - a list of actors and triggers: a signpost at each building door, and exits at scene edges

   It also checks each scene against the tile limit.
2. **Getting data into GB Studio.** Background PNGs go straight into `assets/backgrounds/`. Collisions and actors live in GB Studio's project files, and that format isn't a documented interface. **Spike first:** create one scene in GB Studio 4.x, read the saved project, then decide between writing collisions from a script and painting them by hand over our mask.
3. **A tile kit**, drawn within the 192-tile budget:
   - ground autotiles: grass, path edges, road, water edge, the canyon slope
   - trees and canopy
   - a generic roof and wall kit
   - the 4 to 6 hero buildings, drawn individually: Eliot Hall, Old Dorm Block, the Library, Commons, Performing Arts and Trillium
4. **Door positions.** OSM has 32 `entrance` nodes in our export, and the world build currently drops them. Recover them, then fill the rest from photos or a walk around campus.
5. **Trees and slopes**, built from the lidar:
   - canopy = highest-hit minus bare earth, which tells us where tree tiles go
   - canyon slopes, from the bare-earth DEM, become ledges and stairs, the genre's own way of drawing elevation
6. **A player sprite.** An original 16×16 character with a four-direction walk cycle: front, back and side, two frames each.
7. **Writing.** Signpost text cut down to a few short lines. Optionally, some campus lore and a light objective. An objective turns the walk into a game, which is a separate step in the talk.

---

## 4. References we still need

| Reference | What it's for | Where to get it | Rights |
|---|---|---|---|
| **Aerial imagery** | Lawns versus paved areas, roof colours, tree clusters, paths OSM is missing | **USGS NAIP** (about 0.6 m resolution, public domain), or Portland/Metro orthophotos | NAIP is free to use. Don't trace Google or Apple imagery; their terms forbid it. |
| **Lidar, highest-hit** | Tree canopy and real building heights | DOGAMI Lidar Viewer: the same tile as the bare-earth download | Public |
| **Lidar, bare earth** | Canyon slopes, ledges and stairs | Already downloading | Public |
| **Ground photos of the hero buildings** | Façades, entrances, signature details such as Eliot's Tudor Gothic front and the Old Dorm Block's gables | **Best: photograph them yourself** at the Reed visit. Otherwise Reed's communications office or archives, with permission, or Wikimedia Commons, checking each licence. | Your own photos are cleanest. |
| **Entrance locations** | Door warps and signpost placement | OSM's 32 entrance nodes, plus photos or a site walk | n/a |
| **A current campus map** | Building names since 2010, and whether the Chinese House has moved | Reed's website | Reference only |
| **Floor plans** (interiors only) | Interior layouts | Reed Facilities, with permission, or invent them | Ask |
| **Permission for the Reed name and marks** | A public release | Reed communications | Ask before publishing anything beyond the talk |

---

## 5. Work estimate

These assume one person directing, with AI writing the code. The art is the unknown: it depends on how close to the real buildings the art has to be.

| Phase | Effort |
|---|---|
| Spike: GB Studio project format, one test scene | 0.5–1 day |
| three.js tile prototype: scale, scene cuts, collision feel | 0.5 day |
| `gb_export.py`: backgrounds, collisions, triggers, tile-budget check | 1–2 days |
| Lidar processing: canopy, slopes, heights | 0.5–1 day, once the files are here |
| Collecting references: NAIP, photos, entrances | 1 day, plus a site visit |
| **Tile kit and hero buildings** | **1–3 weeks** for a pixel artist, depending on fidelity |
| Player sprite | 0.5–1 day |
| Building scenes in GB Studio: signposts, exits, music | 2–3 days |
| Testing in an emulator, web build, polish | 1–2 days |

**A rough playable version, with placeholder tiles and all seven scenes walkable, takes about a week.** A version with finished art takes 3 to 5 weeks.

---

## Tracking

Jira project **RW** (Reed-World), at falsework.atlassian.net.
- **RW-1 · GB Studio campus walk** (epic): RW-3 to RW-15
- **RW-2 · World layer: high fidelity** (epic): RW-16 (bare-earth DEM), RW-17 (canopy and heights)

Blocking links:
- RW-4 zones → RW-6 exporter
- RW-3 format spike → RW-13 scene assembly
- RW-9 photos → RW-11 hero buildings
- RW-16 DEM → RW-10 tile kit (ledges and slopes)

---

## 6. Prompt for the next session

Paste this into a new session that has the repo attached:

> We're continuing **aleph-falsework/reed-world**. Read `README.md`, `docs/PROCESS.md` and `docs/GB_STUDIO_PLAN.md` first.
>
> **Task: the GB Studio export pipeline, phase 1. No final art yet.**
> 1. Add `world/gb/zones.json` with the seven scene boxes from §2 of the plan, in world metres. Draw the boxes over `reed_world_lowfi.svg` and show me before going further.
> 2. Write `scripts/gb_export.py`. For each zone, reuse the rasterizer at **8 m per 16×16 metatile** and write to `world/gb/<zone>/`:
>    - `background.png`: placeholder tiles in GB Studio's four-colour palette (#071821, #306850, #86c06c, #e0f8cf), one simple tile per class (grass, path, road, water, woods, building)
>    - `collision.png`, plus `collision.json` with one value per 8×8 cell: solid for buildings, water and dense woods
>    - `actors.json`: a signpost at each named campus building, keyed by OSM id, with its CSV use text cut to 3 short lines
>    - `triggers.json`: exits where the zone meets its neighbours
>    - a report of each scene's unique 8×8 tiles against the 192-tile limit, its pixel size against the 2040 px and 1,048,320 px² limits, and its actor and trigger counts against 20 and 30
> 3. Keep the OSM entrance nodes (`entrance=*`) in `build_world.py` instead of dropping them, and put signposts at those doors where they exist.
> 4. Don't write GB Studio project files yet. List what you'd need to know about the GB Studio 4 project format to import collisions automatically.
> 5. Document new data gaps in the gap report, and commit.
>
> **Rules:**
> - Keep OSM ids on everything.
> - Don't invent buildings, doors or names.
> - The player character, tiles, font and music must all be original. Nothing from Nintendo or Game Freak games.

---

## Sources
- GB Studio docs: [Backgrounds](https://gbstudio.dev/docs/assets/backgrounds) (size, 4-colour palette and unique-tile limits) and [Scene limits](https://gbstudio.dev/docs/project-editor/scenes/limits) (20 actors, 30 triggers, tile memory by colour mode)
- [GB Studio 4.3.1 release notes](https://itch.io/devlog/1550737/gb-studio-431-now-available.amp)
- [GB Studio on itch.io](https://chrismaltby.itch.io/gb-studio)
