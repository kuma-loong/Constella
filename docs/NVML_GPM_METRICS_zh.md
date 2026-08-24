# NVIDIA GPM 性能监控

<p align="center"><a href="NVIDIA_GPM.md">English</a> | 简体中文</p>

Constella 0.1.3 在支持的 NVIDIA GPU 上增加了基于 NVML GPM 的性能监控。它提供
Web 性能工作区、TUI 性能视图、高分辨率内存曲线以及可选的 SQLite 长期汇总；采集路径
与基础 NVML 监控隔离，因此 GPM 不可用或采集失败不会影响常规 GPU 状态和进程监控。

## 使用入口

服务启动后无需额外配置，NVIDIA agent 会自动探测每张 GPU 的 GPM 能力。

- Web UI：打开 `http://127.0.0.1:8765/performance`。可选择节点和多张 GPU，查看
  5 分钟、15 分钟、1 小时或 2 小时时间范围；拖选曲线区间后会重新计算平均值、最小值、
  最大值、P95、样本数和覆盖率。
- TUI：运行 `constella tui` 或 `constella-tui` 后按 `5`。使用 `n`/`g` 切换节点和
  GPU，`[`/`]` 切换时间范围，`h`/`l` 切换指标页，空格暂停或恢复实时刷新。
- API：`GET /api/highres/performance` 返回设备性能曲线和汇总；
  `GET /api/highres/jobs/{job_key}/performance` 返回作业所在设备的性能曲线。

Performance 视图要求节点声明 `nvidia.gpm.v1` profile，并启用高分辨率性能缓存。不满足
条件时界面会显示明确的不可用状态，Overview、Cluster、History 等其余功能不受影响。

## 支持条件

- NVIDIA GPU、提供 NVML GPM 接口的驱动，以及驱动报告为支持的设备/指标组合。
- Constella 使用设备级 GPM 指标，不需要 root 权限，也不会启动按 kernel 的 profiler。
- 已验证环境为 8 张 NVIDIA H100 80GB HBM3、580.65.06 驱动。其他硬件和驱动以运行时
  自动探测结果为准，不能仅根据 GPU 型号推断可用性。
- Ascend agent 不初始化 NVIDIA provider；不支持 GPM 的 NVIDIA GPU 会继续走基础 NVML
  或 `nvidia-smi` 回退路径。

## 指标含义

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

这些百分比是整个 GPU 在采样区间内的平均值，不是瞬时值，也不能直接归因到某个进程、
CUDA kernel 或代码行。相同的平均值可能来自少量 SM 持续繁忙，也可能来自全部 SM 短时
繁忙。指标适合发现设备利用不足、阶段变化和多卡不均衡；定位具体 kernel 时仍应使用
Nsight Systems 或 Nsight Compute。不同 GPU 型号的理论吞吐和带宽不同，因此相同百分比
也不代表相同的实际 FLOPS 或带宽。

### SM Activity（SM 活跃度）

表示采样期间 GPU 的流式多处理器（SM）有多活跃。数值越高，说明越多 SM 在更长时间内
有 warp 处于活动状态；但“活动”不等于正在有效执行计算，等待显存访问的 warp 也可能被
计为活动。因此，较高的 SM Activity 是充分利用 GPU 的必要条件之一，却不能单独证明计算
单元已经饱和。应结合 Tensor、FP16/32/64 和 DRAM 指标判断工作负载实际在执行什么。

### SM Occupancy（SM 占用率）

表示 SM 上实际驻留的 warp 数量相对于硬件理论最大驻留 warp 数量的比例，反映 GPU 同时
保持多少并行工作。Occupancy 会受到线程块大小、每线程寄存器数量、共享内存使用量以及
GPU 架构等因素影响。更高的 Occupancy 有助于隐藏访存延迟，但并不总是意味着性能更好；
计算受限的 kernel 可能在较低 Occupancy 下已经达到最佳吞吐。因此它更适合解释并行度，
而不是直接作为性能评分。

### Tensor Activity（Tensor Core 活跃度）

表示采样期间 GPU 的 SM 执行任意 Tensor 运算的时间比例，用于判断 Tensor Core 是否被
实际使用。较高的数值通常意味着深度学习矩阵计算正在持续使用 Tensor Core；较低的数值
并不一定表示 GPU 利用不足，因为数据准备、通信、普通 CUDA Core 运算或不适合 Tensor
Core 的算子都可能占据主要时间。如果预期工作负载应大量使用 Tensor Core，但该指标长期
接近零，通常需要进一步检查数据类型、矩阵形状和框架配置。

### DRAM Bandwidth Utilization（显存带宽利用率）

