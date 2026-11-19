#!/usr/bin/env bash
set -euo pipefail
umask 077

here=$(dirname "$(realpath "${BASH_SOURCE[0]}")")
default_root=$(dirname "$here")
if [[ -f "$here/../../Cargo.toml" ]]; then default_root=$(realpath "$here/../../run"); fi
root=$(realpath -m "${CONSTELLA_RUNTIME:-$default_root}")
export CONSTELLA_RUNTIME="$root"
process="$root/bin/process.sh"
action=${1:-}
case "$action" in
  check|start|stop|status|watch|cluster-start|cluster-status|cluster-stop|tunnel-start|tunnel-status|tunnel-stop) shift ;;
  backup|backup-lab) shift ;;
  *) echo "Usage: $0 {check|start [--with-tunnel]|status|stop|backup DEST|backup-lab DEST}" >&2; exit 2 ;;
esac
with_tunnel=false
if [[ "$action" == start && ${1:-} == --with-tunnel ]]; then with_tunnel=true; shift; fi
if [[ "$action" == backup || "$action" == backup-lab ]]; then
  [[ $# == 1 ]] || { echo "Provide one backup destination" >&2; exit 2; }
  source "$root/config/stack.env"
  set -a
  source "$root/config/${CONSTELLA_EDITION:-lab}.env"
  exec "$root/bin/constella-manager" "$action" "$1"
fi
[[ $# == 0 ]] || { echo "Unexpected arguments" >&2; exit 2; }

source "$root/config/stack.env"
export CONSTELLA_EDITION=${CONSTELLA_EDITION:-lab}
remote_instance=${CONSTELLA_REMOTE_INSTANCE:-rust-manager}
interval=${CONSTELLA_WATCHDOG_INTERVAL_SECONDS:-300}
[[ "$CONSTELLA_EDITION" == lab || "$CONSTELLA_EDITION" == community ]]
[[ "$remote_instance" =~ ^[a-zA-Z0-9._-]+$ && "$remote_instance" != . && "$remote_instance" != .. ]]
[[ "$interval" =~ ^[1-9][0-9]*$ ]]
remote_root="\$HOME/.local/state/constella/$remote_instance"
ssh_args=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=6 -o ServerAliveInterval=4 -o ServerAliveCountMax=2)
nodes=()
hosts=()
devices=()
while read -r node host device extra; do
  [[ -z "$node" || "$node" == \#* ]] && continue
  if [[ ! "$node" =~ ^[a-zA-Z0-9._-]+$ || ! "$host" =~ ^[a-zA-Z0-9_][a-zA-Z0-9._@-]*$ || -n "$extra" || ! "$device" =~ ^(nvidia|ascend)$ ]]; then
    echo "Invalid node inventory row: $node" >&2
    exit 1
  fi
  for existing in "${nodes[@]}"; do
    if [[ "$existing" == "$node" ]]; then echo "Duplicate node: $node" >&2; exit 1; fi
  done
  for existing in "${hosts[@]}"; do
    if [[ "$existing" == "$host" ]]; then echo "Duplicate SSH host: $host" >&2; exit 1; fi
  done
  nodes+=("$node")
  hosts+=("$host")
  devices+=("$device")
done < "$root/config/nodes.tsv"

manager_healthy() {
  local body
  body=$(curl --noproxy '*' --max-time 3 -fsS "$CONSTELLA_HEALTH_URL") || return 1
  [[ "$body" =~ \"ok\"[[:space:]]*:[[:space:]]*true ]]
}

remote_action() {
  local host=$1 operation=$2
  ssh "${ssh_args[@]}" "$host" "bin=\"$remote_root/bin/process.sh\"; if [ -x \"\$bin\" ]; then \"\$bin\" $operation agent; elif [ $operation = status ]; then echo 'agent not installed'; exit 1; fi"
}

local_settings() (
  source "$root/config/$CONSTELLA_EDITION.env"
  test -s "${CONSTELLA_AGENT_TOKEN_FILE:?}" || { echo "Agent token file is missing" >&2; exit 1; }
  test -r "${CONSTELLA_FRONTEND_DIST:?}/index.html" || { echo "Manager frontend is missing" >&2; exit 1; }
  test -w "$(dirname "${CONSTELLA_DB_PATH:?}")" || { echo "Telemetry directory is not writable" >&2; exit 1; }
  if [[ "$CONSTELLA_EDITION" == lab ]]; then
    test -w "$(dirname "${CONSTELLA_LAB_DB_PATH:?}")" || { echo "Identity directory is not writable" >&2; exit 1; }
  fi
  source "$root/config/agent.env"
  [[ -s "${CONSTELLA_AGENT_TOKEN_FILE:?}" ]]
)

check() {
  command -v curl >/dev/null
  command -v flock >/dev/null
  [[ -x "$root/bin/constella-manager" && -x "$root/bin/constella-agent" &&
     -x "$process" && -x "$root/bin/stack.sh" ]] || {
    echo "Install the Manager, Agent and service scripts in $root/bin" >&2; return 1;
  }
  local_settings
  local architecture host expected
  expected=$(uname -m)
  for host in "${hosts[@]}"; do
    architecture=$(ssh "${ssh_args[@]}" "$host" 'command -v flock >/dev/null && command -v bash >/dev/null && command -v tar >/dev/null && uname -m')
    if [[ "$architecture" != "$expected" ]]; then
      echo "$host is $architecture. This runtime ships an Agent for $expected." >&2
      return 1
    fi
    printf '%s SSH and Agent architecture verified (%s)\n' "$host" "$architecture"
  done
}

remote_group() {
  local i failed=0
  for i in "${!nodes[@]}"; do
    printf '%s (%s) ' "${nodes[i]}" "${hosts[i]}"
    remote_action "${hosts[i]}" "$1" || failed=1
  done
  return "$failed"
}

check_tunnel() {
  [[ -x "$root/bin/cloudflared" && -s "$root/config/cloudflared.env" ]] || {
    echo "Missing cloudflared binary or environment file" >&2; return 1;
  }
}

deploy_agent() (
  local node=$1 host=$2 device=$3 stage
  stage=$(mktemp -d)
  trap 'rm -rf -- "$stage"' EXIT
  mkdir -p "$stage/bin" "$stage/config"
  install -m 700 "$process" "$root/bin/constella-agent" "$stage/bin/"
  source "$root/config/agent.env"
  install -m 600 "$CONSTELLA_AGENT_TOKEN_FILE" "$stage/config/agent-token"
  {
    printf 'CONSTELLA_NODE_ID=%q\nCONSTELLA_DEVICE_TYPE=%q\n' "$node" "$device"
    printf 'CONSTELLA_MANAGER_URL=%q\n' "$CONSTELLA_CLUSTER_URL"
    printf 'CONSTELLA_REFRESH_SECONDS=%q\nCONSTELLA_PROCESS_SECONDS=%q\n' "${CONSTELLA_REFRESH_SECONDS:-1}" "${CONSTELLA_PROCESS_SECONDS:-5}"
    printf 'CONSTELLA_AGENT_TOKEN_FILE=%s\n' "\"$remote_root/config/agent-token\""
    if [[ -n "${CONSTELLA_NVML_GPM:-}" ]]; then
      printf 'CONSTELLA_NVML_GPM=%q\n' "$CONSTELLA_NVML_GPM"
    fi
  } > "$stage/config/agent.env"
  tar -C "$stage" -cf - . | ssh "${ssh_args[@]}" "$host" "set -eu; umask 077; dir=\"$remote_root\"; mkdir -p \"\$dir\"; tar -xf - -C \"\$dir\"; \"\$dir/bin/process.sh\" start agent"
)

start_agents() {
  local i failed=0
  for i in "${!nodes[@]}"; do
    if remote_action "${hosts[i]}" status; then continue; fi
    if "$1" "${nodes[i]}" "${hosts[i]}" "${devices[i]}"; then
      printf '%s supervised\n' "${nodes[i]}"
    else
      printf '%s failed to start\n' "${nodes[i]}" >&2
      failed=1
    fi
  done
  return "$failed"
}

restart_agent() {
  local node=$1 host=$2
  remote_action "$host" start
  remote_action "$host" status >/dev/null
}

case "$action" in
  start|stop|cluster-start|cluster-stop|tunnel-start|tunnel-stop)
    mkdir -p "$root/state"
    exec 7>"$root/state/stack.lock"
    flock 7
    ;;
esac

case "$action" in
  check) check ;;
  cluster-start)
    check
    manager_healthy || { echo "Manager is unavailable" >&2; exit 1; }
    start_agents deploy_agent 7>&-
    "$process" start watchdog 7>&-
    ;;
  cluster-status) remote_group status ;;
  cluster-stop)
    "$process" stop watchdog
    remote_group stop
    ;;
  tunnel-start)
    check_tunnel
    "$process" start tunnel 7>&-
    ;;
  tunnel-status|tunnel-stop) "$process" "${action#tunnel-}" tunnel ;;
  start)
    check
    if "$with_tunnel"; then check_tunnel; fi
    if ! "$process" status manager >/dev/null &&
       curl --noproxy '*' --max-time 3 -sS -o /dev/null "$CONSTELLA_HEALTH_URL" 2>/dev/null; then
      echo "A Manager already responds at $CONSTELLA_HEALTH_URL" >&2; exit 1
    fi
    "$process" start manager 7>&-
    for ((i=0; i<60; i++)); do
      manager_healthy 2>/dev/null && break
      sleep .25
    done
    manager_healthy
    "$process" start agent 7>&-
    local_result=0
    start_agents deploy_agent 7>&- || local_result=$?
    "$process" start watchdog 7>&-
    if ((local_result)); then exit "$local_result"; fi
    if "$with_tunnel"; then "$process" start tunnel 7>&-; fi
    ;;
  watch)
    timer=
    trap 'if [[ -n "$timer" && "$(cat "/proc/$timer/comm" 2>/dev/null)" == sleep ]]; then kill -TERM "$timer" 2>/dev/null || true; fi; exit 0' INT TERM
    while :; do
      if manager_healthy 2>/dev/null; then
        start_agents restart_agent || true
      else
        printf '%s Manager unavailable, skipping remote checks\n' "$(date -Is)"
      fi
      sleep "$interval" &
      timer=$!
      wait "$timer" || true
      timer=
    done
    ;;
  status|stop)
    failed=0
    "$process" "$action" watchdog || failed=1
    if [[ "$action" == stop ]]; then "$process" stop tunnel || failed=1; fi
    remote_group "$action" || failed=1
    for role in agent manager; do "$process" "$action" "$role" || failed=1; done
    if [[ "$action" == status ]]; then
      manager_healthy || failed=1
      "$process" status tunnel || true
    fi
    exit "$failed"
    ;;
esac
