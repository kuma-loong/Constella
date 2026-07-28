# PyPI CLI Usage

This document covers the CLI intended for `pip install constella-gpu` users. Source deployments can keep using `scripts/service/*`.

## Recommended One-Command Service

Start a full local service stack:

```bash
constella service start
```

Default behavior:

- Starts the manager on `127.0.0.1:8765`.
- Creates `run/agent-token` if needed.
- Enables SQLite at `run/constella.db`.
- Starts a local NVIDIA agent connected to the manager; use `--device ascend` on Ascend hosts.
- Writes logs to `logs/`.
- Writes PID and runtime state files to `run/`.

For a user-level install, prefer explicit run and log directories:

```bash
constella service start \
  --host 0.0.0.0 \
  --port 8765 \
  --run-dir ~/.constella/run \
  --log-dir ~/.constella/logs
```

Inspect and stop:

```bash
constella service status
constella service stop
```

## Terminal UI

Open the realtime terminal client after the manager is running:

```bash
constella tui
# equivalent dedicated entry point
constella-tui
```

Both commands connect to `http://127.0.0.1:8765` by default and consume the
existing read-only `/ws/cluster` stream and analytics APIs. They do not start
another collector or write to the manager.

| Parameter | Default | Description |
| --- | --- | --- |
| `--url` | `CONSTELLA_URL` or `http://127.0.0.1:8765` | Manager HTTP, HTTPS, WS, or WSS URL. |
| `--reconnect-delay` | `2.0` | Delay between automatic reconnect attempts. |

Examples:

```bash
constella tui --url https://gpu.example.com
CONSTELLA_URL=http://10.0.0.10:8765 constella-tui
```

Use `1`–`4` to switch between Overview, Cluster, Rankings, and History. `n` and
`g` move to the next node or GPU, `[` and `]` change analytics range, `r`
reconnects, and `?` opens keyboard help. Rankings and historical charts require
SQLite (enabled by `constella service start` unless `--no-db` is set). See
[`tui/README.md`](../tui/README.md) for the complete TUI notes.

## `constella service start`

Starts the manager and optional helper processes.

| Parameter | Default | Description |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Manager bind host. Use `0.0.0.0` when remote agents must connect directly. |
| `--port` | `8765` | Manager HTTP/WebSocket port. |
| `--refresh` | `1.0` | GPU metric refresh interval. Must be one of the supported collector intervals. |
| `--process-refresh` | `5.0` | Process sampling interval. |
| `--graceful-timeout` | `10.0` | Maximum graceful shutdown time for manager and sidecar processes. |
| `--log-level` | `info` | Uvicorn log level for managed server processes. |
| `--run-dir` | `run` | Directory for PID files, token files, local agent state, and the default database. |
| `--log-dir` | `logs` | Directory for manager, local agent, and sidecar logs. |
| `--no-local-agent` | off | Do not start the local GPU agent. |
| `--local-agent-node-id` | host-derived | Display node ID for the local agent. |
| `--local-agent-manager-url` | `ws://127.0.0.1:<port>/api/agents/ws` | Manager WebSocket URL used by the local agent. |
| `--device` | `nvidia` | Local agent backend: `nvidia` (NVML/`nvidia-smi`) or `ascend` (DCMI/`npu-smi`). |
| `--manager-hostname` | unset | Manager hostname label exposed to the app and used as local node ID when no local node ID is set. |
| `--agent-token-file` | `<run-dir>/agent-token` | Agent token file. Created automatically when missing. |
| `--db-path` | `<run-dir>/constella.db` | SQLite database path. Enabled by default. |
| `--no-db` | off | Disable SQLite history and analytics. |
| `--db-queue-size` | `1024` | Async database sink queue size. |
| `--raw-snapshot-seconds` | `0.0` | Raw snapshot persistence interval. `0.0` disables raw snapshot persistence. |
| `--frontend-dir` | package frontend assets | Override frontend dist directory. Rarely needed for PyPI users. |
| `--highres-sidecar` | off | Start the high-resolution sidecar. Requires the default DB or `--db-path`. |
| `--highres-host` | `127.0.0.1` | High-resolution sidecar bind host. |
| `--highres-port` | `8766` | High-resolution sidecar port. |
| `--highres-token-file` | `<run-dir>/highres-token` | High-resolution stream token file. Created automatically when needed. |
| `--highres-manager-stream-url` | `ws://127.0.0.1:<port>/api/highres/stream` | Manager high-resolution stream URL. |
| `--highres-retention-seconds` | sidecar default | In-memory high-resolution sample retention window. |
| `--cluster-nodes` | unset | Start remote agents from a nodes YAML file after manager startup. |
| `--cluster-no-sync` | off | Do not sync the remote agent runtime before remote start. |
| `--wait-timeout` | `10.0` | Seconds to wait for the manager health endpoint before starting helpers. |

