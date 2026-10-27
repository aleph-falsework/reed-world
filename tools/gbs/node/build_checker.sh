#!/usr/bin/env bash
# Build <gbs-src>/out/cli/gbs-check.js (GB Studio loader + schema check).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
GBS_SRC="${GBS_SRC:-$(cd "$HERE/../../../.." && pwd)/gbs-src}"   # default: next to the repo checkout
mkdir -p "$GBS_SRC/src/apps/gbs-check"
cp "$HERE/check_project.ts" "$GBS_SRC/src/apps/gbs-check/check_project.ts"
cd "$GBS_SRC"
GBS_SRC="$GBS_SRC" NO_TYPE_CHECKING=1 node_modules/.bin/webpack --config "$HERE/webpack.check.config.js"
echo "built $GBS_SRC/out/cli/gbs-check.js"
