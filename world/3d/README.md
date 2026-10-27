# Reed College: 3D world (engine-neutral export + three.js walk)

Built by `scripts/export_3d.py` from `world/lowfi/reed_world.geojson`, the 2014 DOGAMI bare earth and the
`world/lidar/` layers. Every file uses one frame: **metres, x east, y north, z = elevation** (16–65 m).
`MANIFEST.json` is written last and lists the files; `heightmap.json` documents the raster convention.

## Rebuild
```sh
python3 scripts/export_3d.py      # world/3d/*.json, heightmap_16.png, surface_albedo.png, MANIFEST.json (~12 s)
python3 scripts/build_web3d.py    # world/3d/web/ data files (index.html is hand-written)
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python3 scripts/test_web3d.py   # screenshots + gameplay tests
```
The test needs three.js r128 locally (`cd /tmp && npm i three@0.128.0`); it routes the cdnjs URL to that copy.

## Decisions
- **Terrain:** the survey stops at x ≈ 1136 m. That strip (11.7 % of the frame) is a smooth pyramid/Laplace fill,
  not measurement. The web build also carves lake beds (1.6 m) and stream channels (0.35 m) for display only;
  `heightmap_16.png` stays raw bare earth.
- **Heights:** lidar p90 (416 buildings), else OSM `height` (119) or `building:levels` × 3.5 m (2), else 7 m (12).
  Trillium is flagged absent in 2014, so it uses OSM: 4 levels = 14 m. Walls run from the lowest ground under the
  footprint; the roof sits at mean ground + height.
- **Roofs:** a gable along the long axis of the oriented box when the lidar shows a pitched surface and the
  footprint is roughly rectangular (313 buildings). Pitched but irregular footprints are `pitched_complex` and
  render flat.
- **Trees:** 10,849 instances from nDSM peaks, with the rest of the canopy filled by Poisson-disk sampling, plus
  24 OSM-only trees. Conifer vs broadleaf is a crown-shape heuristic, not a species map. The web page adds about
  1,400 generic trees *outside* the data bounds so the horizon isn't a void.
- **Signs:** 57 buildings plus Eliot Circle, Reed Lake and Reed Canyon. 48 doors are estimated
  (`door_estimated`), using gb_export's rule: a south-facing wall point with an open doorstep, nearest a path.
  The text is the full CSV row.
- **Collision (web):** building footprints are exact polygons, and the player slides along the walls. Water,
  streams and slopes over 32° off paths come from a 1 m mask (`walk.bin`). Bridges are walkable decks.

## Web page (`web/`)
`index.html`, `world.json`, `terrain.bin`, `albedo.jpg`, `walk.bin` and `trees.bin` total about 1.5 MB.
three.js r128 loads from cdnjs. Controls: WASD or the arrow keys to walk, Shift to run, drag or right-drag to orbit,
the wheel to zoom, M for the top-down map, E to read a sign. On touch screens, the left side is a joystick and the
right side orbits.

© OpenStreetMap contributors (ODbL) · lidar: Oregon DOGAMI 2014.
