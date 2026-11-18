#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
output="${1:-$root/target/constella-release}"
mkdir -p "$output"
output="$(cd "$output" && pwd)"
stage="$(mktemp -d "$output/.stage.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT

cd "$root"
if [[ "$(uname -s)" != Linux ]]; then
    echo "Build Constella packages on Linux" >&2
    exit 1
fi
arch="$(uname -m)"
target="${CONSTELLA_RUST_TARGET:-${CARGO_BUILD_TARGET:-}}"
cargo_args=()
release="$root/target/release"
if [[ -n "$target" ]]; then
    case "$target" in
        x86_64-unknown-linux-gnu|aarch64-unknown-linux-gnu) ;;
        *) echo "Unsupported Linux package target: $target" >&2; exit 1 ;;
    esac
    arch="${target%%-*}"
    cargo_args+=(--target "$target")
    release="$root/target/$target/release"
fi
cargo build --release --locked --workspace --target-dir "$root/target" "${cargo_args[@]}"
agent="$release/constella-agent"
manager="$release/constella-manager"
version="$(cargo metadata --no-deps --locked --format-version 1 | node -e '
    const packages = JSON.parse(require("node:fs").readFileSync(0, "utf8")).packages;
    const names = ["constella-agent", "constella-manager"];
    const versions = names.map(name => packages.find(pkg => pkg.name === name)?.version);
    if (!versions[0] || versions[0] !== versions[1]) {
        console.error("Agent and Manager versions must match");
        process.exit(1);
    }
    process.stdout.write(versions[0]);
')"

platform="$version-linux-$arch"
agent_name="constella-agent-$platform"
manager_name="constella-manager-$platform"
lab_name="constella-lab-$platform"

mkdir -p "$stage/$agent_name" "$stage/$manager_name" "$stage/$lab_name"
install -m 0755 "$agent" "$stage/$agent_name/constella-agent"
install -m 0755 "$manager" "$stage/$manager_name/constella-manager"
install -m 0755 "$manager" "$stage/$lab_name/constella-manager"
for package in "$agent_name" "$manager_name" "$lab_name"; do
    install -m 0644 LICENSE docs/RUST_DEPLOYMENT.md "$stage/$package/"
done
install -m 0644 scripts/service/rust/constella-agent.service "$stage/$agent_name/"
install -m 0644 scripts/service/rust/constella-manager.service "$stage/$manager_name/"
install -m 0644 scripts/service/rust/constella-lab.service "$stage/$lab_name/"

(
    cd frontend
    npm ci
    CONSTELLA_FRONTEND_MODE=community CONSTELLA_FRONTEND_OUT_DIR="$stage/$manager_name/web" npm run build
    CONSTELLA_FRONTEND_MODE=lab CONSTELLA_FRONTEND_OUT_DIR="$stage/$lab_name/web" npm run build
)

packages=("$agent_name" "$manager_name" "$lab_name")
archives=()
for package in "${packages[@]}"; do
    archive="$package.tar.gz"
    tar -C "$stage" -czf "$stage/$archive" "$package"
    archives+=("$archive")
done
(
    cd "$stage"
    sha256sum "${archives[@]}" > "SHA256SUMS-$platform"
)
for archive in "${archives[@]}" "SHA256SUMS-$platform"; do
    mv -- "$stage/$archive" "$output/$archive"
    printf '%s\n' "$output/$archive"
done
