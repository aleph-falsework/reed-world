# Reed College — World Layer (low fidelity)

State only: footprints, paths, water, named buildings. No time, no agents, no rules.
Source: `export.geojson` (Overpass/OpenStreetMap) + `reed_college_buildings.csv`.
© OpenStreetMap contributors (ODbL).

## Files

| File | What it is |
|---|---|
| `reed_world.geojson` | **Canonical world.** Every feature keeps its OSM id (`way/…`, `node/…`). Coordinates are UTM 10N (EPSG:32610) in metres, shifted so the SW corner is (0,0); the offset is in `meta.origin_utm`. Campus buildings carry `map_no`, `category`, `use`, `changes` from the CSV. |
| `reed_world_lowfi.svg` | Monochrome diagram. 1 SVG unit = 1 m, north up, 200 m scale bar. Each shape's `id` is the OSM id (`way-39516532`) with `data-osm`, `data-name`, `data-use` attributes. |
| `reed_world.html` | Interactive viewer: the SVG and the ASCII grid side by side, linked by id. |
| `reed_world_ascii.txt` | 160 × 51 character grid. One character = 8 m E–W × 16 m N–S, so shapes look right in a monospace font. Numbers are the 2010 campus-map numbers (T = Trillium, A = Performing Arts). |
| `reed_world_ascii_ids.json` | The same grid as indices into an `ids` list, so every cell resolves to an OSM id. |
| `reed_world_tiled.tmj` + `reed_tileset.png` | Tiled map (opens in Tiled 1.10+). 319 × 203 tiles at 16 px, 4 m per tile. A `ground` tile layer plus a `features` object layer: building polygons and named trees, each with an `osm_id` property. |

Raster priority (what wins a cell): campus building > neighbourhood building > water > stream > footpath > street > service road > parking > field > woods > lawn.

## Missing or ambiguous in the data (not invented)

**On the 2010 map/CSV but not named in OSM**
- #1 Birchwood Apartments. Possibly the unnamed 442 m² building `way/39516256` in the NW corner; unconfirmed.
- #13 Theatre: no OSM building with that name.
- #41 Paradox Lost Café: no OSM feature (it's inside another building).
- #53 Center for Advanced Computation: no OSM feature; current use is also unconfirmed in the CSV.
- #36 Eliot Circle is a lawn in OSM, not a building. #21 Cerf Amphitheatre is a point, not a footprint.

**Named in OSM but not on the 2010 map**
- Childcare Center, Greenwood Hall, Gyms I and II, Reed Research Reactor, The Horrid Art Project. They're labelled, but have no CSV use description.
- Trillium and the Performing Arts Building (post-2010) are matched to CSV rows.

**Unnamed campus buildings (8)**, shown in mid-grey on the SVG:
- `way/39516256`: 442 m², NW corner, near the Childcare Center.
- `way/919063711`: a 93 m² roof, next to the Childcare Center.
- `way/685753541`: 51 m², about 60 m from Sitka House.
- `way/126856901`: tagged dormitory, 137 m², between Griffin and McKinley.
- `way/877776270`: 46 m², near Gyms I and II.
- `way/119843566` and `way/1047023983`: 128 m² and 132 m², next to Scholz Hall.
- `way/367894209`: a 24 m² garage near the Johansen House.

**Other gaps**
- Building heights: only 19 of 65 campus buildings have `building:levels`, and only 6 have `height`. That isn't enough for 3D extrusion without another source.
- No elevation data was supplied (`reed_dem.tif` isn't in the folder). The canyon is flat in every file.
- The Overpass box ends about 20 m north of campus, so the north edge is cropped tight. South, east and west have a 100 m margin.
- Paths: footways inside some buildings, and where they cross the canyon, are drawn as tagged. The pedestrian and canyon bridges aren't marked separately.
- Skipped: 80 solar-panel polygons, 16 transit and route relations, and 1 broken geometry (`way/369936785`, a one-storey building).
- The CSV notes the Chinese House has moved since 2010. OSM shows its current position.
