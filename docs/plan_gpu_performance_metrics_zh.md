# GPU 硬件性能指标接入计划

## 目标

在保持 Constella 轻量、高性能和可回退的前提下，引入 NVML GPM 硬件性能指标，用于实时 GPU 卡片、Highres 性能分析和可选的历史任务曲线。

本期能力仅适用于支持 NVML GPM 的 NVIDIA GPU，但数据模型、API 和页面不能假设所有加速器都具有 SM、Tensor Core 或 GPM。Ascend NPU 及未来其他硬件继续使用各自的采集实现和指标语义。

## 多硬件抽象与隔离

Constella 的通用领域对象是 Accelerator，NVIDIA GPU 和 Ascend NPU 是不同的设备族。现有 `GpuInfo` 和 `device_type` 为兼容接口继续保留，本期不进行影响全系统的重命名；新增性能能力不得继续把 NVIDIA 专有字段平铺到 `GpuInfo`。

每个设备只增加一个可选的性能能力封装：

```text
performance:
  profile: nvidia.gpm.v1
  status: warming | available | unsupported | error
  sampled_at
  interval_ms
  metrics
  error
```

其中 `profile` 是判别字段。通用层只负责状态、时间、查询和生命周期，不解释厂商指标；`nvidia.gpm.v1` 内部才定义 SM、Tensor 和 DRAM 等字段。未来如果 Ascend 提供对应能力，应新增 `ascend.<provider>.v1` profile，不能把 AI Core 等指标伪装成 SM 或 Tensor 指标。

指标标识采用带命名空间的稳定 ID，例如：

- `nvidia.gpm.sm_active`
- `nvidia.gpm.sm_occupancy`
- `nvidia.gpm.tensor_active`
- `nvidia.gpm.dram_bw_active`

API 只传递紧凑的 ID、数值和状态。名称、单位、分组、颜色和格式由前端按 profile 的静态指标注册表解释，避免每秒重复传输描述元数据，也避免使用无类型约束的全局 EAV 模型。

Agent hello 能力声明增加可选的 `performance_profiles`。节点声明 profile 代表采集器具备该能力，设备级 `status` 才代表当前设备是否实际支持和可用。旧 Agent 没有该字段时按“不提供性能能力”处理，而不是报错。

采集器按设备族隔离：

```text
SnapshotCollector
├─ NVIDIA：NVML -> nvidia-smi
│  └─ 可选 NvidiaGpmPerformanceProvider
└─ Ascend：DCMI -> npu-smi
   └─ 本期无性能 provider
```

GPM provider 由 NVIDIA 采集分支独立持有，并复用 NVML 设备句柄。它的初始化、熔断和错误不能影响普通 NVML，也不会在 Ascend 进程中加载或调用。通用 Collector 只接收可选的 performance 结果，不包含 GPM 指标 ID 和 NVML 调用细节。

Highres 和数据库采用“通用查询门面、profile 专用存储实现”：

- `nvidia.gpm.v1` 使用固定数组的 Highres 环形缓冲，避免逐样本字典和对象开销。
- Rollup 使用专用宽表 `nvidia_gpm_rollups`，避免为了兼容未知硬件而采用高开销 EAV 或 JSON 存储。
- 通用 Performance 查询服务根据 profile 路由到对应缓存和 Repository，并返回统一的时间序列外壳。
- 未来新增 NPU profile 时增加自己的缓冲和表，不修改 NVIDIA 表，也不影响已有保留策略。

前端使用以 profile 为判别字段的联合类型和 renderer registry。GPU 卡片只有在设备声明 `nvidia.gpm.v1` 时才渲染 GPM 性能区，并在区域内部处理 warming、available、unsupported 和 error；Ascend NPU 卡片保持现有内容，不显示“GPM 不支持”之类的错误。Performance 页面路由保持通用的 `/performance`，根据所选 Node 的 profile 加载对应指标分组；本期选择 NPU 节点时显示“尚未提供该硬件的详细性能 provider”，而不是渲染空的 NVIDIA 图表。

后续可单独开展 `GpuInfo -> AcceleratorInfo`、`gpus -> accelerators` 的兼容迁移，同时补充明确的 `kind`、`vendor` 和 `backend` 字段。本期只建立新能力边界，不把大范围命名迁移和 GPM 功能绑在同一次发布中。

