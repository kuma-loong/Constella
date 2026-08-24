# NVIDIA GPM performance monitoring

<p align="center">English | <a href="NVML_GPM_METRICS_zh.md">简体中文</a></p>

Constella 0.1.3 adds NVML GPM-based performance monitoring for supported NVIDIA
GPUs. It provides a Web performance workspace, a TUI performance view,
high-resolution in-memory curves, and optional long-term SQLite rollups. The GPM
collection path is isolated from base NVML monitoring, so unavailable metrics or
collection failures do not interrupt regular GPU status and process monitoring.

## Where to use it

No additional configuration is required after the service starts. Each NVIDIA
agent automatically probes the GPM capability of every GPU.

- Web UI: open `http://127.0.0.1:8765/performance`. Select a node and one or more
  GPUs, then inspect a 5-minute, 15-minute, 1-hour, or 2-hour range. Drag across
  a chart to recalculate its average, minimum, maximum, P95, sample count, and
  coverage for that interval.
- TUI: run `constella tui` or `constella-tui` and press `5`. Use `n`/`g` to
  change the node or GPU, `[`/`]` to change the time range, `h`/`l` to change
  the metric page, and Space to pause or resume live updates.
- API: `GET /api/highres/performance` returns device performance curves and
  summaries. `GET /api/highres/jobs/{job_key}/performance` returns the device
  performance curves for a job.

The Performance view requires the node to advertise the `nvidia.gpm.v1` profile
and the high-resolution performance cache to be enabled. When either requirement
is unavailable, the UI shows an explicit capability state. Overview, Cluster,
History, and the other views continue to work.

## Requirements

- An NVIDIA GPU, a driver that exposes NVML GPM, and a device/metric combination
  reported as supported by that driver.
- Constella uses device-level GPM metrics. It does not require root privileges
  and does not start a per-kernel profiler.
- The verified environment is eight NVIDIA H100 80GB HBM3 GPUs with driver
  580.65.06. Other hardware and drivers are determined by runtime probing; do
  not infer support from the GPU model alone.
- Ascend agents do not initialize the NVIDIA provider. Unsupported NVIDIA GPUs
  continue to use the base NVML or `nvidia-smi` fallback path.

## Metric semantics

The `gpu` value from `nvmlDeviceGetUtilizationRates` is the percentage of the
sample window during which at least one kernel was executing. Its `memory` value
is the percentage of that window during which global device memory was being
read or written. Neither value is SM utilization or DRAM bandwidth utilization,
and neither can distinguish FP16, FP8, or Tensor Core activity.

Hopper and newer architectures can expose NVML GPM. Constella requests the
following device-level interval averages:

| Constella metric ID | GPM metric | Meaning |
| --- | --- | --- |
| `nvidia.gpm.sm_active` | `NVML_GPM_METRIC_SM_UTIL` | Percentage of SMs that were busy |
| `nvidia.gpm.sm_occupancy` | `NVML_GPM_METRIC_SM_OCCUPANCY` | Active warps relative to the theoretical maximum |
| `nvidia.gpm.tensor_active` | `NVML_GPM_METRIC_ANY_TENSOR_UTIL` | Activity from any Tensor operation |
| `nvidia.gpm.dram_bw_active` | `NVML_GPM_METRIC_DRAM_BW_UTIL` | DRAM bandwidth relative to the theoretical maximum |
| `nvidia.gpm.fp64_non_tensor_active` | `NVML_GPM_METRIC_FP64_UTIL` | Non-Tensor FP64 activity |
| `nvidia.gpm.fp32_non_tensor_active` | `NVML_GPM_METRIC_FP32_UTIL` | Non-Tensor FP32 activity |
| `nvidia.gpm.fp16_non_tensor_active` | `NVML_GPM_METRIC_FP16_UTIL` | Non-Tensor FP16 activity |
| `nvidia.gpm.pcie_tx_per_second` | `NVML_GPM_METRIC_PCIE_TX_PER_SEC` | PCIe traffic sent from the GPU in MiB/s |
| `nvidia.gpm.pcie_rx_per_second` | `NVML_GPM_METRIC_PCIE_RX_PER_SEC` | PCIe traffic received by the GPU in MiB/s |
| `nvidia.gpm.nvlink_tx_per_second` | `NVML_GPM_METRIC_NVLINK_TOTAL_TX_PER_SEC` | Aggregate NVLink transmit bandwidth in MiB/s |
| `nvidia.gpm.nvlink_rx_per_second` | `NVML_GPM_METRIC_NVLINK_TOTAL_RX_PER_SEC` | Aggregate NVLink receive bandwidth in MiB/s |

