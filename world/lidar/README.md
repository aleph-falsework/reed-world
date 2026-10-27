# Reed College: lidar layers

Made by `scripts/lidar.py` from DOGAMI's **2014** OLC Metro lidar survey (bare earth, highest hit and intensity). The rasters were reprojected to UTM 10N at 1 m and aligned to the world frame. OSM vectors come from `world/lowfi/reed_world.geojson`.

## Rebuild
```sh
pip install --break-system-packages rasterio scipy scikit-image shapely pyproj pillow
python3 scripts/build_world.py data/raw/reed_osm.geojson data/raw/reed_college_buildings.csv world/lowfi
python3 scripts/lidar.py      # must run after build_world.py; it adds height_lidar_m to reed_world.geojson
```
`build_world.py` rewrites the GeoJSON without the lidar fields, so always run `lidar.py` after it. Running `lidar.py` takes about 12 s.

## Grid
Every raster is 1274 × 811 px at 1 m, in EPSG:32610, with its origin at the world frame's NW corner. Pixel (row r, col c) covers world x ∈ [c, c+1) and y ∈ [811−r−1, 811−r), so world y = 811 − row. The survey stops at about x = 1136 m. East of that line the lidar is nodata.

## Files
| File | What it is |
|---|---|
| `ndsm.tif` | float32, metres: highest_hit − bare_earth. NaN marks nodata. |
| `canopy.tif` | uint8, 1 for tree canopy and 0 otherwise. |
| `slope_deg.tif` | float32, bare-earth slope in degrees (central differences), NaN for nodata. |
| `hillshade.png` | Bare-earth hillshade, azimuth 315°, altitude 45°. |
| `surface_1m.tif` | uint8 surface class for each cell. 0 means nodata. |
| `surface_legend.json` | Class values, names, preview colours, cell counts, thresholds and the intensity calibration. |
| `surface_preview.png` | 2× preview: classes over a light hillshade, with building outlines and entrance nodes as white squares. |
| `buildings_lidar.json` | Lidar statistics for each building, the height disagreements, the buildings absent in 2014, and possible unmapped structures. |

The script also writes into `reed_world.geojson`. Each building gets `height_lidar_m`, the 90th percentile of the nDSM inside its footprint (null when there is no lidar). Buildings that are open ground in the 2014 data also get `lidar_2014_absent: true`. The OSM `height` and `building:levels` tags are left as they are.

## Surface classes (priority order: the first match wins)
| v | class | rule |
|---|---|---|
| 1 | building | OSM footprint |
| 2 | water | OSM water polygon, plus OSM stream lines buffered to about 2 m |
| 3 | road | OSM street (residential, tertiary, secondary, …) buffered to about 6 m |
| 4 | path | footway, path, steps, pedestrian or cycleway buffered to about 2.5 m; service roads to about 4 m |
| 5 | tree | canopy (see below) |
| 6 | steep | bare-earth slope > 25° |
| 7 | paved | open low ground with smoothed intensity < **137**, or OSM parking |
| 8 | lawn | open low ground with intensity ≥ 137, or OSM lawn or pitch, or ground inside OSM woods, scrub or the Reed Canyon park |
| 0 | nodata | no lidar and no OSM class |

The order above has two deliberate exceptions:
- **Bridges** (`bridge: true` ways from RW-7) are drawn as path *above* water, so walkways cross Reed Lake and the canyon.
- **Inside OSM woods, scrub and the Reed Canyon polygon**, open ground is lawn whatever its intensity. Forest litter and shade return low intensity and would otherwise read as paved. The only pavement there is mapped paths.

East of x ≈ 1136 m, only the OSM classes (building, water, road, path, parking, lawn) are filled. Everything else there is 0.

## Method and thresholds
- **Canopy:** nDSM > 2.5 m, outside building footprints buffered by 1 m. A 3×3 cross opening is followed by a closing, and components under 4 m² are dropped.
- **Building height:** the 90th percentile of the nDSM inside the footprint (all-touched for footprints under 4 px). The script also records the median and a roughness share: 3×3 std of the highest-hit surface > 0.5 m.
- **Disagreement test:** |lidar p90 − expected| > max(5 m, 50 %), where expected = levels × 3.5 m or the OSM `height`. The likely cause is labelled *tree overhang* when the median roof height agrees with OSM within max(3 m, 35 %). Otherwise the label is *OSM tag low or tall storeys*.
- **Absent in 2014:** less than 35 % of a footprint (with at least 20 px) has nDSM > 2 m.
- **Paved/lawn threshold:** taken from known areas, not guessed. The intensity was 3×3 median-filtered. Paved references were OSM parking lots (9,779 px). Lawn references were the OSM pitches, Eliot Circle and a South Lawn box (x 470–720, y 230–330; 29,944 px). Both sets were limited to open low ground. The threshold that maximises balanced accuracy is 137, which gives 99.4 %. Paved has a median of 34 (p90 59) and lawn a median of 174 (p10 156), so grass returns much brighter than asphalt at the lidar's near-infrared wavelength, as expected. A 5×5 majority (median) filter then cleans the grass mask.
- **Nodata:** inside the survey, intensity nodata is 0. East of the survey edge it is **255**, not 0, so the script masks both.

## Results (current run)
nodata 8.7 %, building 11.5 %, water 5.3 %, road 5.1 %, path 5.4 %, tree 34.0 %, steep 0.3 %, paved 16.6 %, lawn 13.2 %.

## Limits
- **2014 data.** Anything built later is missing from the lidar. **Trillium** (2019) is detected as absent: only 12 % of its footprint is above 2 m and the p90 is 2.4 m, so its `height_lidar_m` is meaningless. Two small campus structures are also absent: `way/919063711` (a 93 m² roof by the Childcare Center) and `way/367894209` (a 24 m² garage). **The Horrid Art Project** (5 m², too small for the test) shows only ground, so it is probably newer too. The **Performing Arts Building** (opened 2016) does show a full-height structure (median 12 m), so it was either built or framed by the time of the survey, or the footprint lines up with something older. That isn't confirmed.
- **Trees over roofs** push the p90 up, a lot for small houses. In the neighbourhood, 74 of 91 flagged disagreements look like tree overhang. Use the median in `buildings_lidar.json` when the roughness share is high.
- **Steep is mostly hidden:** 23,629 cells have slope > 25°, but 20,057 of them sit under canopy, so the surface map shows them as tree. For ledges in the canyon, combine `slope_deg.tif` with the tree class.
- **Bare earth under buildings** is TIN-interpolated, with visible triangles on the hillshade, so slope under footprints is meaningless.
- **Paved:** concrete patios, courts and dark roofs of unmapped sheds fall into paved. Shrubs under 2.5 m and parked cars count as open ground and are classified by intensity.
- **Possible unmapped structures:** four smooth raised areas outside OSM footprints, all off campus (x≈10 y≈591, x≈11 y≈556, x≈60 y≈491, x≈1007 y≈687), are listed in `buildings_lidar.json`. The surface map classes them as tree.
- Data from DOGAMI (Oregon Department of Geology and Mineral Industries); map data © OpenStreetMap contributors (ODbL).
