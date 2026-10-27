# Reed World art kit (DMG)

This is an original 4-shade Game Boy tile kit, player sprite and signpost for the GB Studio 4.3.2 campus walk. Everything is drawn in code by `make_art.py`, either as hand-typed ASCII pixel maps or with small procedural rules. Nothing is traced or copied from another game.

```sh
python3 art/make_art.py      # regenerates every file below; output is deterministic
```

## Palette
| idx | hex | letter | used for |
|---|---|---|---|
| 0 | `#e0f8cf` | W | paths, plaza, walls, foam, bridge boards; **sprite transparency key** |
| 1 | `#86c06c` | L | lawn, highlights, mortar, stone trim |
| 2 | `#306850` | D | water, asphalt, slate, canopy, shadows |
| 3 | `#071821` | K | outlines, glass, door frames |

Every PNG is checked against the palette when it's written. Nothing uses anti-aliasing.

## Files
| path | what |
|---|---|
| `tiles/kit.png` | Tile atlas: 8×8 tiles, 16 per row, index = row × 16 + column |
| `tiles/kit.json` | Tile names → indices, metatiles (`[tl, tr, bl, br]`), per-metatile info (`family`, `solid`, `mask`, `door`, `sign`, `ledge`), autotile lookup tables, building rule |
| `tiles/metatiles.png` | Every metatile in `kit.json` order, 16 per row, unlabelled |
| `tiles/metatiles_sheet.png` | Labelled reference sheet, grouped by family (×2) |
| `sprites/player.png` | 96×16 strip: down-idle, down-walk, up-idle, up-walk, right-idle, right-walk |
| `sprites/player_left.png` | The same strip with left frames in place of right ones |
| `sprites/player_ext.png` | 192×16: idle, walk, walk_b for down, up, right and left (for a 4-step cycle: idle → walk → idle → walk_b) |
| `sprites/player.json` | Frame order and layout, transparency key, suggested collision box |
| `sprites/signpost.png` | 16×16 static signpost actor |
| `preview.png` / `preview_1x.png` | A 10×9-metatile mock scene, at ×4 and at 1×: a Tudor hall with a double door, gables, a plaque and signs, a plaza, a footpath, a bench, lawn, forest, trees and a lake shore |
| `player_on_grounds.png` | The player over 18 ground and edge types |

## Tile budget
**140 unique 8×8 tiles** across 211 metatiles (128 before the road redraw: kerbs, gutter and centre lines took the road family from 12 to 25 tiles, one shared with parking). A scene uses only the families it needs; the exporter measures 62–98 per scene.

| family | tiles | family | tiles |
|---|---|---|---|
| building (both roofs, façades, doors, gable) | 36 | steep (ledge, cliff, stairs) | 11 |
| water (blob/47 + fill variant) | 14 | lawn | 4 |
| forest (blob/47 + fill variant) | 14 | tree | 4 |
| props (signpost, fence, hedge, bench) | 14 | bridge | 4 |
| path (16-case pipe) | 12 | paved | 3 |
| road (blob/47) | 25 | | |

A scene seldom uses every family: the canyon needs no buildings, and Old Campus needs no road. The real per-scene count is what the exporter measures.

How the budget was kept down:
- Metatiles are sliced and deduplicated automatically.
- All textures repeat every 8 px, so straight edges share tiles.
- The blob families are drawn from a distance field, so each 8×8 quadrant depends only on its own three neighbours. That gives 47 cases from about 13 tiles.
- Corner bays of a façade reuse the corner tiles of the plain wall.

## Metatile names
- **lawn:** `lawn_a` (plain; use for about 80% of cells), `lawn_b` (tufts), `lawn_c` (tuft and daisy)
- **paved:** `paved_plaza` (running-bond pavers), `paved_parking` (asphalt with a bay stripe on the west edge)
- **path:** `path_00` … `path_15`, a 10 px footpath, 4-neighbour
- **road:** `road_NNN`, blob/47 with a dark edge, a 2 px pale kerb and a dark gutter; the asphalt has sparse dark grain (water has light waves). Straight pieces carry a dashed centre line (4 on, 4 off) on the road's axis: mid-cell for a 1-wide road, on the seam for a 2-wide one (`road_dash`). `road_255` is the plain fill. Integration pass, 27 Oct: roads and water had read alike.
- **water:** `water_NNN`, blob/47 with foam and shallows. `water_fill_b` is a second fill for checkerboarding
- **forest:** `forest_NNN`, blob/47 with a scalloped canopy edge and cast shadow. `forest_fill_b` is a second fill. `tree` is a single 16×16 tree
- **steep:** `ledge`, `ledge_l`, `ledge_r` (a one-way south drop: the top half is lawn and the lip is in the bottom half); `cliff_top`, `cliff_face`, `cliff_face_l`, `cliff_face_r`, `cliff_base`; `stairs`
- **bridge:** `bridge_h` (east–west, rails on N and S), `bridge_v` (north–south)
- **building:** `roof_{tudor|flat}[_{n,e,w combos}]` (e.g. `roof_tudor_nw`, `roof_flat_new` for a 1-wide top), `roof_tudor_gable`; `facade_wall[_l|_r]`, `facade_window[_l|_r]`, `facade_ribbon` (modern band windows), `facade_door`, `facade_ddoor_l` + `facade_ddoor_r` (32 px Tudor-arched double door), `facade_plaque`
- **props:** `signpost`, `fence_h` (bottom half), `fence_v` (west half), `hedge`, `hedge_l`, `hedge_r`, `bench`

