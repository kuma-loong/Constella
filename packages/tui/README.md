# Constella TUI

`constella-gpu-tui` installs the standalone keyboard-first Textual client. It
connects to a centrally deployed Constella service and reads the realtime
`/ws/cluster` stream and historical analytics APIs, so live monitoring and
completed workloads stay available in one terminal workspace.

Constella natively supports heterogeneous accelerator clusters. Version 0.1.3rc1
supports NVIDIA GPUs and Ascend NPUs.

## Views

- **Overview** — compact node/GPU navigation, selected-GPU processes, telemetry, and a live Braille dot curve.
- **Cluster** — complete node inventory and the selected node's hardware details.
- **Rankings** — user GPU-hours, jobs, and anomaly rankings from persisted analytics.
- **History** — all GPUs on the selected node overlaid in shared utilization and
  memory dot charts, plus a GPU-by-time utilization heatmap.
- **Performance** — compact high-resolution dot charts for SM activity, occupancy,
  Tensor Cores, DRAM bandwidth, and non-Tensor FP16/FP32/FP64 pipelines.

Rankings and History require the manager database. They show an explicit unavailable state when the manager was started with `--no-db`.

## Run

Install the standalone TUI distribution:

```bash
uv tool install "constella-gpu-tui==0.1.3rc1"
constella-tui
```

Connect to a remote manager through an SSH tunnel or a directly reachable endpoint:

```bash
constella-tui --url https://gpu.example.com
```

`CONSTELLA_URL` can provide the default manager URL.

## Keyboard

| Key | Action |
| --- | --- |
| `1` … `5` | Open Overview, Cluster, Rankings, History, or Performance |
| `Tab`, `Shift+Tab` | Move focus |
| Arrow keys, `j`, `k` | Move through nodes and table rows |
| `n`, `g` | Select the next node or GPU |
| `[`, `]` | Select the previous or next analytics time range |
| `h`, `l` | Select the previous or next Performance metric page |
| `Space` | Pause or resume Performance live refresh |
| `r` | Reconnect immediately |
| `?` | Open keyboard help |
| `q` | Quit |

The selected node and GPU survive realtime refreshes and remain shared across views. The client reconnects automatically after a dropped connection. No agent token is needed because these read-only endpoints follow the same access boundary as the Web UI.

History assigns the first eight GPUs stable, high-contrast colors spaced across
the full hue range. Press `g` to bring the next GPU to the foreground without
hiding the other GPU curves.

Performance requires the manager's `nvidia.gpm.v1` profile and high-resolution
performance cache. Unsupported accelerators continue to work in the other TUI
views and show an explicit unavailable state in Performance. The first metric
page contains four Compute and Memory charts; the second contains three
non-Tensor pipeline charts. See the
[NVIDIA GPM guide](../../docs/NVIDIA_GPM.md) for requirements, metric semantics,
storage controls, and troubleshooting.

The TUI package deliberately does not install the backend, FastAPI, Uvicorn, or
the server-side `constella` command. Use `constella-gpu` when both server and
client components are wanted on one machine.
