# How this was built

This is a record of the code behind the world layer: what each piece does, what went wrong along the way, and an estimate of what the same work would take without AI assistance.

It was built in one evening session (26 October 2026) between Matthew and Claude, an AI assistant. Matthew supplied the data and the direction. Claude wrote and ran the code and checked the results. The first input arrived at 22:17, and the third commit landed at 23:08, about 50 minutes later. That time includes setting up this repo.

## Who did what

| Matthew | Claude |
|---|---|
| Framed the talk: world / simulation / game / engine | Proposed the data sources and the shared-ID approach |
| Exported OSM data from Overpass Turbo | Inspected and cleaned the export |
| Produced the building-use CSV (in an earlier session) and collected the 2010 campus map | Matched CSV rows to OSM features |
| Chose the formats and the fidelity levels | Wrote the five scripts (727 lines of Python with embedded JS and CSS) |
| Created the GitHub repo and installed the app | Rendered, screenshotted and fixed each output |
| Asked for the walk, then for zoom | Wrote the gap report and the docs |

The data gathering, the decisions about what the talk needs, and the judgment calls ("we might want to see a little more") stayed with the person. AI assistance compressed the implementation time.

## The pipeline

Everything runs from a single source file. Each script reads the output of the one before it, and every feature keeps its OpenStreetMap id at every stage.

```
data/raw/reed_osm.geojson ─┐
data/raw/reed_college_buildings.csv ─┴─► build_world.py ─► world/lowfi/reed_world.geojson  (canonical)
                                                               │
                ┌──────────────────────┬───────────────────────┼────────────────────────┐
                ▼                      ▼                       ▼                        ▼
          render_svg.py          rasterize.py             gen_html.py              gen_walk.py
          vector map (SVG)       ASCII grid + id grid     map ⇄ grid viewer        three.js walk
                                 Tiled map + tileset
```

### 1. `build_world.py`: making the world (125 lines)
- **Reads the Overpass export.** It has 3,766 features, most of them neighbourhood houses, solar panels and transit routes.
- **Finds the campus.** It uses the `amenity=university` polygon (`way/27400683`) and keeps everything within a 100 m margin. The north side gets only 15 m, because the export stops there.
- **Reprojects WGS84 lat/lon to UTM zone 10N** (metres), then shifts the result so the south-west corner is (0, 0). Every later step can then work in plain metres, with x pointing east and y pointing north.
- **Sorts each feature into one kind:** building, path, road, service road, stream, water, woods, green, field, parking or boundary. It drops 80 solar-panel polygons, 16 route relations (the Amtrak and TriMet lines, which come through as broken geometry) and one invalid polygon.
- **Splits buildings into campus and neighbourhood**, by whether the footprint lies inside the campus polygon or matches a CSV row.
- **Matches the CSV to OSM** through a hand-written alias table, because the two sources name things differently: "Library (Hauser)" in the CSV is "Eric V. Hauser Memorial Library" in OSM, and "Foster" is "Foster Hall". Matched buildings carry their 2010 map number, category, use and post-2010 changes.
- **Writes `gaps.json`**, which lists everything it couldn't match or name. The README turns that into the list of data gaps.

### 2. `render_svg.py`: the vector map (147 lines)
- Draws each feature kind as a styled SVG group, in a fixed order: ground cover, then water, roads, paths, neighbourhood buildings, the campus boundary, campus buildings, and labels last.
- Every shape keeps its OSM id as its `id`, plus `data-*` attributes, so a later script or page can find it.
- Labels sit on each building's representative point, with one label per name. Long names get shortened, and each label has a halo.
- Adds the scale bar, north arrow, title and OSM credit.

