<p align="center">
  <img src="frontend/public/logo-readme.svg" alt="Constella logo" width="260">
</p>

<h1 align="center">Constella</h1>

<div align="center">
  <blockquote>
    <em>Like stars in a constellation, <strong>Constella</strong> gathers independent GPU nodes into one observable cluster.</em>
  </blockquote>
</div>

<br>

<div align="center" id="constella-badges">

[![Rust](https://img.shields.io/badge/rust-1.80%2B-B7410E?logo=rust&logoColor=white)](https://www.rust-lang.org/)
[![NVIDIA NVML](https://img.shields.io/badge/NVIDIA-NVML-76B900?logo=nvidia&logoColor=white)](https://docs.nvidia.com/deploy/nvml-api/)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/kuma-loong/Constella)

</div>

<p align="center">English | <a href="README_zh.md">简体中文</a></p>

Lightweight realtime NVIDIA GPU monitoring for one server or a small GPU cluster. Every GPU node, including the manager host when local monitoring is enabled, runs the same Rust agent path: native NVML sampling, `nvidia-smi` fallback, `/proc` process enrichment, and WebSocket sample ingest into the manager.

## Features

- Realtime NVIDIA GPU monitoring for a single server or small cluster, with a modular architecture and optional components.
- Low-overhead sampling: one persistent sampler per GPU node, current-point agent payloads, manager-side realtime history, and no per-browser GPU polling.
- Rich GPU and process telemetry: utilization, memory, power, temperature, clocks, P-state, ECC, MIG, process memory, runtime, users, PIDs, and command fingerprints.
- High-performance agent sampling path: one persistent NVML handle per agent, `nvidia-smi` fallback, `/proc` command enrichment, selectable refresh rates, and lower-cadence process sampling to reduce jitter.
- User-level deployment with no sudo or system service required; optional SQLite history is available when persisted metrics are needed.
- Optional analytics dashboards for weighted GPU hours, job rankings, low-utilization reservations, off-hour activity, per-node trends, and range-aware heatmaps.
- Standard APIs for custom frontends, dashboards, and automation.

## Layout

```text
src/                    Rust backend, agent, cluster manager, sampler, API/WebSocket
frontend/               Vite + TypeScript frontend
scripts/                categorized service, cluster, tunnel, maintenance, and dev scripts
docs/                   design and operations notes
tests/                  unit tests
```

## Quick Start

```bash
cd Constella
./scripts/service/setup.sh
./scripts/service/start.sh
```

By default this starts the manager only. The manager listens on `127.0.0.1:8765`. Use SSH forwarding from your local machine:

```bash
ssh -N -L 8765:127.0.0.1:8765 <user>@<server>
```

Then open:

```text
http://127.0.0.1:8765/overview
```

Set `LOCAL_AGENT=1` when the manager host should also run a local GPU agent:

```bash
LOCAL_AGENT=1 ./scripts/service/start.sh
```

## Cluster Mode

`scripts/service/start.sh` creates `run/agent-token` automatically when the local agent is enabled. To provide your own token file:

```bash
mkdir -p run
umask 077
printf '%s\n' 'replace-with-a-random-token' > run/agent-token
chmod 600 run/agent-token
AGENT_TOKEN_FILE=run/agent-token ./scripts/service/start.sh
```

Create `nodes.yaml` from the example and edit hosts/users:

```bash
cp docs/nodes.example.yaml nodes.yaml
```

Set `manager_hostname` to the local manager-host agent label you want in the UI. `scripts/service/start.sh` uses it as the default `LOCAL_AGENT_NODE_ID`.

Start, inspect, and stop remote agents:

```bash
./scripts/cluster/start.sh
./scripts/cluster/status.sh
./scripts/cluster/stop.sh
```

`constella cluster start` uses SSH only for setup/control. The remote agent token is written through stdin into `~/.constella/run/agent.env` with mode `600`; it is not placed on the remote command line.

Remote GPU nodes do not need `uv` or a Python runtime. `constella cluster start` syncs the local `target/release/constella` binary to `~/.constella/agent/bin/constella`; the remote start script runs `constella agent`. Restart all agents after upgrading the manager so every node uses the current sample protocol.

## Optional Components

- SQLite history is disabled by default. Enable it only when persisted GPU/task history and analytics dashboards are needed: [SQLite History](docs/HISTORY.md).
- Cloudflare Tunnel is an optional deployment path for domain access without opening an inbound server port: [Cloudflare Tunnel](docs/CLOUD_TUNNEL.md).

## Commands

```bash
./scripts/service/status.sh
./scripts/service/stop.sh
HOST=127.0.0.1 PORT=8765 REFRESH=1.0 PROCESS_REFRESH=5.0 ./scripts/service/start.sh
LOCAL_AGENT=0 ./scripts/service/start.sh
target/release/constella probe --pretty
target/release/constella agent --manager-url ws://127.0.0.1:8765/api/agents/ws --token-file run/agent-token
target/release/constella cluster start --nodes nodes.yaml
target/release/constella cluster status --nodes nodes.yaml
target/release/constella cluster stop --nodes nodes.yaml
COUNT=20 ./scripts/dev/bench_probe.sh
```

## API

- `GET /api/health`
- `GET /api/cluster/snapshot`
- `GET /api/settings`
- `PATCH /api/settings`
- `WS /ws/cluster`
- `WS /api/agents/ws`
- `GET /api/history/gpu`
- `GET /api/history/tasks`
- `GET /api/users`
- `GET /api/analytics/overview`
- `GET /api/analytics/node/{node_id}`
- `GET /api/highres/status`
- `GET /api/highres/jobs`
- `GET /api/highres/jobs/{job_key}`
- `GET /api/highres/jobs/{job_key}/gpu`
- `WS /api/highres/stream`
When SQLite is not enabled, history, analytics, and job curve search APIs return `enabled:false`; realtime cluster monitoring continues through `/api/cluster/snapshot` and `/ws/cluster`.

Deprecated single-node endpoints are intentionally not compatibility layers: `GET /api/snapshot` returns `410 Gone`, and `WS /ws/gpu` closes immediately. Use the cluster API for local and remote nodes.

## Development

```bash
cargo test
cargo build --release

cd frontend
npm install
npm run build
```

Frontend dev server:

```bash
cd frontend
npm run dev
```

For production, build `frontend/dist`; the Rust manager serves the static frontend directly.

SQLite ingest runs through a bounded background writer. Tune it with `DB_QUEUE_SIZE` and `RAW_SNAPSHOT_SECONDS` when using `scripts/service/start.sh`; set `HIGHRES_TOKEN_FILE` if `/api/highres/stream` should require bearer-token access.

## License

[MIT](LICENSE)
