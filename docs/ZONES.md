# Zones and scene export

How `scripts/gb_export.py` turns the world layer into GB Studio scenes. It writes everything in `world/gb/`. Per-scene numbers are in `world/gb/REPORT.md`.

```sh
python3 scripts/gb_export.py              # uses art/tiles/kit.json when present
python3 scripts/gb_export.py --kit temp   # the exporter's own flat placeholder tiles
```
It runs in about 10 s and the output is deterministic. It needs `numpy`, `rasterio`, `shapely` and `Pillow`.

## Scale and units
- 1 metatile (16×16 px) = **6 m**, and one 8×8 GB tile = 3 m.
- Zone edges are whole metatiles: every edge is a multiple of 6 m in the world frame. So the metatile grid is global, and the same metatile is identical in every scene that touches it.
- Spec positions are in 8×8 tiles. The origin is the top-left corner of the scene, at world (x0, y1). A world point (X, Y) is tile ((X − x0)/3, (y1 − Y)/3).
- Actors and the player use GB Studio's own anchor. `x` is the left tile and `y` is the **bottom** tile row of the 16×16 sprite: GB Studio draws the sprite over rows y−1 and y (`ActorView`/`SpriteSheetCanvas` in the 4.3.2 source). The engine's player collision box is 16×8 on row y (`tools/gbs/sprites.py`).

## Zones

| id | Name | x (m) | y (m) | px | From proposal cell |
|---|---|---|---|---|---|
| `front_lawn` | Front Lawn | 456–816 | 180–354 | 960×464 | C-S, west part of EC-S |
| `old_campus` | Old Campus | 456–816 | 354–486 | 960×352 | C-M, west part of EC-M |
| `west_campus` | West Campus | 216–456 | 108–300 | 640×512 | WC-S |
| `performing_arts` | Performing Arts & Sports | 216–456 | 300–486 | 640×496 | WC-M |
| `science_quad` | Science Quad | 816–978 | 330–486 | 432×416 | east part of EC-M |
| `language_houses` | Language Houses | 816–978 | 168–330 | 432×432 | east part of EC-S |
| `studio_arts` | Studio Arts | 978–1140 | 168–366 | 432×528 | E-S, south part of E-M |
| `canyon_east` | Reed Canyon East | 978–1140 | 366–600 | 432×624 | north part of E-M, south part of E-N |
| `reed_lake` | Reed Lake | 696–978 | 486–600 | 752×304 | south part of EC-N |
| `cross_canyon` | Cross Canyon | 468–696 | 486–810 | 608×864 | C-N (includes the north fields) |
| `the_grove` | The Grove | 216–468 | 486–810 | 672×864 | WC-N |
| `northwest` | Northwest Campus | 0–216 | 486–810 | 576×864 | W-N |

Dropped cells:
- **W-S** and **W-M** (Crystal Springs Lake and the rhododendron gardens) have no campus buildings and lie outside the campus boundary.
- **EC-N north of y 600** and **E-N north of y 600** are neighbourhood housing only.

`world/gb/zones_overlay.png` shows the zones (black) over the lidar surface map, with the proposed grid in magenta.

### Why the cuts moved more than ±40 m
The proposal was a single grid. Checked against the real footprints, it can't keep every campus building whole. `zones.json` records the check as `proposal_check`.
- **x = 470**: no building-free line within ±40 m over the full height. Old Dorm Block (458–571), Gray Campus Center, Naito Hall and the Physical Plant overlap it.
- **y = 300**: no building-free line within ±40 m across the full width. In the east, the library (y 264–349), the ETC, Biology, Psychology, Physics, Chemistry and Vollum form an unbroken chain from y 264 to 431.

