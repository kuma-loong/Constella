#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

NODES="${NODES:-nodes.yaml}"
NO_SYNC="${NO_SYNC:-0}"

if [[ ! -x target/release/constella ]]; then
  cargo build --release
fi

ARGS=(cluster start --nodes "$NODES")
if [[ "$NO_SYNC" == "1" ]]; then
  ARGS+=(--no-sync)
fi

target/release/constella "${ARGS[@]}"
