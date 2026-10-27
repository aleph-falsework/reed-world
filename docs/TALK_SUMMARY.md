# Building Reed World: summary for the talk

One campus, built from open data, then rebuilt for four engines over one evening and one morning (26–27 Oct 2026). Matthew directed it and Claude built it. The companion deck is "Building Reed World", a Slides artifact.

## 1. Data: what describes a place
| Tier | Question | Source we used |
|---|---|---|
| 1 | What is where? | **OpenStreetMap**, via Overpass Turbo, exported as GeoJSON. It returned 3,766 features, including 2,057 building outlines. Only 19 of the 65 campus buildings had a floor count. |
| 1 | What happens there? | A **building-use spreadsheet** made from Reed's printed 2010 campus map: 53 numbered buildings plus 2 built since. |
| 2 | What is it like? | **Lidar** from Oregon DOGAMI's 2014 OLC Metro survey, three layers: bare earth, highest hit and intensity. Each layer was 698 MB and was cropped to a 7 MB, 1 m campus grid. |

**Left out on purpose:**
- Google and Apple imagery and 3D tiles, because their terms forbid tracing or extracting them.
- NAIP aerial photos, an optional extra, because lidar intensity already separates lawn from pavement.
- The printed map's geometry. It's a picture with no coordinates, so we used it for names only.

## 2. Processing
1. **Project to metres.** Everything goes from WGS84 into UTM 10N, with the origin moved to the map's south-west corner and north up.
2. **Classify, clean and keep IDs.**
   - Sort features into kinds: building (campus or neighbourhood), path, road, water, woods and lawn.
   - Drop the noise: 80 solar-panel polygons and 16 transit routes.
   - Match the spreadsheet's names to the OSM names.
   - The OSM id, for example `way/39516532` for Eliot Hall, travels with each feature into every engine.
3. **Derive facts from the lidar:**
   - heights for all 549 buildings
   - roof forms
   - about 10,900 trees from the canopy
   - canyon slopes
   - lawn versus pavement, 99.4% correct on test areas
   - a 1 m surface map
4. **Record the gaps instead of inventing:**
   - 48 of 60 doors are estimated.
   - The lidar predates Trillium (2019).
   - Sources disagree on some construction dates.
   - 8 campus buildings have no name.

## 3. Formats and engines
| Build | Formats | Notes |
|---|---|---|
| Flat world | GeoJSON (master), SVG, ASCII grid with an id grid, Tiled `.tmj`, an HTML viewer, a three.js wireframe walk | State only: no time, no rules |
| Game Boy | GB Studio 4.3.2 project with 4-shade PNG backgrounds and a collision grid, compiled to a `.gb` ROM plus a web build | 6 m per player tile and 12 scenes, each using 62–98 of its 192-tile budget. 61 signposts. The automated playtest passed 248 of 248 checks. |
| three.js | A 2 m heightmap, packed building and tree binaries, and the surface texture | Lidar terrain, measured heights, about 10,000 trees, full sign text and collision, all in a 1.5 MB download |
| Unreal 5.8 | glTF terrain, building and water meshes; tree and sign CSVs; an editor Python script | 549 buildings and 10,873 trees. Second pass: lidar roof forms, a sourced style for 52 of 60 named buildings, and 20 original materials. Ran cleanly in Matthew's UE 5.8.4. |

## 4. Time
| Build | With Claude (wall clock, from git) | By hand (Claude's estimate, not measured) |
|---|---|---|
| Flat world, formats and wireframe walk | about 50 min | 3–6 working days (24–47 h) |
| Game Boy game | 47 min to the first ROM; about 3 h including the independent review and fixes (overnight, unattended) | 3–6 weeks |
| three.js 3D game | about 55 min | 1–2 weeks |
| Unreal kit and detailed buildings | about 45 min | 3–6 weeks |

The hand estimates assume an experienced developer who already has the same data. Someone new to these tools should expect roughly double.

**Who did what:**
- **Matthew** framed the question, sourced the data, chose the scale, look and character, judged the results and ran Unreal.
- **Claude** wrote about 11,000 lines of code, ran and checked it, built the automated tests and documented the gaps.

**The overnight agent team:** Engine, Data, Art, Exporter, Integration, an independent Reviewer and a Fixer.

## 5. Takeaways
- **Every engine has its own resolution.**
  - The lidar measures every 1 m.
  - three.js and Unreal hold 2 m terrain and every building and tree.
  - A Game Boy tile is 6 m, so most of the data has to go.
  - The Game Boy needs a deliberate simplification pass, not a conversion.
- **More detail isn't more truth.**
  - OSM said Eliot Hall was 4 m tall; the lidar says about 16 m.
  - The detailed Unreal buildings use measured roofs, but their windows and materials are generated from a style rule.
  - Realism earns trust it hasn't earned, so mark what's guessed.
- **AI compresses the making, not the deciding.** Framing, data access, judgement and verification stay human.
- **The recipe:**
  1. Decide what you need.
  2. Download open data.
  3. Put everything on one metre grid and keep the IDs.
  4. Write down the gaps.
  5. Pick an engine and decide what it can hold.
  6. Script the export, so it can be re-run when the data improves.