### 3. `rasterize.py`: grids for games (164 lines)
- A shared rasterizer lays a grid over the world and decides what fills each cell, using a spatial index (shapely's STRtree).
- **The ordering rule:** a line (a path or road) claims every cell it touches. An area (a building or lake) claims a cell only if the cell's centre falls inside it. Without that rule, thin paths disappear at coarse resolution and buildings bloat.
- A priority order settles conflicts: a building beats water, water beats a path, and a path beats lawn.
- **ASCII:** cells are 8 m × 16 m, so shapes look true in a monospace font, where characters are about twice as tall as they are wide. The 2010 map numbers are stamped onto the buildings. A companion JSON grid maps every cell back to an OSM id.
- **Tiled:** 16 px tiles at 4 m each. The script generates a monochrome tileset image and writes a tile layer for the ground. It adds an object layer of building polygons, converted from world metres to Tiled's pixel coordinates (where y points down), each with an `osm_id` property.

### 4. `gen_html.py`: the linked viewer (133 lines)
- Embeds the SVG and draws the ASCII grid on a canvas. Hovering on either one highlights the same OSM id on both and shows its name and use.

### 5. `gen_walk.py`: the three.js walk (158 lines)
- Exports building footprints, paths, water and boundaries into a compact JSON blob embedded in the page.
- **Wireframes.** Each footprint becomes an outline at ground level, an outline at roof height, and a vertical edge at every corner. They're built as raw line-segment buffers, one draw call per style, so roughly 550 footprints render quickly.
- **Heights** come from OSM `height`, or from `building:levels` × 3.5 m. When neither exists, the building gets a guessed 7 m and is drawn dimmer.
- **Walking.** A perspective camera follows the walker. Input is WASD or arrow keys, Shift to run, or drag on touch. The page runs a point-in-polygon test each frame to say which building you're in or near.
- **Zoom and tilt** (second commit): a slider and buttons on a log scale, pinch zoom from two-pointer tracking, a tilt from 0° to 60° that orbits the camera south of the walker, three presets, and a scale bar computed from the camera height.

## Problems hit along the way

These are where the time goes when you work without assistance. Each was found by running the code and looking at the output.

| Problem | Fix |
|---|---|
| The user's computer had Python but no `shapely` or `pyproj` | Did the processing in a cloud workspace and copied results back to the folder |
| 10 features in the export crashed the geometry parser (transit route relations with broken geometry) | Skipped route relations and logged every invalid id |
| The export held 2,057 buildings, mostly neighbourhood houses | Split them into campus and neighbourhood, and drew the neighbourhood dimmer |
| 55 campus "buildings" were really solar panels | Filtered out `power` and `generator:*` tags |
| CSV names and OSM names disagree | Hand-written alias table; unmatched names reported, not guessed |
| Labels were illegible in the PNG preview (the converter ignores the CSS `paint-order` property) | Drew halos as a separate text layer under the labels |
| "Eliot Circle" was labelled twice (two OSM polygons) | Deduplicated area labels |
| A 5-row blank band at the top of every output | The export stops about 20 m north of campus; trimmed the north margin |
| Monospace characters made the ASCII map look stretched north–south | Used non-square 8 × 16 m cells |
| Testing WebGL without a GPU or internet | Headless Chromium with software rendering, serving three.js from a local copy |
| The walk's controls covered the scale bar on phones | Moved the panel and scale bar at narrow widths |
| GitHub push refused with a 403 | The Claude GitHub app had to be installed on the account first |

## Without AI assistance: an estimate

These figures are estimates for an experienced creative technologist who already knows Python, basic GIS and three.js, and who already had the same data in hand. Someone learning GIS projections, the Tiled format or three.js along the way should expect roughly double.

| Piece | Estimated hours | Where the time goes |
|---|---:|---|
| Data inspection, clipping, reprojection, CSV matching | 3–6 | Learning the export's quirks; broken relations; name reconciliation |
| SVG renderer (styling, labels, scale bar, legibility) | 4–8 | Label placement and halos; cartographic styling by trial and error |
| Rasterizer, ASCII and id grid | 3–5 | Choosing the touch-vs-centre rule; priority order; character aspect |
| Tiled export and tileset | 2–5 | Reading the TMJ spec; flipping y; polygon object coordinates |
| Linked map ⇄ grid viewer | 4–8 | Canvas text grid, cross-highlighting, responsive layout |
| three.js wireframe walk | 4–8 | Extruding geometry by hand, camera, input, HUD |
| Zoom, tilt, pinch, presets, phone layout | 2–4 | Pointer events, camera orbit, layout on small screens |
| Gap report, READMEs, repo setup | 2–3 | |
| **Total** | **24–47 hours** | **About 3 to 6 working days** |

**What AI assistance changed:** the implementation took about 50 minutes of wall-clock time instead of several days. Most of the saving is in the loop of writing, running, screenshotting, spotting a problem and fixing it.

**What it didn't change:**
- Someone still has to export the data, decide what the talk needs and judge whether the result reads well.
- The outputs are only as good as OpenStreetMap. Heights are missing for 46 of the 65 campus buildings, and some recorded ones look wrong (Eliot Hall comes out at about 4 m).
- The checks were screenshots and headless runs, not field verification. Nobody has yet compared the map against the actual campus.

## Rebuilding

See the main [README](../README.md). Every output can be regenerated from `data/raw/` with the scripts in `scripts/`.
