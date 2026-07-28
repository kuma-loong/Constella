# Constella Backend

`constella-gpu-backend` is the standalone Constella service package. It provides
the manager, local and remote accelerator agents, SQLite workload history,
high-resolution job curves, and the HTTP/WebSocket API without installing a
user interface.

Constella natively supports heterogeneous accelerator clusters. Version 0.1.2
supports NVIDIA GPUs through NVML with an `nvidia-smi` fallback, and Ascend NPUs
through DCMI with an `npu-smi` fallback.

## Install and run

```bash
pip install constella-gpu-backend
constella service start --no-local-agent
```

Start a local NVIDIA or Ascend agent with the service:

```bash
constella service start --device nvidia
constella service start --device ascend
```

The backend exposes its API documentation at `/docs` and OpenAPI schema at
`/openapi.json`. This package intentionally contains no Web or TUI frontend.

Other distributions are available for the [Web UI](https://pypi.org/project/constella-gpu-web/),
[TUI](https://pypi.org/project/constella-gpu-tui/), and
[full installation](https://pypi.org/project/constella-gpu/).

Source and documentation: [github.com/kuma-loong/Constella](https://github.com/kuma-loong/Constella)
