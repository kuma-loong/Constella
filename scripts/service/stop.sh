#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PID_FILE="$ROOT_DIR/run/constella.pid"
AGENT_PID_FILE="$ROOT_DIR/run/local-agent.pid"
HIGHRES_PID_FILE="$ROOT_DIR/run/highres-sidecar.pid"

is_running() {
  local pid="$1"
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    return 1
  fi

  local state
  state="$(ps -o stat= -p "$pid" 2>/dev/null || true)"
  [[ -n "$state" && "$state" != Z* ]]
}

wait_for_exit() {
  local pid="$1"
  local attempts="$2"
  for ((i = 0; i < attempts; i++)); do
    if ! is_running "$pid"; then
      return 0
    fi
    sleep 0.2
  done
  return 1
}

stop_pid() {
  local label="$1"
  local pid_file="$2"

  if [[ ! -f "$pid_file" ]]; then
    echo "$label: not running"
    return
  fi

  local pid
  pid="$(cat "$pid_file")"
  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    echo "$label: invalid pid file: $pid_file" >&2
    rm -f "$pid_file"
    return 1
  fi

  if ! is_running "$pid"; then
    echo "$label: stale pid=$pid"
    rm -f "$pid_file"
    return
  fi

  kill -TERM "$pid"
  if ! wait_for_exit "$pid" 60; then
    echo "$label: graceful stop timed out; forcing pid=$pid"
    kill -INT "$pid" >/dev/null 2>&1 || true
  fi
  if ! wait_for_exit "$pid" 25; then
    echo "$label: force stop timed out; killing pid=$pid" >&2
    kill -KILL "$pid" >/dev/null 2>&1 || true
  fi
  if ! wait_for_exit "$pid" 25; then
    echo "$label: failed to stop pid=$pid" >&2
    return 1
  fi

  rm -f "$pid_file"
  echo "$label: stopped pid=$pid"
}

stop_pid "local agent" "$AGENT_PID_FILE"
stop_pid "highres sidecar" "$HIGHRES_PID_FILE"
stop_pid "manager" "$PID_FILE"
