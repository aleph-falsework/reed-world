/*
 * Load a project with GB Studio 4.3.2's own loader and check every .gbsres
 * strictly against GB Studio's TypeBox schemas.
 *
 * GB Studio's loader uses Value.Cast, which silently repairs bad fields; this
 * script uses Value.Errors so we see anything that would have been repaired.
 * It also reports whether loading would trigger a migration (isMigrated) and
 * whether any sprite lacks frame data (modifiedSpriteIds, which the editor
 * would auto-detect and rewrite on open).
 *
 * Bundled with GB Studio's CLI webpack config by tools/gbs/node/build_checker.sh
 * and run as:  node <gbs-src>/out/cli/gbs-check.js <project.gbsproj>
 */
import Path from "path";
import glob from "glob";
import { readFileSync } from "fs";
import { Value } from "@sinclair/typebox/value";
import { Type, TSchema } from "@sinclair/typebox";
import initElectronL10N from "lib/lang/initElectronL10N";
import loadProject from "lib/project/loadProjectData";
import { decompressProjectResources } from "shared/lib/resources/compression";
import {
  ActorResource,
  TriggerResource,
  CompressedSceneResource,
  CompressedBackgroundResource,
  SpriteResource,
  PaletteResource,
  FontResource,
  VariablesResource,
  EngineFieldValuesResource,
  SettingsResource,
  ProjectMetadataResource,
} from "shared/lib/resources/types";
import { LATEST_PROJECT_VERSION, LATEST_PROJECT_MINOR_VERSION } from "lib/project/migration/migrateProjectResources";

const schemas: Record<string, TSchema> = {
  actor: ActorResource,
  trigger: TriggerResource,
  scene: CompressedSceneResource,
  background: CompressedBackgroundResource,
  sprite: SpriteResource,
  palette: PaletteResource,
  font: FontResource,
  variables: VariablesResource,
  engineFieldValues: EngineFieldValuesResource,
  settings: Type.Partial(SettingsResource),
};

const main = async (projectFile: string) => {
  initElectronL10N();
  const root = Path.dirname(Path.resolve(projectFile));
  const problems: string[] = [];
  const counts: Record<string, number> = {};

  const meta = JSON.parse(readFileSync(projectFile, "utf8"));
  for (const e of Value.Errors(ProjectMetadataResource, meta)) {
    problems.push(`${Path.basename(projectFile)}${e.path}: ${e.message}`);
  }

  const files = glob.sync(Path.join(root, "{project,assets}", "**/*.gbsres"));
  for (const f of files) {
    const data = JSON.parse(readFileSync(f, "utf8"));
    const type = data._resourceType;
    counts[type] = (counts[type] ?? 0) + 1;
    const schema = schemas[type];
    if (!schema) {
      problems.push(`${Path.relative(root, f)}: unknown _resourceType ${type}`);
      continue;
    }
    for (const e of Value.Errors(schema, data)) {
      problems.push(`${Path.relative(root, f)}${e.path}: ${e.message}`);
    }
  }

  const loaded = await loadProject(projectFile);
  const res = decompressProjectResources(loaded.resources);
  const settingsMissing = Object.keys(SettingsResource.properties).filter(
    (k) => !(k in JSON.parse(readFileSync(Path.join(root, "project", "settings.gbsres"), "utf8"))),
  );
  for (const scene of res.scenes) {
    if (scene.collisions.length !== scene.width * scene.height) {
      problems.push(
        `scene ${scene.name}: ${scene.collisions.length} collision bytes != ${scene.width}x${scene.height}`,
      );
    }
    const bg = res.backgrounds.find((b) => b.id === scene.backgroundId);
    if (!bg) problems.push(`scene ${scene.name}: background ${scene.backgroundId} not loaded`);
  }
  const startScene = res.scenes.find((s) => s.id === res.settings.startSceneId);
  if (!startScene) problems.push(`settings.startSceneId does not match a scene`);

  const out = {
    ok: problems.length === 0 && !loaded.isMigrated && loaded.modifiedSpriteIds.length === 0,
    latestVersion: `${LATEST_PROJECT_VERSION} r${LATEST_PROJECT_MINOR_VERSION}`,
    fileVersion: `${meta._version} r${meta._release}`,
    isMigrated: loaded.isMigrated,
    modifiedSpriteIds: loaded.modifiedSpriteIds,
    resourceCounts: counts,
    loaded: {
      scenes: res.scenes.map((s) => ({
        name: s.name,
        size: `${s.width}x${s.height}`,
        actors: s.actors.length,
        triggers: s.triggers.length,
      })),
      sprites: res.sprites.map((s) => `${s.name} (${s.width}x${s.height}, ${s.states[0]?.animationType})`),
      backgrounds: res.backgrounds.map((b) => `${b.name} (${b.imageWidth}x${b.imageHeight})`),
      startScene: startScene?.name,
      colorMode: res.settings.colorMode,
    },
    settingsKeysFilledByDefaults: settingsMissing,
    problems,
  };
  console.log(JSON.stringify(out, null, 2));
  process.exit(out.ok ? 0 : 1);
};

main(process.argv[2]).catch((e) => {
  console.error(e);
  process.exit(2);
});