## 指标范围

NVML GPM 作为增强能力，不替代普通 NVML。首批采集：

- SM Active
- SM Occupancy
- Tensor Active，NVML 仅提供所有 Tensor Core 指令的合计活跃度，不能可靠区分 FP16、BF16、FP8
- DRAM Bandwidth
- FP16、FP32、FP64 non-Tensor Active

所有性能字段均允许为空。缺失、未支持和采样失败不能按 0 处理，也不能用普通 GPU utilization 冒充 SM Active。

## 采样与回退

NVIDIA 节点的采样分为两条相互独立的路径：

1. 基础指标：普通 NVML，失败后回退到 `nvidia-smi`。
2. 性能指标：优先 NVML GPM，失败时只将性能指标标记为不可用，基础指标继续工作。

GPM 采用连续两个、间隔至少 100 ms 的样本计算指标。正常运行时复用相邻采样周期，不在一次循环中阻塞等待。状态需包含 `warming`、`available`、`unsupported` 和 `error`，连续错误后暂时熔断并定期重试。

## 实时 GPU 卡片

保持现有卡片高度，用固定的 2×2 性能区替换时钟频率等低价值信息：

- SM Active
- SM Occupancy
- Tensor Active
- DRAM Bandwidth

卡片主曲线保持现状，继续使用普通 NVML 的 GPU utilization，但不在曲线内部增加额外文字标签。主曲线用于判断 GPU 是否持续执行任务，不切换为 SM Active，以保证不同型号、不同节点及 GPM 不可用时的指标一致性和历史连续性。

SM Active 仅作为性能区指标，用于解释 GPU 活跃时计算资源的实际展开程度。FP16、FP32、FP64 仅在性能分析页面展示。NVIDIA GPU 已声明 GPM profile 但当前不可用时，保留相同布局高度并显示状态，避免 GPU 卡片网格跳动。没有 GPM profile 的 NPU 和其他设备保持自己的卡片布局，不为兼容 GPM 预留空白区域。卡片可跳转到对应 Node 和 GPU 的 Performance 页面。

## Highres Performance 页面

新增 `/performance` 页面，以分类平铺的方式同时展示多张曲线：

- Compute：SM Active、SM Occupancy、Tensor Active
- Memory：DRAM Bandwidth
- Non-Tensor Pipelines：FP16、FP32、FP64

页面顶部提供：

- Node 单选
- 当前 Node 内的 GPU 多选、全选和清空
- 时间范围、Live 模式、布局和全部展开/折叠控制

第一阶段只支持一个 Node 内多设备对比，不做跨 Node 混合比较。Node 切换后恢复该节点上次选择的设备；从 GPU 卡片进入时自动选中对应 Node 和 GPU。Node 选择器展示所有节点及其 performance profile 状态，选择当前没有 profile renderer 的节点时展示明确的能力空状态。

分类和单张图表都允许折叠，折叠状态保存在浏览器本地。默认宽屏双列、窄屏单列，并提供全局单列/双列切换。折叠图表不创建或更新绘图实例。

所有展开图表共享时间轴、十字光标和选中区间。在任意图表框选时间后，统一计算各 GPU 的平均值、峰值、P95、样本数和覆盖率。统计值基于服务端原始数据计算，绘图数据允许降采样；长时间范围采用保留峰值的 min/max bucket 策略。缺失样本在曲线上显示为空洞。

Highres 环形缓冲为每张 GPU 增加 7 个 `float32` 指标数组、一个 `float64`
时间戳数组和有效位掩码，即每个预分配槽位 37 bytes。按 8 GPU、1 秒采样、
保留 2 小时估算约 2.03 MiB；按最小 0.5 秒采样间隔预分配约 4.06 MiB。
状态接口同时报告有效点估算和实际预分配容量。

接口按 Node、GPU、指标和时间范围批量查询，并支持 `max_points` 与 `summary_only`。
默认仅请求已展开指标；页面使用约 24,000 点的总预算，将单条曲线动态限制在
250 至 1500 点。5/15 分钟 Live 窗口每 2 秒刷新，1 小时每 5 秒、2 小时每
10 秒刷新，并取消 Node/GPU 切换后迟到的旧请求。统计值始终基于原始点计算，
不受绘图预算影响。