## Autotile bitmask convention
```
bit set = that neighbour is the SAME terrain (connects)
N = 1   E = 2   S = 4   W = 8   NE = 16   SE = 32   SW = 64   NW = 128
off-map neighbours count as connected (no edge is drawn at the scene border)
```
- **path** (`path_MM`, MM = 0–15): uses only N, E, S and W. Treat `paved_plaza`, `bridge_*`, door cells and `stairs` as connected so the path runs into them.
- **road, water, forest** (`<family>_NNN`): blob/47. A diagonal bit counts only when both orthogonals beside it are set, so reduce first with `canon(mask)` from `make_art.py`. `kit.json → autotile.<family>.masks` maps each of the 47 canonical masks to its metatile. To drive a blob family from 4 neighbours only, look up `canon(mask4 | 0xF0)`.
- Edges are drawn against **lawn**: the far side of an edge is assumed to be `#86c06c` grass. For water, count bridge cells as water, so the shore doesn't run under the bridge.
- `make_art.mask_at(grid, x, y, same)` computes the mask for you.

## Buildings
A building of w × h metatiles is roof on rows 0 to h−2 and façade on row h−1.
- **Roof edges:** add `n` on row 0, `w` on column 0 and `e` on column w−1.
- **Gables:** `roof_tudor_gable` can replace any roof cell in row h−2. It draws a stone-coped brick gable rising out of the slate.
- **Façade ends:** use `*_l` and `*_r` on the end columns.
- **Double door:** `facade_ddoor_l` and `facade_ddoor_r` fill two adjacent columns.

`building_layout(w, h, roof, doors, ddoors, gables, plaques)` in `make_art.py` returns the name grid. The hall in the preview is `building_layout(6, 3, "tudor", ddoors=(2,), gables=(1, 4), plaques=(4,))`.

Collisions: roofs and façades are solid. `facade_door` and the `facade_ddoor_*` cells are not solid, so the exporter can put a warp trigger there.

## Sprites
- 16×16 frames. The sprites use only L, D and K; **W `#e0f8cf` is the transparent background**.
- If GB Studio 4.3.2 needs another key colour (some versions treat `#65ff00` as transparent), change `SPRITE_KEY` and the frame rendering in `make_art.py`. Keep in mind that `#65ff00` would break the four-colour rule for the PNG.
- To re-lay out the frames, edit the `STRIP_MAIN`, `STRIP_LEFT` and `STRIP_EXT` lists. Frames are kept by name in `FRAMES`.
- **The player:** a student in a pale knit beanie with a pom-pom, a dark hoodie with light backpack straps, and dark jeans. From behind, the backpack is a big pale block. From the side, it's a box on the student's back. The rounded beanie and the pack give a silhouette that is distinct in all four directions.
- **The walk:** legs only, 2 px, so the head doesn't bob. `walk_b` is the opposite leg.

## Design notes
- **Value plan:** walkable surfaces are light (W path, plaza, boards), lawn is L, and anything solid or dangerous is dark (D water, D/K canopy, D/K slate). The player has a full K outline, so it reads on every ground; `player_on_grounds.png` shows this.
- **Lawn is mostly flat L.** The tufts and daisies are sparse on purpose, to keep the tile count low and leave the eye on paths and buildings.
- **Tudor Gothic:**
  - dark slate roof in 4 px courses, with an L ridge and verges
  - steep stone-coped gables with lancet windows
  - pale brick façade with 3 px courses and half-bond joints
  - lancet window pairs and quoined corners
  - a 4-centred (Tudor) arch on the double door, made by joining the single door's arch shoulders with a flat lintel
- The flat roof is pale gravel with a parapet, for modern and science buildings. Use it with `facade_ribbon`.
- **The forest** is a canopy of small conifer crowns, one per 8×8 tile, so the outline scallops line up with the crowns. Light comes from the upper left, so the shadows on the grass fall down and to the right.

## Review passes
1. **First render.** The path pebbles lined up into dotted "lane markings", so I replaced them with grass nibbles at the path's edge. The asphalt grain looked like pegboard, so I made the road plain. The slate courses read as a brick wall, so I shortened the seams. The cliff's vertical stripes looked like a bamboo fence, so I redrew it as boulders. The double door looked like a garage, so I removed the straps and added a transom and a single centre seam.
2. **Player.** The face read as a helmet or an owl, so I narrowed the hair, added the pom-pom and shortened the side nose. I joined the side backpack to the body.
3. **Budget and ledges.** I shared the corner tiles between the wall and window bays (132 → 128 tiles). I made the ledge a taller drop so it no longer reads as a log, and calmed the flat-roof gravel. I added checkerboarded fill variants to the water and forest in the preview.

## With more time
- A building drop shadow, as an east/south lawn-shadow autotile.
- Forest-to-water and road-to-path transitions. Edges currently assume lawn.
- A pitched-roof hip end, and a chimney or bell-tower piece for Eliot Hall.
- Corner caps for the cliff top and base.
- A vertical hedge.
- A less regular canopy, by offsetting the crowns in fill B.
- A third walk frame, and a 1 px bob on the side walk.