Examples:

```bash
constella service start --host 0.0.0.0 --port 8765
constella service start --run-dir ~/.constella/run --log-dir ~/.constella/logs
constella service start --local-agent-node-id HGX-H100
constella service start --device ascend
constella service start --no-local-agent
constella service start --db-path /data/constella.db
constella service start --no-db
constella service start --highres-sidecar --highres-port 8766
constella service start --cluster-nodes nodes.yaml
```

## `constella service status`

Shows PID-based status for the managed local processes.

| Parameter | Default | Description |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Included for symmetry with start; PID status is based on `--run-dir`. |
| `--port` | `8765` | Included for symmetry with start. |
| `--run-dir` | `run` | Directory containing PID files. |
| `--log-dir` | `logs` | Directory containing logs. |
| `--cluster-nodes` | unset | Also check remote agents from the nodes YAML file. |

Example:

```bash
constella service status --run-dir ~/.constella/run --log-dir ~/.constella/logs
```

## `constella service stop`

Stops local managed processes in this order: local agent, high-resolution sidecar, manager.

| Parameter | Default | Description |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Included for symmetry with start; stop is based on PID files. |
| `--port` | `8765` | Included for symmetry with start. |
| `--run-dir` | `run` | Directory containing PID files. |
| `--log-dir` | `logs` | Directory containing logs. |
| `--cluster-nodes` | unset | Nodes YAML file for remote agents. |
| `--stop-cluster` | off | Also stop remote agents from `--cluster-nodes`. |

Examples:

```bash
constella service stop
constella service stop --cluster-nodes nodes.yaml --stop-cluster
```

## Remote Nodes

Create a nodes file:

```yaml
manager_hostname: manager-node
manager_url: ws://10.0.0.10:8765/api/agents/ws
agent_token_file: run/agent-token
refresh_interval: 1.0
process_interval: 5.0
remote_base: $HOME/.constella
nodes:
  - id: gpu-node-01
    host: gpu-node-01
    user: alice
    device: nvidia
  - id: ascend-node-01
    host: ascend-node-01
    user: alice
    device: ascend
```

Start manager, local agent, SQLite, and remote agents in one command:

```bash
constella service start \
  --host 0.0.0.0 \
  --port 8765 \
  --cluster-nodes nodes.yaml
```

Remote nodes do not need `uv`. The manager syncs a minimal agent runtime, including both the NVIDIA and Ascend backends, and starts `python3 -m constella.agent_main` through SSH.

## Fine-Grained Commands

The `service` command is the recommended PyPI entry point. Lower-level commands remain available for supervisors, debugging, and custom process managers.

### `constella serve`

Runs only the manager web service.