So each band gets its own column cuts, and each column its own row cut. The zones form a rectangle partition instead of a grid:
- **Old Campus | Performing Arts at x 456**: −14 m, between Kaul/Anna Mann (≤ 445) and Old Dorm Block/Gray (≥ 458).
- **The Grove | Cross Canyon at x 468**: between the Physical Plant (≤ 467) and Naito Hall (≥ 469).
- **Front Lawn top at y 354** (+54 m, outside the ±40 m rule): it has to clear the library's north end (348.9), and it puts the main drive, the South Lawn and the library together in one scene.
- **x 816**: the only line between the library (≤ 814.1) and the Science Quad. Greywood Hall starts at 815.8, so a **3.3 m² sliver** of it lies west of the line. That is under 2 % of its footprint and under 5 m², so it never reaches 40 % of a metatile and isn't drawn. The validator treats such slivers as not split, and REPORT.md lists them.
- **Science Quad | Language Houses at y 330**: between the ETC (≤ 322.9) and Biology/Psychology (≥ 336). **x 978** (+18 m): east of the Spanish and Chinese Houses (≤ 976.3).
- **Studio Arts | Canyon East at y 366**: north of Studio Arts (≤ 362). **Row 480 → 486** everywhere. The east edge is **1140**: campus ends near 1173, but the lidar stops at 1136, so the rest would be nodata.
- The **bottom edges** stop just south of SE Woodstock Blvd (y 168–180), not at y 60. West Campus goes down to 108 to include Parker House (y 114–137).

The result is 12 scenes, all within 960 px per side and 0.6 Mpx.

## Class grid (one per metatile, 36 m²)
The 1 m `surface_1m.tif` grid is flipped south-up and binned 6×6. Rules, in order (a later rule overwrites an earlier one):
1. The majority of water, tree, steep, paved, lawn and nodata. Nodata (east of 1136 m) becomes lawn.
2. **Steep** if ≥ 34 %. **Water** if ≥ 30 %, so streams survive.
3. **Path or road** if either covers ≥ 20 %, whichever is larger. **Bridge** if it is a path and an OSM `bridge` way covers ≥ 10 %.
4. **Connectivity pass.** Every metatile an OSM footway, path or service road passes through becomes path, and every one a street passes through becomes road. Lines are traced 4-connected, adding an orthogonal step at each diagonal step. Without this, diagonal paths broke into corner-touching fragments. Cells ≥ 70 % open water are skipped, but bridge ways are always drawn as bridge.
5. **Building** if a footprint covers ≥ 40 %. The metatile keeps the OSM id of the footprint covering most of it. Campus footprints win over neighbourhood ones. A campus building that reaches 40 % nowhere is forced onto its best-covered metatile. Only The Horrid Art Project (5 m²) needed this.
6. Tree cells whose 8 neighbours are all tree become **forest interior**: solid. Other tree cells are forest edge: walkable, the canopy you walk under. A tree cell with no tree on any side becomes a single **lone tree**: solid.

## Buildings and doors
- Each building is its footprint's metatiles. The southernmost metatile of each column is the **facade**; the rest are **roof**. Roof edge pieces (`_n`, `_e`, `_w`) are chosen by whether the neighbour has the same OSM id, so adjacent buildings stay separate.
- **Doors from OSM `entrance` nodes.** Each door goes on the building metatile nearest to its entrance. If that metatile is a roof edge (the entrance is on a north, east or west wall), the door piece moves to the nearest south-facade metatile and the door is flagged `moved_to_facade` in `meta.json` and REPORT.md.
- **Estimated doors.** Named campus buildings with no entrance get one door on the facade metatile nearest a path or road. This prefers a facade with open ground to the south and away from a scene's outer metatile ring. These are flagged `door_estimated: true` on the signpost and in `meta.json`.
- If the metatile south of a door is solid, it becomes path, so every door can be reached.
- **Art choices (stylistic, not from data).** Every roof uses the kit's dark pitched roof, because the light flat roof read as a plaza at this scale. Houses (off-campus, or campus buildings named "… House") and the Tudor Gothic core (Eliot, Old Dorm Block, Anna Mann, the library) get windowed facades with gables. Other campus buildings get the ribbon facade. Paved ground off campus draws as lawn, to keep the neighbourhood readable. OSM parking draws as `paved_parking`, and other paved ground on campus as `paved_plaza`.

## Collision (8×8, twice the metatile resolution)
- **Solid (1):** roof, facade, water, forest interior, lone tree, steep, and the outer tile ring of the scene except under exit triggers.
- **Walkable (0):** lawn, paved, path, road, bridge, forest edge and doors.
- Signposts are solid in GB Studio through their own actor collision, so the grid stays 0 under them.

