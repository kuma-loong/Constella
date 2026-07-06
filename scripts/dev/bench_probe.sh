#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

COUNT="${COUNT:-10}"

if [[ ! -x target/release/constella ]]; then
  cargo build --release
fi

target/release/constella probe --count "$COUNT" --no-processes
