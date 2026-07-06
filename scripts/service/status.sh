#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"
PID_FILE="$ROOT_DIR/run/constella.pid"
AGENT_PID_FILE="$ROOT_DIR/run/local-agent.pid"
HIGHRES_PID_FILE="$ROOT_DIR/run/highres-sidecar.pid"
AGENT_STATE_FILE="$ROOT_DIR/run/local-agent-state.json"

export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost}"
export no_proxy="${no_proxy:-127.0.0.1,localhost}"

print_pid_status() {
  local label="$1"
  local pid_file="$2"

  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file")"
    if kill -0 "$pid" >/dev/null 2>&1; then
      echo "$label: running pid=$pid"
    else
      echo "$label: stale pid=$pid"
    fi
  else
    echo "$label: not running"
  fi
}

print_pid_status "manager" "$PID_FILE"
print_pid_status "highres sidecar" "$HIGHRES_PID_FILE"
print_pid_status "local agent" "$AGENT_PID_FILE"

if [[ -f "$AGENT_STATE_FILE" ]]; then
  echo "local agent state:"
  cat "$AGENT_STATE_FILE"
  echo
fi

if ! command -v curl >/dev/null 2>&1; then
  exit 0
fi

echo "health:"
curl -fsS "http://$HOST:$PORT/api/health" || true
echo

SNAPSHOT="$(curl -fsS "http://$HOST:$PORT/api/cluster/snapshot" 2>/dev/null || true)"
if [[ -z "$SNAPSHOT" ]]; then
  exit 0
fi

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  "$ROOT_DIR/.venv/bin/python" - "$SNAPSHOT" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
totals = payload.get("totals", {})
nodes = payload.get("nodes", [])
print("cluster:")
print(
    "  nodes={node_count} online={online_node_count} stale={stale_node_count} "
    "offline={offline_node_count} gpus={gpu_count} processes={active_processes}".format(
        node_count=totals.get("node_count", 0),
        online_node_count=totals.get("online_node_count", 0),
        stale_node_count=totals.get("stale_node_count", 0),
        offline_node_count=totals.get("offline_node_count", 0),
        gpu_count=totals.get("gpu_count", 0),
        active_processes=totals.get("active_processes", 0),
    )
)
for node in nodes:
    node_totals = node.get("totals", {})
    print(
        "  {node_id}: status={status} gpus={gpus} source={source} seq={seq}".format(
            node_id=node.get("node_id", "?"),
            status=node.get("status", "?"),
            gpus=node_totals.get("gpu_count", 0),
            source=node.get("source", "?"),
            seq=node.get("seq", 0),
        )
    )
PY
else
  echo "cluster snapshot:"
  printf '%s\n' "$SNAPSHOT"
fi
