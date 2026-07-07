#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost}"
export no_proxy="${no_proxy:-127.0.0.1,localhost}"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"
REFRESH="${REFRESH:-1.0}"
PROCESS_REFRESH="${PROCESS_REFRESH:-5.0}"
AGENT_TOKEN_FILE="${AGENT_TOKEN_FILE:-}"
MANAGER_HOSTNAME="${MANAGER_HOSTNAME:-}"
LOCAL_AGENT="${LOCAL_AGENT:-0}"
LOCAL_AGENT_NODE_ID="${LOCAL_AGENT_NODE_ID:-}"
NODES_CONFIG="${NODES_CONFIG:-nodes.yaml}"
DB_PATH="${DB_PATH:-}"
DB_QUEUE_SIZE="${DB_QUEUE_SIZE:-1024}"
RAW_SNAPSHOT_SECONDS="${RAW_SNAPSHOT_SECONDS:-0}"
HIGHRES_SIDECAR="${HIGHRES_SIDECAR:-0}"
HIGHRES_TOKEN_FILE="${HIGHRES_TOKEN_FILE:-}"
LOG_DIR="$ROOT_DIR/logs"
RUN_DIR="$ROOT_DIR/run"
PID_FILE="$RUN_DIR/constella.pid"
AGENT_PID_FILE="$RUN_DIR/local-agent.pid"
LOG_FILE="$LOG_DIR/constella.log"
AGENT_LOG_FILE="$LOG_DIR/local-agent.log"
AGENT_STATE_FILE="$RUN_DIR/local-agent-state.json"

mkdir -p "$LOG_DIR" "$RUN_DIR"

if [[ ! -x target/release/constella ]]; then
  cargo build --release
fi

if [[ ! -d frontend/dist ]]; then
  pushd frontend >/dev/null
  if [[ -f package-lock.json ]]; then
    npm ci
  else
    npm install
  fi
  npm run build
  popd >/dev/null
fi

if [[ -z "$MANAGER_HOSTNAME" && -f "$NODES_CONFIG" ]]; then
  MANAGER_HOSTNAME="$(target/release/constella config manager-hostname --nodes "$NODES_CONFIG")"
fi

if [[ -z "$AGENT_TOKEN_FILE" && "$LOCAL_AGENT" != "0" ]]; then
  AGENT_TOKEN_FILE="$RUN_DIR/agent-token"
fi

if [[ -z "$HIGHRES_TOKEN_FILE" && "$HIGHRES_SIDECAR" == "1" ]]; then
  HIGHRES_TOKEN_FILE="$RUN_DIR/highres-token"
fi

if [[ -n "$AGENT_TOKEN_FILE" && ! -s "$AGENT_TOKEN_FILE" ]]; then
  umask 077
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 32 > "$AGENT_TOKEN_FILE"
  else
    date +%s%N | sha256sum | awk '{print $1}' > "$AGENT_TOKEN_FILE"
  fi
  chmod 600 "$AGENT_TOKEN_FILE"
fi

if [[ -n "$HIGHRES_TOKEN_FILE" && ! -s "$HIGHRES_TOKEN_FILE" ]]; then
  umask 077
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 32 > "$HIGHRES_TOKEN_FILE"
  else
    date +%s%N | sha256sum | awk '{print $1}' > "$HIGHRES_TOKEN_FILE"
  fi
  chmod 600 "$HIGHRES_TOKEN_FILE"
fi

if [[ -n "$AGENT_TOKEN_FILE" ]]; then
  export CONSTELLA_AGENT_TOKEN_FILE="$AGENT_TOKEN_FILE"
fi

if [[ -n "$HIGHRES_TOKEN_FILE" ]]; then
  export CONSTELLA_HIGHRES_TOKEN_FILE="$HIGHRES_TOKEN_FILE"
fi

if [[ -n "$MANAGER_HOSTNAME" ]]; then
  export CONSTELLA_MANAGER_HOSTNAME="$MANAGER_HOSTNAME"
fi

if [[ -z "$LOCAL_AGENT_NODE_ID" && -n "$MANAGER_HOSTNAME" ]]; then
  LOCAL_AGENT_NODE_ID="$MANAGER_HOSTNAME"
fi

if [[ -n "$DB_PATH" ]]; then
  export CONSTELLA_DB_PATH="$DB_PATH"
  export CONSTELLA_DB_QUEUE_SIZE="$DB_QUEUE_SIZE"
  export CONSTELLA_RAW_SNAPSHOT_SECONDS="$RAW_SNAPSHOT_SECONDS"
fi

CMD=(
  "$ROOT_DIR/target/release/constella"
  serve
  --host "$HOST"
  --port "$PORT"
  --refresh "$REFRESH"
  --process-refresh "$PROCESS_REFRESH"
)

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" >/dev/null 2>&1; then
    echo "manager already running: pid=$PID url=http://$HOST:$PORT"
  else
    rm -f "$PID_FILE"
  fi
fi

if [[ ! -f "$PID_FILE" ]]; then
  if command -v setsid >/dev/null 2>&1; then
    nohup setsid "${CMD[@]}" >"$LOG_FILE" 2>&1 &
  else
    nohup "${CMD[@]}" >"$LOG_FILE" 2>&1 &
  fi
  echo "$!" > "$PID_FILE"
  echo "manager started: pid=$(cat "$PID_FILE") url=http://$HOST:$PORT log=$LOG_FILE"
fi

if [[ "$HIGHRES_SIDECAR" == "1" ]]; then
  echo "highres sidecar is no longer started separately; Rust manager serves highres APIs and stream"
fi

if [[ "$LOCAL_AGENT" == "0" ]]; then
  echo "local agent disabled"
  exit 0
fi

AGENT_CMD=(
  "$ROOT_DIR/target/release/constella"
  agent
  --manager-url "ws://$HOST:$PORT/api/agents/ws"
  --token-file "$AGENT_TOKEN_FILE"
  --refresh "$REFRESH"
  --process-refresh "$PROCESS_REFRESH"
  --state-file "$AGENT_STATE_FILE"
)

if [[ -n "$LOCAL_AGENT_NODE_ID" ]]; then
  AGENT_CMD+=(--node-id "$LOCAL_AGENT_NODE_ID")
fi

if [[ -f "$AGENT_PID_FILE" ]]; then
  AGENT_PID="$(cat "$AGENT_PID_FILE")"
  if kill -0 "$AGENT_PID" >/dev/null 2>&1; then
    echo "local agent already running: pid=$AGENT_PID state=$AGENT_STATE_FILE"
    exit 0
  fi
  rm -f "$AGENT_PID_FILE"
fi

if command -v setsid >/dev/null 2>&1; then
  nohup setsid "${AGENT_CMD[@]}" >"$AGENT_LOG_FILE" 2>&1 &
else
  nohup "${AGENT_CMD[@]}" >"$AGENT_LOG_FILE" 2>&1 &
fi
echo "$!" > "$AGENT_PID_FILE"
echo "local agent started: pid=$(cat "$AGENT_PID_FILE") log=$AGENT_LOG_FILE state=$AGENT_STATE_FILE"
exit 0
