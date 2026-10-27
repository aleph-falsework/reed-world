/* Bundle check_project.ts with GB Studio's own CLI webpack config (aliases,
 * loaders). build_checker.sh first copies check_project.ts into
 * <gbs-src>/src/apps/gbs-check/ so GB Studio's tsconfig applies to it.
 */
const path = require("path");
const gbsSrc = process.env.GBS_SRC || "/home/claude/gbs-src";
const cli = require(path.join(gbsSrc, "src/apps/gb-studio-cli/webpack.cli.config.js"));

module.exports = {
  ...cli,
  entry: { check_project: path.join(gbsSrc, "src/apps/gbs-check/check_project.ts") },
  // Must live in out/cli: src/consts.ts finds appData/ and buildTools/ by
  // looking for "out/cli" in __dirname.
  output: {
    filename: "gbs-check.js",
    path: path.join(gbsSrc, "out", "cli"),
    publicPath: cli.output.publicPath,
  },
  plugins: cli.plugins, // GB Studio defines (paths, VERSION) live here
  resolve: {
    ...cli.resolve,
    modules: [path.join(gbsSrc, "node_modules"), "node_modules"],
  },
  resolveLoader: { modules: [path.join(gbsSrc, "node_modules")] },
};
