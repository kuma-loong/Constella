# Constella Web

`constella-gpu-web` installs the Constella backend and its production Web
interface. It provides realtime accelerator monitoring, automatic workload
history, job curves, and multi-user analytics in a browser.

Constella natively supports heterogeneous accelerator clusters. Version 0.1.3rc1
supports NVIDIA GPUs through NVML with an `nvidia-smi` fallback, and Ascend NPUs
through DCMI with an `npu-smi` fallback.

## Install and run

```bash
pip install constella-gpu-web
constella service start
```

Open `http://127.0.0.1:8765/overview`. On an Ascend host, start the service with
`constella service start --device ascend`.

This distribution contains no Textual TUI. Use
[`constella-gpu`](https://pypi.org/project/constella-gpu/) for both frontends,
[`constella-gpu-tui`](https://pypi.org/project/constella-gpu-tui/) for a
terminal-focused installation, or
[`constella-gpu-backend`](https://pypi.org/project/constella-gpu-backend/) for
the API service alone.

Source and documentation: [github.com/kuma-loong/Constella](https://github.com/kuma-loong/Constella)