表示 GPU 实际使用的显存带宽相对于理论最大 DRAM 带宽的比例。它衡量的是显存数据传输
通道有多忙，不是显存容量占用了多少。持续较高的 DRAM 带宽利用率，同时 SM 或计算流水线
活跃度相对有限，通常说明工作负载可能受到显存带宽限制；但仍需结合应用吞吐和专业分析
工具确认。

### FP16 Non-Tensor Activity（FP16 非 Tensor 运算活跃度）

表示 SM 执行非 Tensor Core 的半精度浮点运算所占的时间比例。这个指标不包含 HMMA 等
Tensor Core 运算，因此在混合精度训练中出现“FP16 较低、Tensor 较高”通常是正常现象。
较高的 FP16 数值说明工作负载主要通过普通 CUDA 计算流水线执行半精度运算。它与 Tensor
Activity 配合使用，可以区分普通 FP16 运算和 Tensor Core 加速的矩阵运算。

### FP32 Non-Tensor Activity（FP32 非 Tensor 运算活跃度）

表示 SM 执行非 Tensor Core 单精度浮点运算所占的时间比例。较高的数值说明工作负载大量
使用普通 CUDA Core 的 FP32 计算，常见于传统 CUDA kernel、未启用 Tensor Core 的模型
或部分前后处理算子。较低的 FP32 数值不代表 GPU 没有工作，因为任务也可能主要由 Tensor、
FP16、FP64、整数计算或显存访问构成。

### FP64 Non-Tensor Activity（FP64 非 Tensor 运算活跃度）

表示 SM 执行非 Tensor Core 双精度浮点运算所占的时间比例。FP64 主要用于科学计算、数值
模拟和对精度要求较高的 HPC 工作负载，在常规 AI 训练和推理中通常较低。较高的 FP64
活跃度说明任务正在持续使用双精度计算资源；不同 GPU 型号的 FP64 峰值能力差异很大，
因此不能仅凭百分比直接比较不同型号 GPU 的实际计算性能。

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

## 数据保留与开关

数据按用途分为三条可独立控制的路径：

| 路径 | 默认状态 | 内容 | 控制方式 |
| --- | --- | --- | --- |
| 实时采集 | 自动探测 | agent 快照中的设备级 GPM 值 | `CONSTELLA_NVML_GPM=off` 关闭 |
| 高分辨率内存缓存 | 开启 | 近期原始曲线与有效性掩码 | `CONSTELLA_NVIDIA_GPM_HIGHRES=off` 关闭 |
| SQLite rollup | 启用数据库时开启 | 20 秒、2 分钟、1 小时汇总 | `CONSTELLA_NVIDIA_GPM_ROLLUP=off` 关闭 |

高分辨率缓存默认保留 2 小时，并按 0.5 秒最小采样间隔预分配固定容量。8 张 GPU 的性能
数组约预分配 4.06 MiB。原始高频性能点不会写入 SQLite；GPM rollup 与基础 GPU rollup
使用独立表和相同保留层级。

三个开关只影响 NVIDIA GPM。关闭其中任意一条路径都不会关闭基础 NVML、
`nvidia-smi` 回退、Ascend 采集或实时集群快照。

## 验证与排障

先验证 agent 是否报告 GPM profile 和状态：

```bash
uv run constella probe --pretty
curl -s http://127.0.0.1:8765/api/cluster/snapshot
curl -s http://127.0.0.1:8765/api/highres/status
```

重点检查节点的 `performance_profiles` 是否包含 `nvidia.gpm.v1`，以及各 GPU 的性能状态：

- `warming`：刚启动，尚未取得满足间隔要求的第二个 sample；等待下一轮采样。
- `available`：GPM 指标可用。
- `unsupported`：当前设备、驱动或指标组合不支持 GPM；基础监控仍会继续。
- `error`：本轮 GPM 调用失败；查看 `logs/local-agent.log` 或对应远端 agent 日志。

页面提示缓存关闭时，检查 `CONSTELLA_NVIDIA_GPM_HIGHRES`，以及独立 sidecar 的状态和
`/api/highres/status`。页面可以显示实时曲线但没有持久化汇总时，检查是否配置了 `DB_PATH`
以及 `CONSTELLA_NVIDIA_GPM_ROLLUP`。如果 `probe` 的基础数据源为 `nvidia-smi`，说明 NVML
路径本身不可用，此时无法产生可用的 GPM 采样。

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
- [NVIDIA Fleet Intelligence metric rationale](https://docs.nvidia.com/fleet-intel/data-collection-rationale/)
- [Nsight Compute profiling overhead](https://docs.nvidia.com/nsight-compute/ProfilingGuide/index.html)
