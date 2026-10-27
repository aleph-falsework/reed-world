# Reed College in Unreal Engine: import kit

This folder turns the Reed College world (`world/3d/`, the same data the three.js page uses) into a walkable Unreal level. One editor Python script, `import_reed.py`, imports everything and builds the level. Matthew's target is **Unreal Engine 5.8**. The script keeps fallbacks for 5.4–5.7.

> **Honest status:** the kit was built and checked on Linux **without Unreal**. The geometry, numbers and script logic were verified (see [What was verified](#what-was-verified)). The Unreal API calls themselves have not been run. Read [Unverified](#unverified-for-ue-58) before you rely on anything.

Rebuild the kit: `python3 scripts/export_3d.py && python3 scripts/export_unreal.py` (needs numpy, scipy, shapely, pillow, trimesh, mapbox_earcut).

## What's in the folder

| File | Size | What it is |
|---|---:|---|
| `import_reed.py` | 32 kB | The editor script. Run it in Unreal. |
| `reed_kit.json` | 4 kB | Coordinate mapping, Landscape numbers, PlayerStart, the self-check reference and counts. The script reads it. |
| `reed_terrain.glb` | 17.7 MB | Terrain as 12 mesh tiles (2 m grid, 515k triangles), plus 4 invisible edge walls. The albedo texture is embedded. |
| `reed_buildings.glb` | 1.6 MB | 549 buildings, one mesh node each, named `B_<osm-type>_<osm-id>` (e.g. `B_way_39516532` = Eliot Hall). Walls and roofs are separate material slots, gable roofs come from the lidar, and normals are flat-shaded. The node `extras` hold osm_id, name, map number, realm, height and its source, and roof type. |
| `reed_water.glb` | 49 kB | 4 lakes (flat, at surface elevation) and 6 stream ribbons. |
| `reed_tree.glb` | 13 kB | `SM_Tree_Broadleaf` and `SM_Tree_Conifer`, each 1 m tall with a 1 m crown radius, base at the origin. |
| `reed_sign.glb` | 4 kB | `SM_SignPost`: a post plus a 1.6 × 0.9 m board, whose readable face points along +X. |
| `T_ReedGround.png` | 3.1 MB | Ground colour (stylised surface classes), 2048×1024. |
| `heightmap_landscape_1009.png` | 0.7 MB | 16-bit Landscape heightmap, 1009×1009 at 127 cm. |
| `heightmap_landscape_2017.png` | 2.2 MB | 16-bit Landscape heightmap, 2017×2017 at 64 cm. |
| `buildings.csv` | 26 kB | mesh, osm_id, name, realm, map_no |
| `trees.csv` | 0.6 MB | 10,873 trees: x_cm, y_cm, z_cm, height_cm, radius_cm, source (lidar/osm), form |
| `signs.csv` | 13 kB | 60 signs: position, yaw, door, osm_id, name, map_no, door_estimated, and the full text |
| `water.csv` | 1 kB | Water meshes and their surface elevation in cm |
| `preview/` | | Renders of the kit in three.js, made from these exact files |
| `dev/` | | A stub `unreal` module and dry-run harness (`python3 dev/run_stub.py`) |

## Coordinates

Unreal works in centimetres, is left-handed and has Z up. The world data is in metres, with x east and y north.

| | X | Y | Z |
|---|---|---|---|
| **Unreal (cm)** | east × 100 | **−north × 100 (south)** | elevation above sea level × 100 |
| glTF files (m, Y-up) | east | elevation | −north |

- The campus covers UE X 0 to 127,200 and Y −81,000 to 0. Ground sits at Z ≈ 1,500 to 6,500 cm (15–65 m, NAVD88). No datum is subtracted, so the elevations are real.
- Unreal's glTF importer turns glTF (X, Y, Z) metres into UE (X, Z, Y) × 100 cm, which gives exactly the mapping above. Every vertex is written in world position with identity transforms, so every imported mesh is placed at (0, 0, 0).
- **Yaw** is UE yaw = −(world heading, measured counter-clockwise from east). East is 0°, south is +90° and north is −90°.
- In the top view (**+X right, −Y up**), north is up. Reed Lake lies north of the Old Campus, and Eliot Hall's long axis runs east–west (79 × 27 m).

## Steps in Unreal (about 15 minutes, most of it waiting)

1. **Create the project.** In the Epic Games Launcher, open **UE 5.8**, then **Games → Third Person**, **Blueprint**. Starter Content isn't needed. Name it e.g. `ReedWorld`. The Third Person template gives you a walking character whose GameMode is the project default, so any new level uses it.
2. **Enable the plugins.** Go to **Edit → Plugins**, search for and tick **Python Editor Script Plugin**, then restart the editor. Also check that **Editor Scripting Utilities** is enabled if it's listed, and that **Interchange** and **glTF Importer** are on (they are by default in 5.x).
3. **Copy the folder.** Copy this whole `world/unreal` folder to `<YourProject>/ReedWorldKit/`, next to `Content/`, **not inside it**. That way Unreal won't try to auto-import the PNGs.
4. **Run the script.** Choose **Tools → Execute Python Script…** and pick `ReedWorldKit/import_reed.py`. You can also type `py "C:/…/ReedWorldKit/import_reed.py"` in **Output Log → Cmd**. The script:
   - imports the files into `/Game/ReedWorld/…`. Expect a few minutes with the editor unresponsive; 549 buildings become 549 static meshes.
   - creates and opens the level `/Game/ReedWorld/Maps/L_ReedCampus`, a plain level without World Partition.
   - places the terrain, buildings, water, 2 tree HISMs (10,873 instances), 60 signs, the sun, sky, sky light, fog, clouds and a PlayerStart, then saves.
   - Watch the **Output Log**, filtered on `ReedWorld`. It should report `axis self-check OK` and the counts: terrain 12 tiles, buildings 549, trees 10097 + 776, signs 60.
5. **Press Play** (Alt+P). You start about 20 m south of Eliot Circle, facing Eliot Hall to the north-west. Walk with WASD.

Re-running is safe. Everything the script spawns is tagged `ReedWorld` and is deleted before it rebuilds. To skip the slow re-import, set `REIMPORT = False` at the top of the script.

Settings at the top of `import_reed.py`:
- `TERRAIN_MODE` (`'mesh'` or `'landscape'`)
- `CREATE_LEVEL`
- `SPAWN_NEIGHBOURHOOD`
- `TREES`
- `SIGNS`
- `REIMPORT`
- `TEXT_YAW_OFFSET`
- `TREE_COLLISION`

## Optional: terrain as a Landscape instead of meshes

Mesh tiles are the default because they need no manual step. A Landscape gives you sculpting and painting tools.

1. In the script, set `TERRAIN_MODE = 'landscape'`, then run it once. It skips the terrain meshes and prints these numbers.
2. Open **Landscape mode** (Shift+2) and choose **Manage → New → Import from File**. Pick the heightmap and enter these values:

| | 1009 (recommended) | 2017 |
|---|---|---|
| File | `heightmap_landscape_1009.png` | `heightmap_landscape_2017.png` |
| Location (X, Y, Z) | **63600, −40500, 3991** | **63600, −40500, 3991** |
| Rotation | 0, 0, 0 | 0, 0, 0 |
| Scale (X, Y, Z) | **127, 127, 15** | **64, 64, 15** |
| Section size / sections per component / components | 63×63 quads, 2×2, 8×8 (or 63, 1×1, 16×16) | 63×63 quads, 2×2, 16×16 |
| Resulting Landscape actor location | −408, −104508, 3991 | −912, −105012, 3991 |
| Covers (world m) | x −4…1276, y −235…1045 | x −9…1281, y −240…1050 |

3. Click **Import**, then run the script again. It finds the Landscape, gives it `M_ReedGround`, and moves it to the resulting actor location if the editor put it somewhere else.

**How Z works.** A 16-bit landscape pixel *p* sits at **Z (cm) = Location.Z + (p − 32768) × Scale.Z / 128**. At Scale.Z = 100 that spans ±256 **m**, which is ±256 cm × Scale.Z, not ×Scale.Z/100. The campus spans 1,489 to 6,493 cm. With Location.Z = 3991 (the mid-height) and Scale.Z = 15, the representable range is 151 to 7,831 cm, in steps of 0.117 cm. Decoding the PNGs with this formula reproduces the terrain to within 0.06 cm at every pixel, and to within 3.5 cm (1009) and 0.9 cm (2017) at the 60 sign spots (that's resampling). Outside the campus, the padding repeats the edge heights, so expect flat streaks out there.

**Location vs actor location.** The **Location field** in the New Landscape panel is the landscape's **centre**: the editor offsets the actor by half its size. That's why the table gives both values.

## What to expect

- Stylised ground: lawns, paths, roads and water are painted from the lidar surface map, not photographed. Buildings are flat-coloured extrusions with gabled or flat roofs; campus buildings are warm brick and the neighbourhood is grey.
- The lakes are carved 1.5 m into the terrain, and the streams 0.3 m, so the water planes are never pierced. Water has no collision, so you can wade.
- Trees are low-poly lollipops and cones scaled from lidar. They are walk-through (`TREE_COLLISION = False`).
- Signs stand beside doors, 2.6 m out from the door, with the board facing away from the building. The board shows the building name and map number. The **full text** (the CSV "what happens there", plus changes since 2010) is in the sign actor's **Tags** (`text:…`), along with `osm:…`, `name:…`, `map_no:…` and `door_estimated:…`. Doors marked `door_estimated:true` were guessed; there was no OSM entrance.
- Invisible walls at the data edge stop you walking off the world. East of x ≈ 1136 m there was no lidar, so that strip's terrain is a smooth fill.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `axis self-check FAILED` in the log | The glTF importer applied an unexpected rotation or scale. On re-import, check the Interchange options: no extra transform, **Bake Meshes** on, and **Combine Static Meshes** off. The script auto-corrects a ×100 unit error and a mirrored Y on its own (it logs a warning). |
| You fall through the ground | Open a terrain tile, e.g. `T_Terrain_r1_c1`, and in **Collision Complexity** choose **Use Complex Collision As Simple**, then save. The script tries to do this through `body_setup`. |
| Sign text is mirrored, or only shows from behind | Set `TEXT_YAW_OFFSET = 180.0` and re-run. |
| You play as a floating camera, not the character | In **World Settings → GameMode Override**, choose `BP_ThirdPersonGameMode`, or check that the project was made from the Third Person template. |
| A dialog pops up during import | Accept the defaults. If it asks, leave "combine meshes" off. |
| Trees are missing and the log says "HISM path failed" | The fallback spawned the 400 tallest trees of each kind as separate actors. Paint the rest with Foliage mode using `SM_Tree_*`, or report the log message. |
| Everything is very bright or very dark | That's auto exposure. Add a Post Process Volume (Infinite Extent) and set Exposure → Min/Max EV100 to about 10. |
| Buildings look like solid grey blocks | The materials didn't assign. Check that `MI_Wall_*` and `MI_Roof_*` exist in `/Game/ReedWorld/Materials`, then re-run. |

## What was verified

All of these were checked here, without Unreal:
- **glbs** load in trimesh and pygltflib, with the expected node counts and names (549 `B_*`, 12 `T_Terrain_*`, 10 `W_*`). Every triangle's winding agrees with its vertex normal (100 %). The bounds converted to UE cm match the campus (X 0–127,306, Y −81,050–0, Z 989–9,494, the latter including the edge walls).
- **Renders** (`preview/`): the kit's own glbs and CSVs, loaded in three.js r128 through headless Chromium (SwiftShader). Trees and signs were placed by converting the CSV's UE cm back with the inverse mapping, which tests the CSV convention.
  - North is up, Reed Lake lies north of the Old Campus, and Eliot Hall runs east–west.
  - Buildings sit on the terrain.
  - Sign boards face away from their building.
  - The PlayerStart view looks at Eliot Hall.
- **Landscape numbers**: decoded with UE's formula and checked against the terrain (above).
- **`import_reed.py`** compiles under Python 3.13 and **3.11** (5.8's version). It also runs end to end, twice, against `dev/unreal_stub.py`:
  - The stub simulates the asset registry and the glTF importer's axis swap.
  - Both runs give the same 703 actors, with nothing untagged, no errors and no warnings. The second run deletes the first run's actors.
  - The axis self-check passes, and it auto-corrects when the stub mirrors Y or drops the ×100.
  - Landscape mode prints the table above.

## Unverified (for UE 5.8)

None of this has been run in Unreal. Each item is guarded in the script with a fallback, a log message, or both.
1. **The glTF importer's axis conversion** (glTF (X, Y, Z) → UE (X, Z, Y) × 100) is taken from Epic's GLTFCore documentation ("from glTF right-handed and Y-up to left-handed and Z-up") and from known behaviour, not from a test. The Eliot Hall self-check catches a mismatch at runtime.
2. **Interchange asset names.** The script expects static meshes named after the glTF nodes. It also matches names with a prefix or suffix (`SM_…`, `…_1`). Release notes say 5.8 reworked the Interchange import dialog and renamed options; the automated import uses the defaults, so the script sets no options.
3. **Collision.** The script sets `body_setup.collision_trace_flag = CTF_USE_COMPLEX_AS_SIMPLE` through `get_editor_property('body_setup')`. If that property isn't exposed, it falls back to box collision; see Troubleshooting.
4. **HISM creation** goes through `SubobjectDataSubsystem.add_new_subobject` (5.0+), with a fallback to individual actors.
5. **TextRender facing** (which side is readable) and `HorizTextAligment` / `VerticalTextAligment` (Epic's own spelling). Use `TEXT_YAW_OFFSET` if the text is backwards.
6. **`LevelEditorSubsystem.new_level(path, is_partitioned_world)`** signature, with a fallback without the flag. The level is meant to be non-World-Partition.
7. **The New Landscape "Location = centre" behaviour.** The script corrects the actor location afterwards in any case.
8. **The DirectionalLight `atmosphere_sun_light`, SkyLight `real_time_capture` and fog property names.** Any failure only logs a warning.
9. **Performance** with 10,873 tree instances, 549 building meshes and 515k terrain triangles. This should be light for UE5, but it hasn't been measured. To use Nanite, enable it on the meshes in the editor.

Data © OpenStreetMap contributors (ODbL). Lidar from Oregon DOGAMI, 2014 OLC Metro.