These percentages are interval averages for the whole GPU. They are not
instantaneous values and cannot be attributed directly to a process, CUDA
kernel, or source line. The same average can represent a small subset of SMs
remaining busy or all SMs becoming busy for a shorter period. The metrics are
suited to finding underused devices, phase changes, and multi-GPU imbalance. Use
Nsight Systems or Nsight Compute when an investigation requires kernel-level
attribution. GPU models also have different peak throughput and bandwidth, so
the same percentage does not imply the same FLOPS or bytes per second.

### SM Activity

SM Activity describes how busy the GPU's streaming multiprocessors were during
the sample interval. A higher value means that more SMs had active warps for a
larger part of the interval. "Active" does not necessarily mean that a warp was
making computational progress: a warp waiting for a memory request can still be
active. High SM Activity is therefore one prerequisite for effective GPU use,
but it does not by itself prove that the compute pipelines are saturated. Read
it together with Tensor, FP16/32/64, and DRAM activity to determine what work the
GPU was performing.

### SM Occupancy

SM Occupancy is the number of resident warps on an SM relative to the maximum
number the hardware can support. It describes how much parallel work the GPU can
keep resident at once. Block size, registers per thread, shared memory per block,
and GPU architecture all affect occupancy. Higher occupancy can help hide memory
latency, but it does not always improve performance; a compute-bound kernel may
reach peak throughput at a lower occupancy. Treat this metric as an explanation
of available parallelism rather than a performance score.

### Tensor Activity

Tensor Activity is the percentage of time the GPU's SMs spent performing any
Tensor operation. It helps verify that a workload is using Tensor Cores. A high
value usually indicates sustained Tensor Core matrix computation. A low value
does not necessarily mean that the GPU is underused because input preparation,
communication, regular CUDA Core work, or operators that do not map to Tensor
Cores may dominate the interval. If a workload is expected to rely heavily on
Tensor Cores but this value remains near zero, inspect its data types, matrix
shapes, and framework configuration.

### DRAM Bandwidth Utilization

DRAM Bandwidth Utilization is the percentage of the GPU's theoretical maximum
device-memory bandwidth that was used. It measures how busy the memory transfer
path was, not how much memory capacity was allocated. Sustained high DRAM
bandwidth with only moderate SM or compute-pipeline activity can indicate a
memory-bandwidth-bound workload. Confirm that diagnosis with application
throughput and a developer profiler.

### FP16 Non-Tensor Activity

FP16 Non-Tensor Activity is the percentage of time the SMs spent performing
half-precision arithmetic outside Tensor Cores. It excludes Tensor operations
such as HMMA, so low FP16 activity together with high Tensor activity is normal
for many mixed-precision workloads. A high value means that ordinary CUDA
compute pipelines are handling substantial FP16 work. Compare it with Tensor
Activity to distinguish standard FP16 arithmetic from Tensor Core-accelerated
matrix operations.

### FP32 Non-Tensor Activity

FP32 Non-Tensor Activity is the percentage of time the SMs spent performing
single-precision arithmetic outside Tensor Cores. A high value indicates
substantial FP32 work on regular CUDA Cores, which is common in traditional CUDA
kernels, models that do not use Tensor Cores, and some preprocessing or
postprocessing operators. A low value does not mean that the GPU is idle: the
workload may instead be dominated by Tensor, FP16, FP64, integer, or memory work.

### FP64 Non-Tensor Activity

FP64 Non-Tensor Activity is the percentage of time the SMs spent performing
double-precision arithmetic outside Tensor Cores. FP64 is common in scientific
computing, numerical simulation, and other precision-sensitive HPC workloads,
but is usually low in conventional AI training and inference. A high value
indicates sustained use of double-precision compute resources. Peak FP64
capability varies greatly across GPU models, so do not use the percentage alone
to compare their actual compute performance.

### PCIe and NVLink bandwidth

PCIe and NVLink counters report directional traffic in MiB/s rather than a
percentage. PCIe TX/RX describes traffic leaving and entering the GPU over
PCIe. NVLink TX/RX is aggregated across all links reported by NVML. Constella
probes all four counters with the first valid GPM sample pair, then removes any
counter that returns `NVML_ERROR_NOT_SUPPORTED` from that device's subsequent
requests. A supported counter with value zero remains visible because an idle
link is different from an unsupported link.

NVML GPM does not expose a dedicated FP8 utilization metric. FP8 Tensor Core
work contributes to generic Tensor Activity, but GPM alone cannot identify
whether that activity used FP8, BF16, FP16, or another Tensor data type. Use a
short, on-demand CUPTI or Nsight Compute profiling session when that distinction
is required. Per-kernel profiling should not be part of a resident high-frequency
monitoring path.

## Collection model

1. Probe each device once with `nvmlGpmQueryDeviceSupport`. An unsupported device
   silently remains on the existing metric path.
2. Allocate two sample buffers for every supported GPU with
   `nvmlGpmSampleAlloc`.
