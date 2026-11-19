#!/usr/bin/env bash
set -euo pipefail
umask 077

self=$(realpath "${BASH_SOURCE[0]}")
root=${CONSTELLA_RUNTIME:-$(dirname "$(dirname "$self")")}
action=${1:-}
role=${2:-}
case "$role" in
  manager|agent|watchdog|tunnel) ;;
  *) echo "Usage: $0 {start|stop|status} {manager|agent|watchdog|tunnel}" >&2; exit 2 ;;
esac
state="$root/state"
log="$root/logs/$role.log"
mkdir -p "$state" "$root/logs"
pidfile="$state/$role.pid"
command=()
case "$role" in
  manager)
    edition=${CONSTELLA_EDITION:-lab}
    command=("$root/bin/constella-manager")
    if [[ "$edition" == lab ]]; then command+=(lab); fi
    ;;
  agent) command=("$root/bin/constella-agent") ;;
  tunnel) command=("$root/bin/cloudflared" tunnel --no-autoupdate run) ;;
  watchdog) command=(bash "$root/bin/stack.sh" watch) ;;
esac

process_start() {
  local line
  IFS= read -r line < "/proc/$1/stat" || return 1
  line=${line##*) }
  local -a fields
  read -ra fields <<< "$line"
  [[ ${fields[0]} != Z ]] || return 1
  printf '%s\n' "${fields[19]}"
}

supervisor_pid() {
  local pid birth
  local -a argv
  [[ -s "$pidfile" ]] || return 1
  read -r pid birth < "$pidfile"
  [[ "$pid" =~ ^[1-9][0-9]*$ && "$birth" == "$(process_start "$pid" 2>/dev/null)" ]] || return 1
  mapfile -d '' -t argv < "/proc/$pid/cmdline" || return 1
  [[ ${argv[0]##*/} == bash && ${argv[1]:-} == "$self" && ${argv[2]:-} == supervise && ${argv[3]:-} == "$role" ]] || return 1
  printf '%s\n' "$pid"
}

load_env() {
  set -a
  source "$root/config/$1.env"
  set +a
}

run_child() {
  case "$role" in
    manager) load_env "$edition" ;;
    agent) load_env agent ;;
    tunnel)
      load_env cloudflared
      : "${TUNNEL_TOKEN:?set TUNNEL_TOKEN in config/cloudflared.env}"
      ;;
  esac
  exec "${command[@]}"
}

child_matches() {
  local pid=$1 index
  local -a argv
  mapfile -d '' -t argv < "/proc/$pid/cmdline" || return 1
  [[ ${#argv[@]} == ${#command[@]} ]] || return 1
  if [[ "$role" == watchdog ]]; then
    [[ ${argv[0]##*/} == bash ]] || return 1
  else
    [[ "$(readlink "/proc/$pid/exe")" == "$(realpath "${command[0]}")" ]] || return 1
  fi
  for ((index=1; index<${#command[@]}; index++)); do
    [[ ${argv[index]} == "${command[index]}" ]] || return 1
  done
}

supervise() {
  exec 8>"$state/$role.worker.lock"
  flock -n 8 || return 1
  local child="" birth="" rc pid="$BASHPID"
  stop_child() {
    local attempt
    if [[ -n "$child" && -n "$birth" && "$birth" == "$(process_start "$child" 2>/dev/null)" ]]; then
      for ((attempt=0; attempt<20; attempt++)); do
        if child_matches "$child" 2>/dev/null; then
          kill -TERM "$child" 2>/dev/null || true
          for ((attempt=0; attempt<80; attempt++)); do
            [[ "$birth" == "$(process_start "$child" 2>/dev/null)" ]] || break
            sleep .1
          done
          if [[ "$birth" == "$(process_start "$child" 2>/dev/null)" ]] &&
             child_matches "$child" 2>/dev/null; then
            kill -KILL "$child" 2>/dev/null || true
          fi
          break
        fi
        [[ "$birth" == "$(process_start "$child" 2>/dev/null)" ]] || break
        sleep .05
      done
    fi
    if [[ -n "$child" ]]; then wait "$child" 2>/dev/null || true; fi
    rm -f "$pidfile"
    exit 0
  }
  # Defer the stop until the child has loaded its settings and executed its binary.
  local stopping=0
  trap 'stopping=1' TERM INT
  printf '%s %s\n' "$pid" "$(process_start "$pid")" > "$pidfile.tmp"
  mv "$pidfile.tmp" "$pidfile"
  while (( !stopping )); do
    (run_child) 8>&- &
    child=$!
    birth=$(process_start "$child" 2>/dev/null || true)
    rc=0
    if (( stopping )); then stop_child; fi
    wait "$child" || rc=$?
    if (( stopping )); then stop_child; fi
    printf '%s %s exited (%s), restarting in 3 seconds\n' "$(date -Is)" "$role" "$rc"
    child=""
    sleep 3 &
    wait "$!" || true
  done
  stop_child
}

case "$action" in
  supervise) supervise ;;
  status)
    if pid=$(supervisor_pid); then
      printf '%s supervised (pid %s)\n' "$role" "$pid"
    else
      printf '%s stopped\n' "$role"
      exit 1
    fi
    ;;
  start|stop)
    exec 9>"$state/$role.action.lock"
    flock 9
    if pid=$(supervisor_pid); then
      if [[ "$action" == start ]]; then exit 0; fi
      kill -TERM "$pid"
      for ((attempt=0; attempt<120; attempt++)); do
        supervisor_pid >/dev/null || exit 0
        sleep .1
      done
      echo "$role still stopping (pid $pid), see $log" >&2
      exit 1
    fi
    if [[ "$action" == stop ]]; then exit 0; fi
    CONSTELLA_RUNTIME="$root" nohup bash "$self" supervise "$role" </dev/null >>"$log" 2>&1 9>&- &
    for ((attempt=0; attempt<30; attempt++)); do
      supervisor_pid >/dev/null && exit 0
      sleep .1
    done
    echo "$role supervisor could not start, see $log" >&2
    exit 1
    ;;
  *) echo "Unknown process action: $action" >&2; exit 2 ;;
esac