| Parameter | Default | Description |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Bind host. |
| `--port` | `8765` | HTTP/WebSocket port. |
| `--refresh` | `1.0` | Manager refresh interval broadcast to agents. |
| `--process-refresh` | `5.0` | Process refresh interval broadcast to agents. |
| `--graceful-timeout` | `10.0` | Maximum graceful shutdown time. |
| `--agent-token-file` | unset | Enables authenticated agent ingest. |
| `--highres-token-file` | unset | Token file for high-resolution stream clients. |
| `--db-path` | unset | Enables SQLite history and analytics. |
| `--db-queue-size` | app default | Async database queue size. |
| `--raw-snapshot-seconds` | app default | Raw snapshot persistence interval. |
| `--frontend-dir` | package frontend assets | Override frontend dist directory. |
| `--log-level` | `info` | Uvicorn log level. |

### `constella agent`

Runs one local GPU node agent.

| Parameter | Default | Description |
| --- | --- | --- |
| `--node-id` | host-derived | Node ID shown in the UI. |
| `--manager-url` | env required | Manager WebSocket URL, for example `ws://127.0.0.1:8765/api/agents/ws`. |
| `--token-file` | unset | Agent token file. Required unless `CONSTELLA_AGENT_TOKEN` or `CONSTELLA_AGENT_TOKEN_FILE` is set. |
| `--refresh` | env/default | Sampling refresh interval. |
| `--process-refresh` | env/default | Process sampling interval. |
| `--state-file` | `~/.constella/run/agent-state.json` | Private JSON state file. |
| `--device` | `nvidia` or `CONSTELLA_DEVICE_TYPE` | Backend selection: `nvidia` or `ascend`. |

### `constella probe`

Prints one JSON accelerator snapshot. Use `--device ascend` to select DCMI with
`npu-smi` fallback; the default is `nvidia`.

```bash
constella probe --device ascend --pretty
```

### `constella highres-sidecar`

Runs the optional high-resolution sidecar.

| Parameter | Default | Description |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Sidecar bind host. |
| `--port` | `8766` | Sidecar port. |
| `--db-path` | env required | SQLite database path. |
| `--manager-stream-url` | `ws://127.0.0.1:8765/api/highres/stream` | Manager high-resolution stream URL. |
| `--token-file` | unset | Token file for manager stream authentication. |
| `--retention-seconds` | sidecar default | In-memory high-resolution retention. |
| `--graceful-timeout` | `10.0` | Maximum graceful shutdown time. |
| `--log-level` | `info` | Uvicorn log level. |

### `constella cluster`

Controls remote agents over SSH.

| Command | Parameter | Default | Description |
| --- | --- | --- | --- |
| `cluster start` | `--nodes` | `nodes.yaml` | Nodes YAML file. |
| `cluster start` | `--no-sync` | off | Skip syncing the remote runtime. |
| `cluster status` | `--nodes` | `nodes.yaml` | Nodes YAML file. |
| `cluster stop` | `--nodes` | `nodes.yaml` | Nodes YAML file. |

### `constella db`

Maintains SQLite history data.

| Command | Important Parameters | Description |
| --- | --- | --- |
| `db maintain` | `--path`, `--raw-retention-seconds`, `--session-stale-seconds` | Routine maintenance: close stale sessions, roll up metrics, prune old data. |
| `db rollup` | `--path`, `--from-bucket-seconds`, `--to-bucket-seconds` | Roll up metric buckets. |
| `db migrate-samples` | `--path`, `--bucket-seconds` | One-time migration from legacy raw GPU samples. |
| `db prune-rollups` | `--path`, `--bucket-seconds` | Delete expired rollups. |
| `db prune-raw` | `--path`, `--retention-seconds` | Delete expired raw snapshots. |
| `db close-sessions` | `--path`, `--stale-seconds` | Close long-unseen running process sessions. |

Typical maintenance command:

```bash
constella db maintain --path run/constella.db
```

## Notes

- `constella service start` is idempotent for local managed processes: if a PID file points to a running process, it reports that process instead of starting a duplicate.
- `service stop` uses PID files under `--run-dir`; use the same `--run-dir` used for start.
- Keep `--run-dir` and `--log-dir` outside temporary directories for long-running deployments.
- Use `--host 0.0.0.0` when remote nodes connect directly to the manager over the network.