3. Add `nvmlGpmSampleGet` to the existing collector tick without creating a new
   thread or blocking for the sample interval.
4. Starting with the second tick, call `nvmlGpmMetricsGet` with two adjacent
   samples and then swap the buffers.
5. Release both buffers with `nvmlGpmSampleFree` when the sampler closes.

NVIDIA requires more than 100 ms between the two samples. Constella's minimum
refresh interval is 500 ms, so it does not add a sleep. Metric structures and
sample buffers are reused, and a GPM failure never fails the base NVML snapshot.

Runtime support is probed automatically. Set `CONSTELLA_NVML_GPM=off` to disable
GPM collection completely. With SQLite enabled,
`CONSTELLA_NVIDIA_GPM_ROLLUP=off` disables only persistent GPM rollups; base GPU
rollups, realtime monitoring, and Ascend NPU collection are unaffected. Set
`CONSTELLA_NVIDIA_GPM_HIGHRES=off` to disable only the in-memory performance
ring buffers.

## Retention and controls

The data follows three independently controlled paths:

| Path | Default | Contents | Control |
| --- | --- | --- | --- |
| Realtime collection | Runtime probe | Device-level GPM values in agent snapshots | Disable with `CONSTELLA_NVML_GPM=off` |
| High-resolution memory cache | Enabled | Recent raw curves and validity masks | Disable with `CONSTELLA_NVIDIA_GPM_HIGHRES=off` |
| SQLite rollups | Enabled with the database | 20-second, 2-minute, and 1-hour summaries | Disable with `CONSTELLA_NVIDIA_GPM_ROLLUP=off` |

The high-resolution cache retains two hours by default and preallocates a fixed
capacity for the minimum 500 ms sample interval. Performance arrays for eight
GPUs reserve approximately 5.93 MiB. Raw high-frequency performance samples are
not written to SQLite. GPM rollups use a separate table but the same retention
tiers as base GPU rollups.

All three controls affect NVIDIA GPM only. They do not disable base NVML, the
`nvidia-smi` fallback, Ascend collection, or realtime cluster snapshots.

## Verification and troubleshooting

First verify that the agent reports the GPM profile and state:

```bash
uv run constella probe --pretty
curl -s http://127.0.0.1:8765/api/cluster/snapshot
curl -s http://127.0.0.1:8765/api/highres/status
```

Check that the node's `performance_profiles` contains `nvidia.gpm.v1`, then
inspect each GPU's performance state:

- `warming`: the collector has just started and does not yet have two samples
  separated by the required interval. Wait for the next collection tick.
- `available`: GPM metrics are available.
- `unsupported`: the current device, driver, or metric combination does not
  support GPM. Base monitoring continues.
- `error`: the current GPM call failed. Inspect `logs/local-agent.log` or the
  corresponding remote agent log.

If the page reports that the cache is disabled, inspect
`CONSTELLA_NVIDIA_GPM_HIGHRES` and the independent sidecar's
`/api/highres/status`. If realtime curves work but persistent summaries do not,
check `DB_PATH` and `CONSTELLA_NVIDIA_GPM_ROLLUP`. If `probe` reports
`nvidia-smi` as the base data source, NVML itself is unavailable and GPM samples
cannot be produced.

## Local validation

The original seven compute and memory metrics were validated on eight NVIDIA
H100 80GB HBM3 GPUs with driver 580.65.06. The interconnect counters use the
same GPM request and per-metric support result, but still require validation
under each target topology and driver. Across
12 read-only collection rounds on eight GPUs, a full GPM round averaged about
11 ms; each GPU's `SampleGet` averaged about 1.06 ms and `MetricsGet` about
0.30 ms. These measurements indicate scale only. Re-run
`scripts/dev/bench_probe.sh` under the target driver and workload.

A shadow database generated with the seven-metric schema for eight GPUs and the
full retention window contained 657,600 rows and occupied 147.15 MiB, or about
234.6 bytes per row. A seven-day, 20-second-granularity range query for one GPU
covered 30,240 rows and took about 91 ms. This local SQLite benchmark supports
capacity planning but predates the four interconnect columns and does not
replace shadow-write observation in the target deployment.

## References

- [NVML GPM metrics](https://docs.nvidia.com/deploy/nvml-api/group__nvmlGpmEnums.html)
- [NVML GPM call sequence](https://docs.nvidia.com/deploy/nvml-api/group__nvmlGpmFunctions.html)
- [NVML utilization structure semantics](https://docs.nvidia.com/deploy/nvml-api/structnvmlUtilization__t.html)
- [DCGM low-overhead profiling](https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/profiling.html)
- [NVIDIA Fleet Intelligence metric rationale](https://docs.nvidia.com/fleet-intel/data-collection-rationale/)
- [Nsight Compute profiling overhead](https://docs.nvidia.com/nsight-compute/ProfilingGuide/index.html)
