#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $# != 3 ]]; then
  echo "Usage: $0 RUNTIME_DIR AGENT_ARCHIVE MANAGER_OR_LAB_ARCHIVE" >&2
  exit 2
fi
runtime=$(realpath -m "$1")
agent=$(realpath "$2")
manager=$(realpath "$3")
agent_name=$(basename "$agent" .tar.gz)
manager_name=$(basename "$manager" .tar.gz)
platform=${agent_name#constella-agent-}
case "$manager_name" in
  "constella-manager-$platform"|"constella-lab-$platform") ;;
  *) echo "Select Agent and Manager packages for the same version and architecture" >&2; exit 1 ;;
esac
[[ "$agent_name" == "constella-agent-$platform" && "$platform" == *-linux-"$(uname -m)" ]]
for file in bin/constella-agent bin/constella-manager bin/stack.sh bin/process.sh "packages/$agent_name" "packages/$manager_name"; do
  if [[ -e "$runtime/$file" || -L "$runtime/$file" ]]; then
    echo "Runtime entry already exists: $runtime/$file" >&2
    exit 1
  fi
done
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
tar -xf "$agent" -C "$stage"
tar -xf "$manager" -C "$stage"
test -x "$stage/$agent_name/constella-agent"
test -x "$stage/$manager_name/constella-manager"
test -r "$stage/$manager_name/web/index.html"
mkdir -p "$runtime"/{bin,config,packages,data,logs}
cp -r "$stage/$agent_name" "$stage/$manager_name" "$runtime/packages/"
ln -s "../packages/$agent_name/constella-agent" "$runtime/bin/constella-agent"
ln -s "../packages/$manager_name/constella-manager" "$runtime/bin/constella-manager"
install -m 700 "$stage/$manager_name/ops/"{stack,process}.sh "$runtime/bin/"
printf '%s\n' "Installed binaries and frontend in $runtime" "Configure files under $runtime/config and run bin/stack.sh check"
