#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

NODES="${NODES:-nodes.yaml}"
NO_SYNC="${NO_SYNC:-0}"
EDITION="${EDITION:-core}"

if [[ "$EDITION" != "core" && "$EDITION" != "lab" ]]; then
  echo "edition must be one of: core, lab" >&2
  exit 2
fi

if [[ -f uv.lock ]]; then
  if [[ "$EDITION" == "lab" ]]; then
    uv sync --frozen --all-packages
  else
    uv sync --frozen
  fi
else
  if [[ "$EDITION" == "lab" ]]; then
    uv sync --all-packages
  else
    uv sync
  fi
fi

ARGS=(cluster start --nodes "$NODES")
if [[ "$NO_SYNC" == "1" ]]; then
  ARGS+=(--no-sync)
fi

uv run constella "${ARGS[@]}"
