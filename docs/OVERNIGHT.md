# Overnight run: GB Studio campus walk

How an unattended, multi-agent run turns the world layer into a playable Game Boy ROM. Started 26 Oct 2026, 23:50 PT, on branch `gb-overnight`.

## Locked decisions (Matthew, 23:39)
- **Scale:** 1 metatile (16×16 px, the player's footprint) = **6 m**. One screen (160×144 px) shows about 60 × 54 m.
- **Look:** classic 4-shade DMG: `#071821 #306850 #86c06c #e0f8cf`. Each scene may use at most 192 unique 8×8 tiles.
- **Player:** an original Reed student with a backpack. Nothing derived from Nintendo or Game Freak designs.
- **Target:** GB Studio **4.3.2** (Matthew's copy). Aseprite 1.3.18 is available for art passes in the morning.
- **Commits:** branch `gb-overnight`, reviewed in the morning.

## The loop
```
world layer + lidar ──► surface map ──► scenes (tiles, collisions, signposts, exits)
                                              │
       tile kit + player sprite ──────────────┤
                                              ▼
                              GB Studio 4.3.2 project ──► ROM ──► PyBoy plays it ──► screenshots
                                              ▲                                          │
                                              └────────────── fix ◄──── reviewer ◄───────┘
```

## Agents and handoffs

Each agent writes only to its own paths. The orchestrator (Claude, main session) commits, pushes and updates Jira.

| Wave | Agent | Writes to | Delivers |
|---|---|---|---|
| 1 | **Engine** | `tools/gbs/`, `tests/gbs_smoke/`, `docs/gbstudio-4.3.2-format.md` | Builds GB Studio 4.3.2 from source, headless. `make_project(spec)`, `build_rom()` and `play(rom, inputs)` with screenshots. Proven on a 2-scene smoke test. |
| 1 | **Data** | `scripts/lidar.py`, `scripts/build_world.py`, `world/lidar/`, `world/lowfi/reed_world.geojson` | Entrances, bridges and trees kept. Lidar heights per building, a canopy mask, slopes, and a 1 m surface map (building, water, tree, lawn, paved, path, road, slope). |
| 1 | **Art** | `art/` | An original DMG tile kit with metatile definitions and autotile variants, a building kit, the player sprite and a signpost sprite, plus a preview sheet. |
| 2 | **Exporter** | `scripts/gb_export.py`, `world/gb/` | Zones cut so they don't split buildings. For each scene: background, collisions, signposts and exits, plus a limits report. |
| 2 | **Prototype** | `world/gb/preview/` | A browser walk over the stitched scene backgrounds, with the same collisions. |
| 3 | **Integration** | `game/`, `build/` | The GB Studio project for every scene, `reed_world.gb`, a web build, and a scripted walkthrough with a screenshot of each scene. |
| 3 | **Reviewer** (fresh eyes) | `docs/REVIEW.md` | Checks the limits, OSM ids, originality, that the ROM boots, and that the screenshots match the map. Opens Jira bugs. |
| 4 | Orchestrator | `docs/MORNING_REPORT.md` and an artifact | What works, screenshots, what's left. |

### Scene spec (the contract between Exporter and Engine)
```json
{
  "title": "Reed World",
  "player": {"sprite": "art/sprites/player.png", "scene": "front_lawn", "x": 20, "y": 30, "direction": "up"},
  "scenes": [{
    "id": "front_lawn", "name": "Front Lawn",
    "background": "world/gb/front_lawn/background.png",
    "width": 80, "height": 72,
    "collisions": [[0,1,"..."]],
    "actors":   [{"id": "way/39516532", "name": "Eliot Hall", "sprite": "signpost", "x": 12, "y": 9,
                  "dialogue": ["ELIOT HALL", "Reed's original", "1912 building."]}],
    "triggers": [{"id": "exit_n_0", "x": 0, "y": 0, "w": 10, "h": 1,
                  "to": "old_campus", "to_x": 10, "to_y": 70, "direction": "up"}]
  }]
}
```
All positions and sizes are in **8×8 tile units** (GB Studio's own grid). Collision values: `0` is walkable and `1` is solid.

## Zones (proposed; the Exporter adjusts cut lines so they don't split buildings)

World metres, x east and y north. Campus bounds are x 100–1173, y 100–795.

| Columns | x range | | Rows | y range |
|---|---|---|---|---|
| W | 0–215 | | S | 60–300 |
| WC | 215–470 | | M | 300–480 |
| C | 470–700 | | N | 480–810 |
| EC | 700–960 | | | |
| E | 960–1273 | | | |

That's 15 cells. Cells with no campus are dropped, and small neighbours are merged, aiming for **10–12 scenes**. Each cell is about 250 × 220 m, roughly 42 × 37 metatiles (about 670 × 590 px), well under GB Studio's size limits. The tile budget is the real constraint.

## Guardrails
- Don't invent buildings, doors or names. Anything guessed is flagged `"estimated": true` and listed in the report.
- Keep OSM ids on everything.
- All art, sprites, fonts and music are original.
- No network access beyond package registries and GitHub.
- No actions on Matthew's computer overnight. Everything runs in the cloud workspace.
