#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  backup|backup-lab) ;;
  *) echo "Usage: $0 {backup|backup-lab} DEST" >&2; exit 2 ;;
esac
exec "$(dirname "${BASH_SOURCE[0]}")/../service/stack.sh" "$@"
