# Constella

`constella-gpu` is the PyPI distribution for Constella, a lightweight Web-based
accelerator monitoring and workload history platform for standalone servers and
small clusters.

Constella natively supports heterogeneous accelerator clusters across multiple
hardware backends. It shows current accelerator activity in realtime, records
workload history automatically, and makes completed training and inference jobs
easy to review without requiring a Prometheus/Grafana stack.

The current release supports:

- NVIDIA GPUs: NVML with `nvidia-smi` fallback.
- Ascend NPUs: DCMI with `npu-smi` fallback.

The wheel includes the production Web interface and the `constella` command, so
an installed package does not require a source checkout or a separate frontend
build.

## Install

Constella requires Python 3.10 or newer.

```bash
pip install constella-gpu
```

The distribution name is `constella-gpu`; the installed command and Python
package are both named `constella`.

## Quick start

Start the manager, SQLite history, and a local NVIDIA agent:

```bash
constella service start \
  --run-dir ~/.constella/run \
  --log-dir ~/.constella/logs
```

On an Ascend host, select the backend explicitly:

```bash
constella service start \
  --device ascend \
  --run-dir ~/.constella/run \
  --log-dir ~/.constella/logs
```

Open `http://127.0.0.1:8765/overview` in a browser.

Inspect or stop the managed processes with the same runtime directory:

```bash
constella service status --run-dir ~/.constella/run --log-dir ~/.constella/logs
constella service stop --run-dir ~/.constella/run --log-dir ~/.constella/logs
```

Constella binds to `127.0.0.1` by default. Keep that default for local access,
or use SSH port forwarding when the service runs on a remote host.

## Common commands

Print one accelerator snapshot:

```bash
constella probe --pretty
constella probe --device ascend --pretty
```

Run only the manager for a custom supervisor or a manager-only node:

```bash
constella serve --host 127.0.0.1 --port 8765
constella service start --no-local-agent
```

Manage agents defined in a cluster manifest:

```bash
constella cluster start --nodes nodes.yaml
constella cluster status --nodes nodes.yaml
constella cluster stop --nodes nodes.yaml
```

## Optional history and job curves

`constella service start` enables SQLite history by default. Disable it for a
realtime-only deployment:

```bash
constella service start --no-db
```

The high-resolution job-curve sidecar is optional:

```bash
constella service start --highres-sidecar
```

## Project links

- [Source repository](https://github.com/kuma-loong/Constella)
- [Installed CLI reference](https://github.com/kuma-loong/Constella/blob/main/docs/PYPI_CLI.md)
- [Operations guide](https://github.com/kuma-loong/Constella/blob/main/docs/OPERATIONS.md)
- [Cluster manifest example](https://github.com/kuma-loong/Constella/blob/main/docs/nodes.example.yaml)
- [Issue tracker](https://github.com/kuma-loong/Constella/issues)
- [MIT license](https://github.com/kuma-loong/Constella/blob/main/LICENSE)
