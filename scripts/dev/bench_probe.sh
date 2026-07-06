#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

COUNT="${COUNT:-10}"

if [[ ! -x target/release/constella ]]; then
  cargo build --release
fi

elapsed=()
last_snapshot=""
for _ in $(seq 1 "$COUNT"); do
  start_ns="$(date +%s%N)"
  last_snapshot="$(target/release/constella probe)"
  end_ns="$(date +%s%N)"
  elapsed+=("$(( (end_ns - start_ns) / 1000000 ))")
  sleep 0.05
done

sorted="$(printf '%s\n' "${elapsed[@]}" | sort -n)"
sum=0
for value in "${elapsed[@]}"; do
  sum=$((sum + value))
done
avg=$((sum / COUNT))
p95_index=$(( (COUNT * 95 + 99) / 100 ))
p95="$(printf '%s\n' "$sorted" | sed -n "${p95_index}p")"

source="$(printf '%s' "$last_snapshot" | sed -n 's/.*"source":"\([^"]*\)".*/\1/p')"
gpu_count="$(printf '%s' "$last_snapshot" | grep -o '\"index\":' | wc -l | tr -d ' ')"

echo "samples=$COUNT"
echo "source=${source:-unknown} gpu_count=$gpu_count"
echo "avg_ms=$avg p95_ms=${p95:-0}"
