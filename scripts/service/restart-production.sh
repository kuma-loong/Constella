#!/usr/bin/env bash
set -euo pipefail
umask 077

here=$(dirname "$(realpath "${BASH_SOURCE[0]}")")
runtime=$(realpath -m "${CONSTELLA_RUNTIME:-$(dirname "$here")}")
export CONSTELLA_RUNTIME="$runtime"
stack="$runtime/bin/stack.sh"

if [[ $# -gt 1 ]]; then echo "Usage: $0 [--status]" >&2; exit 2; fi
case "${1:-}" in
  --help|-h) echo "Usage: $0 [--status]"; exit 0 ;;
  ''|--status) ;;
  *) echo "Usage: $0 [--status]" >&2; exit 2 ;;
esac
source "$runtime/config/stack.env"
for name in http_proxy https_proxy all_proxy no_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY; do
  unset "$name"
done
expected=$(awk 'NF && $1 !~ /^#/ { n++ } END { print n+1 }' "$runtime/config/nodes.tsv")
health_url=${CONSTELLA_HEALTH_URL:?}
request_health() {
  curl --noproxy '*' --max-time 4 -fsS "$health_url"
}
field() {
  local key=$1 value=$2
  if [[ "$value" =~ \"$key\"[[:space:]]*:[[:space:]]*([0-9]+) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
  else
    return 1
  fi
}
verify() {
  local response online before after metric
  "$stack" status || return 1
  "$stack" tunnel-status || return 1
  response=$(request_health) || return 1
  [[ "$response" =~ \"ok\"[[:space:]]*:[[:space:]]*true ]] || return 1
  online=$(field online_node_count "$response") || return 1
  if [[ "$online" != "$expected" ]]; then
    printf '在线节点 %s/%s\n' "$online" "$expected" >&2
    return 1
  fi
  before=$(field written_samples "$response") || return 1
  sleep 2
  response=$(request_health) || return 1
  after=$(field written_samples "$response") || return 1
  (( after > before )) || { echo 'SQLite 入库计数未增加' >&2; return 1; }
  metric=$(sed -nE 's/.*Starting metrics server on ([^ ]+).*/\1/p' "$runtime/logs/tunnel.log" | tail -1)
  [[ "$metric" =~ ^127\.0\.0\.1:[0-9]+/metrics$ ]] || return 1
  curl --noproxy '*' -fsS --max-time 4 "http://$metric" |
    awk '$1=="cloudflared_tunnel_ha_connections" && $2>0 {ok=1} END {exit !ok}' || return 1
  printf '在线节点 %s/%s，GPU %s，SQLite 正在入库，Tunnel 已连接\n' "$online" "$expected" "$(field gpu_count "$response")"
}

if [[ ${1:-} == --status ]]; then
  verify
  exit
fi

mkdir -p "$runtime/state" "$runtime/logs"
exec 9>"$runtime/state/restart-production.lock"
flock -n 9 || { echo '已有恢复流程正在运行' >&2; exit 1; }
log="$runtime/logs/restart-production.log"
printf '\n[%s] 开始完整恢复\n' "$(date -Is)" | tee -a "$log"
run() {
  local label=$1 limit=$2
  shift 2
  printf '%s\n' "$label" | tee -a "$log"
  timeout --foreground "$limit" "$@" 9>&- 2>&1 | tee -a "$log"
}
run '检查配置与远端 SSH' 90 "$stack" check
run '停止整套服务' 180 "$stack" stop
run '启动 Manager、Agent、看门狗及 Tunnel' 180 "$stack" start --with-tunnel
for ((attempt=0; attempt<15; attempt++)); do
  if verify 9>&- 2>&1 | tee -a "$log"; then exit 0; fi
  sleep 3
done
printf '恢复流程已执行，部分组件未通过核验。日志：%s\n' "$log" >&2
exit 1
