#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

rm -rf dist packages/web/src/constella_web/dist

pushd frontend >/dev/null
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi
npm run build:package
popd >/dev/null

uv build --all-packages --out-dir dist

echo "built local distributions:"
ls -1 dist
