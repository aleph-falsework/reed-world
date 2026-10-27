# Reed College: World → Simulation → Game → Engine

Material for a talk at Reed College on the difference between a **world** (a map; state only), a **simulation** (events over time and chance), a **game** (structure and rules), and a **game engine** (an environment for authoring games).

Every stage is built on one canonical file, `world/lowfi/reed_world.geojson`. Each feature in it keeps its OpenStreetMap id, so later layers attach to the same objects instead of redrawing them.

## Layout
- `docs/PROCESS.md`: how this was built, the problems hit along the way, and an estimate of the effort without AI assistance.
- `data/raw/`: inputs. These are the Overpass export, the building-use CSV and the 2010 campus map, which is used only as a reference.
- `scripts/`: the pipeline (Python 3, `shapely`, `pyproj`, `Pillow`).
- `world/walk/reed_walk.html`: a top-down three.js wireframe you can walk around (WASD or drag). Building heights come from OSM levels, or are estimated and drawn dimmer.
- `world/lowfi/`: the world layer as GeoJSON, SVG, ASCII grid, Tiled map and an interactive HTML page. See its README for the files and the data gaps.

## Rebuild
```sh
pip install shapely pyproj pillow
python scripts/build_world.py data/raw/reed_osm.geojson data/raw/reed_college_buildings.csv world/lowfi
cd scripts
python render_svg.py ../world/lowfi/reed_world.geojson ../world/lowfi/reed_world_lowfi.svg
python rasterize.py ../world/lowfi/reed_world.geojson ../world/lowfi
python gen_html.py ../world/lowfi ../world/lowfi/reed_world.html
python gen_walk.py ../world/lowfi/reed_world.geojson ../world/walk/reed_walk.html
```

## Roadmap
- [x] World, low fidelity
- [ ] World, high fidelity: DEM terrain, building heights, canyon vegetation
- [ ] Simulation
- [ ] Game
- [ ] Engine export

Map data © OpenStreetMap contributors, ODbL.
