#!/usr/bin/env bash
# Build GB Studio 4.3.2's CLI headlessly on Linux x64 (no Electron GUI).
# Result: $GBS_SRC/out/cli/gb-studio-cli.js (ROM/web builds) and
#         $GBS_SRC/out/cli/gbs-check.js   (strict loader/schema check).
# Takes ~3 minutes on a fresh machine. Needs node 22, corepack, git, curl, tar.
set -euo pipefail

GBS_SRC="${GBS_SRC:-/home/claude/gbs-src}"
GBS_TAG="v4.3.2"
HERE="$(cd "$(dirname "$0")" && pwd)"
# sha256 of gbdk-linux64.tar.gz pinned in GB Studio 4.3.2's buildTools/dependencies.lock
GBDK_SHA256="d7857a5f6d135ee4c249043ca26aad9f2ec8ab5d4106d97720d404114f42605c"

if [ ! -d "$GBS_SRC/.git" ]; then
  git clone --depth 1 --branch "$GBS_TAG" https://github.com/chrismaltby/gb-studio "$GBS_SRC"
fi
cd "$GBS_SRC"
git -c advice.detachedHead=false checkout -q "$GBS_TAG"
git submodule update --init --recursive --depth 1   # appData/engine/gbvm

# 1. GBDK-2020 toolchain into buildTools/linux-x64/gbdk (what fetch-deps does).
#    The repo pins ".../releases/latest/download/..." plus a checksum, so
#    verify the download matches the lock before using it.
if [ ! -x buildTools/linux-x64/gbdk/bin/lcc ]; then
  tmp="$(mktemp -d)"
  curl -sSL -o "$tmp/gbdk.tgz" \
    https://github.com/gbdk-2020/gbdk-2020/releases/latest/download/gbdk-linux64.tar.gz
  echo "$GBDK_SHA256  $tmp/gbdk.tgz" | sha256sum -c -
  tar -zxf "$tmp/gbdk.tgz" -C buildTools/linux-x64
  rm -rf "$tmp"
fi

# 2. JS dependencies with the exact Yarn (4.4.1, from packageManager) and the
#    exact lockfile. Yarn 1 cannot read this lockfile and would re-resolve.
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
export COREPACK_HOME="${COREPACK_HOME:-$HOME/.corepack}"
export COREPACK_NPM_REGISTRY="${COREPACK_NPM_REGISTRY:-https://registry.npmjs.org}"  # repo.yarnpkg.com may be blocked
export ELECTRON_SKIP_BINARY_DOWNLOAD=1
corepack yarn install --immutable

# 3. The CLI imports "electron" (for settings/dialogs it never calls in a
#    build). In plain node, require("electron") only needs path.txt to exist;
#    electron.app is then undefined and every caller guards for that.
[ -f node_modules/electron/path.txt ] || printf "electron-not-installed" > node_modules/electron/path.txt

# 4. Bundle the CLI (package.json "make:cli"; webpack is not a direct dep, so
#    call the binary rather than `yarn make:cli`).
NO_TYPE_CHECKING=1 node_modules/.bin/webpack --config ./src/apps/gb-studio-cli/webpack.cli.config.js

# 5. Strict checker (GB Studio loader + TypeBox schemas).
GBS_SRC="$GBS_SRC" "$HERE/node/build_checker.sh"

node out/cli/gb-studio-cli.js --version
echo "GB Studio CLI ready: $GBS_SRC/out/cli/gb-studio-cli.js"