## Rollup 与任务曲线

复用当前 SQLite 数据库和异步写入队列，为 `nvidia.gpm.v1` 新增独立的 `nvidia_gpm_rollups` 宽表，不修改基础指标表，也不让未来 NPU 指标写入该表。每个指标保存：

- `avg`
- `max`
- `valid_count`

每行保存 `expected_count` 以计算覆盖率。上卷平均值使用 `valid_count` 加权，未支持字段保持 NULL。

沿用 20 秒保留 7 天、2 分钟保留 60 天、1 小时保留 365 天的策略，每张 GPU 最多约 82,200 行，8 GPU 节点约 657,600 行。包含 7 个指标、统计字段和索引后，预计每个 8 GPU 节点增加约 100 至 180 MiB，最终以影子写入实测为准。计算开销较小，主要成本是长期存储和大范围查询响应体。

本机使用最终宽表结构完成 657,600 行容量基准：数据库文件 147.15 MiB，约
234.6 bytes/row；单卡 7 天 20 秒粒度范围查询约 91 ms。该结果落在预估区间内，
公开发布前仍保留目标环境 7 至 14 天影子写入观察项。

任务页面仅在用户选择性能指标时懒加载新表数据。GPM 是设备级指标，任务曲线只能表示“任务运行期间所在 GPU 的硬件活动”，不能归因到单个进程；存在共享 GPU 会话时必须明确提示。

## 隔离验收矩阵

落地时至少覆盖以下场景：

| 场景 | 预期结果 |
|---|---|
| NVIDIA GPU，GPM 可用 | 基础指标和 `nvidia.gpm.v1` 同时可用 |
| NVIDIA GPU，GPM 不支持 | 基础指标正常，性能区显示 unsupported，不写 GPM Rollup |
| NVIDIA GPU，GPM 瞬时失败 | 基础 NVML 不回退，性能 provider 独立熔断和重试 |
| NVIDIA 基础 NVML 失败 | 基础指标回退 `nvidia-smi`，GPM 本轮不可用，不冒充性能值 |
| Ascend NPU | 只运行 DCMI 或 `npu-smi` 路径，不初始化 NVML/GPM，不写 NVIDIA 表 |
| 旧版本 Agent | Manager 正常接收基础快照，按无 performance profile 处理 |
| 未识别的新 profile | Manager 保留通用状态，前端显示无 renderer，不导致页面崩溃 |

回归测试需要验证关闭或破坏 GPM provider 时，NVIDIA 基础指标、Ascend NPU、Highres 基础曲线、现有 Rollup 和任务查询结果均保持不变。

## 落地顺序

1. 定义 Accelerator 性能能力外壳、profile 注册机制、采样状态和独立回退路径。
2. 实现 `nvidia.gpm.v1` provider，完成 NVIDIA GPU 实时卡片，验证 NPU 路径和卡片不受影响。
3. 扩展 profile 专用 Highres 环形缓存和通用查询接口，完成多图 Performance 页面及区间统计。
4. 开启 NVIDIA GPM Rollup 影子写入 7 至 14 天，观测数据库增长、写队列延迟和查询耗时。
5. 验证通过后，将性能 Rollup 接入任务曲线。

采集、Highres 和 Rollup 应分别受 profile 级功能开关控制。关闭 `nvidia.gpm.v1`、Highres 性能数据或 NVIDIA GPM Rollup 时，不影响 NVIDIA 基础监控、Ascend NPU 监控和现有历史数据查询。

## 当前落地状态

- 已完成 profile 能力外壳、NVIDIA GPM provider、独立错误隔离与实机 H100 验证。
- 已完成固定数组 Highres 缓存、批量查询、原始样本统计和缺失点保留。
- 已完成复用现有 SQLite 的 NVIDIA 专用宽表、三级 rollup、保留与任务查询。
- 已完成 GPU 卡片辅助指标；主曲线保持普通 NVML GPU utilization。
- 已完成 `/performance` 页面，按类别分区并在类别内平铺图表，支持 Node/GPU
  切换、折叠、布局、Live、框选区间和服务端统计。
- 已完成任务页面性能指标懒加载，并显示设备级、非进程归因警告。
- 发布门槛：完整测试、包构建、隔离预览和目标环境影子写入观察。
