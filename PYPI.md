# Constella

`constella-gpu` is the complete Constella distribution: backend services, the
production Web interface, and the keyboard-first Textual TUI.

Constella is a lightweight accelerator monitoring and workload history platform
for standalone servers and small heterogeneous clusters. It shows current
accelerator activity in realtime, records workload history automatically, and
makes completed training and inference jobs easy to review without requiring a
Prometheus/Grafana stack.

Version 0.1.2 supports:

- NVIDIA GPUs: NVML with an `nvidia-smi` fallback.
- Ascend NPUs: DCMI with an `npu-smi` fallback.

## Install

Constella requires Python 3.10 or newer.

```bash
pip install constella-gpu
```

Four distributions provide explicit deployment sizes:

| Distribution | Backend/API | Web UI | TUI |
| --- | :---: | :---: | :---: |
| `constella-gpu` | Yes | Yes | Yes |
| `constella-gpu-web` | Yes | Yes | No |
| `constella-gpu-tui` | Yes | No | Yes |
| `constella-gpu-backend` | Yes | No | No |

## Quick start

Start the manager, SQLite history, and a local NVIDIA agent:

```bash
constella service start \
  --run-dir ~/.constella/run \
  --log-dir ~/.constella/logs
```

On an Ascend host, add `--device ascend`.

Use the terminal interface:

```bash
constella tui
# equivalent standalone entry point
constella-tui
```

Or open `http://127.0.0.1:8765/overview` in a browser.

Inspect or stop the managed processes with the same runtime directory:

```bash
constella service status --run-dir ~/.constella/run --log-dir ~/.constella/logs
constella service stop --run-dir ~/.constella/run --log-dir ~/.constella/logs
```

Constella binds to `127.0.0.1` by default. Keep that default for local access,
or use SSH port forwarding when the service runs on a remote host.

## Common commands

```bash
constella probe --pretty
constella probe --device ascend --pretty
constella serve --host 127.0.0.1 --port 8765
constella service start --no-local-agent
constella cluster start --nodes nodes.yaml
constella cluster status --nodes nodes.yaml
constella cluster stop --nodes nodes.yaml
```

SQLite history is enabled by default. Use `constella service start --no-db` for
realtime-only operation, or `constella service start --highres-sidecar` to run
the optional high-resolution job-curve sidecar.

## Project links

- [Source repository](https://github.com/kuma-loong/Constella)
- [Installed CLI reference](https://github.com/kuma-loong/Constella/blob/main/docs/PYPI_CLI.md)
- [Operations guide](https://github.com/kuma-loong/Constella/blob/main/docs/OPERATIONS.md)
- [Cluster manifest example](https://github.com/kuma-loong/Constella/blob/main/docs/nodes.example.yaml)
- [Issue tracker](https://github.com/kuma-loong/Constella/issues)
- [MIT license](https://github.com/kuma-loong/Constella/blob/main/LICENSE)