## Signposts
- There is one signpost per named campus building, keyed by OSM id. It stands one metatile south-west or south-east of the building's main door: OSM `entrance=main` first, then a real door, then an estimated one. It never blocks a door's apron or sits on an exit.
- Cerf Amphitheatre and Eliot Circle are named campus features with CSV use text, so they get signposts too.
- In Science Quad, every tile beside Biology's door is solid or an exit, so its signpost stands on the door itself (`on_door: true`).
- **Dialogue.** The name in capitals, wrapped at 18 characters, then the CSV `use` text, with at most 4 lines in total. The engine packs 3 lines per text box. The text is the first sentence (abbreviations such as "A.E." don't end it), cut at clause boundaries (`:` `;` `,` `(`). A leading clause that only repeats the name is dropped. If even one clause doesn't fit, it is cut at a word and ends in "...", never on a function word. The text is ASCII only.

## Exits
- For every pair of zones sharing an edge, the exporter walks the shared span in tiles. An exit cell is one where the edge metatile is walkable on **both** sides. Runs are merged, then split into pieces of at most 6 tiles, so the landing spot stays close to where you left. The piece length grows if a scene goes over 30 triggers; none did.
- Each piece produces a mirrored pair:
  - The trigger is 1 tile deep on the edge row or column of each scene.
  - Its id names the direction and the destination, and the piece index counts along the edge: `exit_n_old_campus_0` in `front_lawn` pairs with `exit_s_front_lawn_0` in `old_campus`.
  - You land on the matching span of the neighbour, 2 tiles in from the edge, or 1 if 2 is blocked. The landing is clear of every trigger, and the 16×8 player box (row y, columns x and x+1) is walkable. You face the direction of travel.
- A piece whose neighbour has no room for the player is a one-metatile pocket at a scene corner. It is dropped on both sides and listed under Notes in REPORT.md.
- **Check.** A flood fill from the player start through every exit reaches 100 % of the walkable area of all 12 scenes, apart from 5 isolated player positions (2 in West Campus, 3 in Reed Lake), and reaches every signpost.

## Tile budget
- Each scene's background is rendered from the kit, and the exporter counts the unique 8×8 tiles exactly. On DMG, GB Studio doesn't dedupe flipped tiles.
- With the Art kit (130 tiles in total), every scene uses 63–88. No simplification step was needed.
- The steps are still in place, in order: merge lawn variants, drop decorative tiles (gables and fill variants), simplify autotile edges (no inner corners), and finally shrink the zone (manual). They run while a scene is over 180, and each step is logged in REPORT.md.
- With the placeholder tiles (`--kit temp`), the scenes use 34–54.

## Start
The player starts on the **Front Lawn** at tile (92, 47), facing up. That is just inside the main entrance, where the main drive (`way/158891925`) leaves SE Woodstock Blvd at about (733, 212) and heads to Eliot Circle.

## Outputs
| File | Contents |
|---|---|
| `world/gb/zones.json` | Zone boxes in metres, px sizes, source cells, and the proposal check |
| `world/gb/zones_overlay.png` | Zones over the surface map |
| `world/gb/<id>/background.png` | 4-shade background, RGB in the DMG palette |
| `world/gb/<id>/collision.png` | Debug view: solid in red, exits in blue, doors outlined in yellow (orange = estimated), signposts in magenta |
| `world/gb/<id>/spec.json` | Exactly one contract scene object |
| `world/gb/<id>/meta.json` | World box, unique tiles, door list with sources, per-metatile class names |
| `world/gb/project_spec.json` | Full spec: title, player, scenes |
| `world/gb/REPORT.md` | Per-scene limits table, tile log, doors, notes, warnings |

## Known limits
- Most doors are estimated: only 10 buildings have OSM entrances. Walking the site, or adding entrances to OSM, would fix this.
- Moved doors: entrances on north, east or west walls appear on the south facade, because the kit only has south-facing doors.
- Forest edge is walkable while the interior is solid, so the player walks over the outer ring of tree tiles. This follows the brief ("dense tree" is solid), but an art pass could give the edge its own look.
- Roads and water are both dark blob terrains, and in the west (SE 28th Ave) they look alike. That is an art-kit question.
- Steep is drawn as a ledge but is plainly solid, because the contract has only 0/1 collision and no one-way ledges.
