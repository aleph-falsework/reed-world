# Reed_World: Unreal 5.8 project

A walkable Reed College campus built from data, for Matthew's talk at Reed on **world → simulation → game → engine**. This Unreal project is the "engine" stage.

- **Source of truth:** GitHub `aleph-falsework/reed-world`, branch `world-3d`. The data, the scripts that generate assets, and every decision are documented there; see `docs/PROCESS.md` and `world/unreal/README.md`.
- **Generated kit:** `ReedWorldKit/` in this folder. Treat it as build output. Change the generator in the repo (`scripts/export_unreal.py`, `scripts/build_buildings.py`) and re-copy it; don't hand-edit it.

## What's in the level
**Map:** `/Game/ReedWorld/Maps/L_ReedCampus`, built by running `ReedWorldKit/import_reed.py` (Tools → Execute Python Script).

| Content | Details |
|---|---|
| Terrain | 12 static-mesh tiles from 2014 DOGAMI lidar bare earth, plus invisible edge walls |
| Buildings | 549, each named after its OSM id (`B_way_<id>`). Detailed meshes from `reed_buildings_detailed.glb`: lidar roof forms, plus a style family per building (`buildings_detailed.csv`). |
| Trees | About 10.9k instances in two Hierarchical Instanced Static Mesh components (broadleaf and conifer), placed from the lidar canopy |
| Signs | 60 signs; a TextRender shows the building name, and the full text sits in the actor tags |
| Water | Reed Lake and the canyon creek |
| Other actors | Lighting, sky, fog, and a PlayerStart south of Eliot Circle facing Eliot Hall |

Every actor the script creates is **tagged `ReedWorld`**, and re-running the script replaces all of them. Don't hand-place anything you want to keep with that tag.

## Coordinates (keep these consistent)
- World data is in metres: x east, y north.
- **UE X = east × 100, UE Y = −north × 100, UE Z = elevation × 100** (cm). There's no datum shift, so Z is the real elevation.
- UE yaw = −(world heading, measured counter-clockwise from east).
- Self-check: Eliot Hall's bounds should match `ReedWorldKit/reed_kit.json` to within about 60 cm.

## Unreal MCP
- The project uses Unreal MCP (Experimental, 5.8). The server runs at `http://127.0.0.1:8000/mcp`; the client config is in `.mcp.json`.
- MCP is loopback only and has no authentication. Never expose it beyond this machine.
- Tool calls run serially on the game thread: make one call at a time and save the level before big changes.
- Start with `list_toolsets` and `describe_toolset`, then use `call_tool`.

## Ground rules
- Facts about Reed's buildings (names, dates, uses) come only from the repo's data: `data/raw/reed_college_buildings.csv` and `data/raw/building_styles.csv`, which has its sources. Don't invent history.
- 48 of the 60 doors are **estimated**, flagged `door_estimated` in `signs.csv`. Real positions will come from Matthew's photos.
- All art is original. Nothing is copied from other games.
- Record anything durable you change in the repo, for example as a note in `world/unreal/README.md`.

## Next tasks
1. **Look-dev check:** inspect the brick, slate and trim materials and the normal-map green channel. Compare Eliot Hall with `world/buildings/out/preview/eliot_hall.png` in the repo.
2. **Collision and play:** confirm the buildings and edge walls block the player, and that the canyon slopes are walkable.
3. **Interactive signs:** walking up and pressing E shows the full sign text in a UMG widget. The text is already in the actor tags.
4. **Performance:** turn on Nanite for the buildings and terrain, check HISM culling for the trees, and profile in PIE.
5. **Lighting pass:** a Portland-autumn sun angle, sky and fog. Set camera bookmarks for the talk: Eliot Circle, Old Dorm Block, Reed Lake from the Blue Bridge, and the canyon.
