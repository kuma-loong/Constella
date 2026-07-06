#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PID_FILE="$ROOT_DIR/run/constella.pid"
AGENT_PID_FILE="$ROOT_DIR/run/local-agent.pid"

stop_pid() {
  local label="$1"
  local pid_file="$2"

  if [[ ! -f "$pid_file" ]]; then
    echo "$label: not running"
    return
  fi

  local pid
  pid="$(cat "$pid_file")"
  if kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid"
    for _ in {1..30}; do
      if ! kill -0 "$pid" >/dev/null 2>&1; then
        break
      fi
      sleep 0.2
    done
    if kill -0 "$pid" >/dev/null 2>&1; then
      echo "$label: still stopping pid=$pid"
    else
      echo "$label: stopped pid=$pid"
    fi
  else
    echo "$label: stale pid=$pid"
  fi

  rm -f "$pid_file"
}

stop_pid "local agent" "$AGENT_PID_FILE"
stop_pid "manager" "$PID_FILE"
