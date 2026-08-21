# NVML 硬件执行单元监控调研

## 结论

`nvmlDeviceGetUtilizationRates` 返回的 `gpu` 是采样窗口内“至少一个 kernel 在执行”的
时间比例，`memory` 是全局显存发生读写的时间比例。它们不能解释为 SM 利用率或显存带宽
利用率，也不能区分 FP16、FP8 或 Tensor Core。

Hopper 及更新架构可使用 NVML GPM。Constella 请求以下设备级区间平均值：

| Constella 指标 ID | GPM metric | 含义 |
| --- | --- | --- |
| `nvidia.gpm.sm_active` | `NVML_GPM_METRIC_SM_UTIL` | 忙碌 SM 的比例 |
| `nvidia.gpm.sm_occupancy` | `NVML_GPM_METRIC_SM_OCCUPANCY` | 活跃 warp 相对理论上限的比例 |
| `nvidia.gpm.tensor_active` | `NVML_GPM_METRIC_ANY_TENSOR_UTIL` | 任意 Tensor 运算活跃度 |
| `nvidia.gpm.dram_bw_active` | `NVML_GPM_METRIC_DRAM_BW_UTIL` | DRAM 带宽相对理论上限的比例 |
| `nvidia.gpm.fp64_non_tensor_active` | `NVML_GPM_METRIC_FP64_UTIL` | 非 Tensor FP64 活跃度 |
| `nvidia.gpm.fp32_non_tensor_active` | `NVML_GPM_METRIC_FP32_UTIL` | 非 Tensor FP32 活跃度 |
| `nvidia.gpm.fp16_non_tensor_active` | `NVML_GPM_METRIC_FP16_UTIL` | 非 Tensor FP16 活跃度 |

NVML GPM 没有独立的 FP8 利用率指标。FP8 Tensor Core 工作会进入通用 Tensor 活跃度，
但仅凭 GPM 不能判断它究竟是 FP8、BF16、FP16 还是其他 Tensor 数据类型。需要精确区分时，
应对选定作业做 CUPTI/Nsight Compute 的短时、按需 profiling，不应放进常驻高频监控路径。

## 调用模型

1. 用 `nvmlGpmQueryDeviceSupport` 对每块设备探测一次；不支持时静默保持原指标路径。
2. 每块支持的 GPU 用 `nvmlGpmSampleAlloc` 预分配两个 sample buffer。
3. 将 `nvmlGpmSampleGet` 合并进现有 collector tick，不新增线程、不阻塞等待。
4. 从第二个 tick 起，用相邻两个 sample 调用 `nvmlGpmMetricsGet`，之后交换 buffer。
5. sampler 关闭时用 `nvmlGpmSampleFree` 释放 buffer。

官方要求两个 sample 间隔大于 100 ms。Constella 的最小刷新间隔为 500 ms，因此无需额外
sleep。指标结构体和 buffer 都会复用，GPM 失败也不会使基础 NVML 快照失败。

默认模式为自动探测。可设置 `CONSTELLA_NVML_GPM=off` 完全关闭 GPM 路径。
开启 SQLite 时，`CONSTELLA_NVIDIA_GPM_ROLLUP=off` 可单独停止写入 GPM
rollup；基础 GPU rollup、实时监控和 Ascend NPU 路径不受影响。
`CONSTELLA_NVIDIA_GPM_HIGHRES=off` 可单独关闭内存中的性能环形缓冲。

## 本机验证

在 8 张 NVIDIA H100 80GB HBM3、580.65.06 驱动上验证：设备支持探测和 7 个指标均成功。
12 轮、每轮 8 卡的只读采样中，GPM 整轮平均约 11 ms；单卡 `SampleGet` 平均约 1.06 ms，
单卡 `MetricsGet` 平均约 0.30 ms。该结果只用于量级判断，实际开销仍应在目标驱动和负载上
通过 `scripts/dev/bench_probe.sh` 复测。

使用实际表结构生成 8 GPU、完整保留周期的 657,600 行影子库，数据库文件为
147.15 MiB，约 234.6 bytes/row。单卡 7 天 20 秒粒度的 30,240 行范围查询约
91 ms。该结果来自本机 SQLite 无并发基准，用于容量规划，不替代目标部署环境的
影子写入观测。

## 资料

- [NVML GPM metrics](https://docs.nvidia.com/deploy/nvml-api/group__nvmlGpmEnums.html)
- [NVML GPM call sequence](https://docs.nvidia.com/deploy/nvml-api/group__nvmlGpmFunctions.html)
- [NVML utilization struct semantics](https://docs.nvidia.com/deploy/nvml-api/structnvmlUtilization__t.html)
- [DCGM low-overhead profiling](https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/profiling.html)
- [Nsight Compute profiling overhead](https://docs.nvidia.com/nsight-compute/ProfilingGuide/index.html)
