# Constella TUI

Constella TUI is a keyboard-first terminal client for an existing Constella manager. It reads the same realtime `/ws/cluster` stream and analytics APIs as the Web UI, so sampling, aggregation, authentication boundaries, and cluster state remain in the backend.

## Views

- **Overview** — compact node/GPU navigation, selected-GPU processes, telemetry, and a live Braille dot curve.
- **Cluster** — complete node inventory and the selected node's hardware details.
- **Rankings** — user GPU-hours, jobs, and anomaly rankings from persisted analytics.
- **History** — utilization and memory dot curves plus a GPU-by-time utilization heatmap.

Rankings and History require the manager database. They show an explicit unavailable state when the manager was started with `--no-db`.

## Run

After installing `constella-gpu`:

```bash
constella tui
# or
constella-tui
```

Connect to a remote manager through an SSH tunnel or a directly reachable endpoint:

```bash
constella tui --url http://127.0.0.1:8765
constella-tui --url https://gpu.example.com
```

`CONSTELLA_URL` can provide the default manager URL.

## Keyboard

| Key | Action |
| --- | --- |
| `1` … `4` | Open Overview, Cluster, Rankings, or History |
| `Tab`, `Shift+Tab` | Move focus |
| Arrow keys, `j`, `k` | Move through nodes and table rows |
| `n`, `g` | Select the next node or GPU |
| `[`, `]` | Select the previous or next analytics time range |
| `r` | Reconnect immediately |
| `?` | Open keyboard help |
| `q` | Quit |

The selected node and GPU survive realtime refreshes and remain shared across views. The client reconnects automatically after a dropped connection. No agent token is needed because these read-only endpoints follow the same access boundary as the Web UI.
