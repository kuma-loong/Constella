数据分析与看板落地计划

## 落地状态

已在 `codex/data-analysis-dashboard` 分支落地第一版：

- 新增 `src/constella/analytics.py`，集中处理用户卡时、GPU 权重、作业合并、异常占用、非工作时段统计、节点曲线和热力图聚合。
- 新增 `GET /api/analytics/overview?range=7d` 与 `GET /api/analytics/node/{node_id}?range=24h`。
- SQLite 未启用时分析 API 返回 `enabled:false`，前端显示历史库未启用提示；实时 WebSocket 和集群快照路径不依赖数据库。
- 前端已切换为明亮主题，并在 Overview 和 Node 页面加入轻量原生 SVG/HTML 分析看板，不引入大型图表库。
- 已补充 `tests/test_analytics.py`，覆盖 overlap、GPU 权重、作业 key、Overview 聚合、异常检测、Node series/heatmap 和 API disabled/enabled。

## 目标

在不影响实时监控主路径的前提下，基于可选 SQLite 历史库增加数据分析看板。第一版聚焦两个场景：

- Overview：用户级、作业级和异常占用分析。
- Node 页面：节点内 GPU 历史曲线和使用热力图。

实时链路仍然保持：

```text
agent sample -> manager memory state -> frontend websocket
```

历史分析链路独立走：

```text
optional SQLite DB -> analytics query layer -> HTTP API -> frontend dashboard widgets
```

数据库未启用时，分析 API 返回 `enabled:false`，前端隐藏历史分析模块，不影响现有实时页面。

## 总体设计

新增一个分析层模块，例如 `src/constella/analytics.py`。

职责划分：

- `db.py`：继续负责 SQLite schema、写入、rollup、基础查询。
- `analytics.py`：负责面向看板的统计口径、时间窗、排行、异常检测、热力图聚合。
- `app.py`：只暴露分析 API，不放复杂 SQL 和业务口径。
- 前端：只负责展示、筛选、交互，不在浏览器里拉大表做重计算。

建议新增 API：

```text
GET /api/analytics/overview?range=7d
GET /api/analytics/node/{node_id}?range=24h
```

统一响应元信息：

```json
{
  "enabled": true,
  "generated_at": 1234567890,
  "range_start": 1234560000,
  "range_end": 1234567890,
  "timezone": "Asia/Shanghai",
  "bucket_seconds": 120,
  "items": []
}
```

前端所有时间标签按北京时间展示。后端可以返回 epoch 秒，前端用 `Asia/Shanghai` 渲染；也可以在响应中显式返回 `timezone`，避免含糊。

## 时间窗与 Rollup 选择

第一版时间窗：

- Overview 默认：滚动 7 天。
- Node 历史曲线：`1h / 24h / 7d / 30d`。
- Node 热力图：默认 `24h`，可切 `7d`。

Rollup 选择规则：

- `<= 7d`：优先使用 20 秒 rollup。
- `> 7d && <= 60d`：使用 2 分钟 rollup。
- `> 60d`：使用 1 小时 rollup。

前端分辨率不直接等于数据库 bucket。API 层需要按图表宽度和时间窗做二次降采样，避免返回过多点：

- 曲线目标点数：每条线 300-800 点。
- 热力图目标列数：`24h` 使用 30 分钟或 1 小时列；`7d` 使用 2 小时或 4 小时列。
- 排行榜和异常卡片只返回 Top N，例如 20-50 条。

## 用户用卡耗时

### 指标定义

不能直接用 `process_sessions.duration_seconds` 作为用卡耗时，因为一个进程可能占多张卡，或者多个进程属于同一个作业。第一版用 `process_gpu_usages` 计算“卡时”。

基础卡时：

```text
card_seconds = SUM(
  overlap_seconds(process_gpu_usage.first_seen_at, process_gpu_usage.last_seen_at, range_start, range_end)
)
```

展示单位：

```text
gpu_hours = card_seconds / 3600
```

加权卡时：

```text
weighted_gpu_hours = SUM(card_seconds * gpu_weight) / 3600
```

### GPU 权重

需要支持按 GPU 型号设置权重。例如：

```text
H100: 1.0
PRO 6000: 0.9
default: 1.0
```

第一版建议先做硬编码默认表或简单配置表，后续再做 UI 配置。

匹配策略：

- 从 `gpus.name` 读取 GPU 型号。
- 规范化为大写，去掉多余空格。
- 按包含关系匹配，例如 name 包含 `H100` 使用 1.0，包含 `PRO 6000` 使用 0.9。
- 未命中使用 `default=1.0`。

Overview 用户榜字段：

- `user`
- `gpu_hours`
- `weighted_gpu_hours`
- `task_count`
- `job_count`
- `last_seen_at`
- `top_gpu_models`

默认按 `weighted_gpu_hours` 排序，同时显示原始 `gpu_hours`。

## 作业合并

第一版采用启发式作业合并，不新增强依赖。

作业 key：

```text
job_key = node_id + user + coalesce(parent_start_time, process_start_time) + coalesce(ppid, pid)
```

佐证字段：

- `cmdline_hash`
- `task_name`
- `process_name`
- `first_seen_at / last_seen_at` 时间重叠
- 涉及的 `gpu_uuid` 数量
- session 数量

这个口径对 torchrun、accelerate、多 worker 训练和普通 python 任务已经够可信。第一版不追求完美识别容器、服务管理器和复杂进程树。

作业榜字段：

- `job_key`
- `user`
- `node_id`
- `task_name`
- `started_at`
- `last_seen_at`
- `duration_seconds`
- `gpu_count`
- `session_count`
- `gpu_hours`
- `weighted_gpu_hours`
- `status`

作业榜只保留“耗时榜”和“资源占用榜”。不做高效榜。

## 异常占用

以卡片形式展示异常占用，放在 Overview。第一版只做明确、低误报的规则。

异常规则：

```text
duration_seconds >= 2h
AND avg_memory_mb >= 20GB
AND recent_or_window_avg_gpu_utilization < 5%
```

需要考虑“初期确实在用，后面忘记释放”的场景，所以不能只看整个生命周期平均利用率。建议同时计算：

- `lifetime_avg_gpu_utilization`：作业生命周期内平均 GPU 利用率。
- `recent_avg_gpu_utilization`：最近 30-60 分钟平均 GPU 利用率。
- `idle_tail_seconds`：连续低于 5% 利用率的大致时长。

判定优先使用：

```text
duration >= 2h
AND avg_memory_mb >= 20GB
AND recent_avg_gpu_utilization < 5%
```

如果 recent 数据不足，再退化使用生命周期窗口平均。

异常卡片字段：

- `user`
- `node_id`
- `task_name`
- `duration_seconds`
- `gpu_memory_gb`
- `recent_avg_gpu_utilization`
- `idle_tail_seconds`
- `gpu_uuids`
- `last_seen_at`
- `reason`

不做 `task_name` 匹配规则，避免把模型服务或正常长任务按名字误判。

## 深夜与周末统计

作为轻量趣味模块，不作为主排行榜。

统计口径：

- 使用 `process_sessions.first_seen_at` 或作业合并后的 `started_at`。
- 北京时间：
  - 深夜：`00:00-06:00`
  - 周末：周六、周日

可展示：

- `night_job_count`
- `weekend_job_count`
- `night_gpu_hours`
- `weekend_gpu_hours`
- Top 用户小榜

文案保持中性，例如“非工作时段活跃”，不要做强评价。

## Node GPU 历史曲线

Node 页面新增历史曲线模块。

设计：

- 一张多线图展示同一节点内多张 GPU。
- 支持指标切换：GPU 利用率、显存占用、功耗、温度。
- 支持时间窗：`1h / 24h / 7d / 30d`。
- 支持 GPU 选择：全部、单卡、多选。
- Hover 显示北京时间、GPU、avg/max、显存等数据标签。

不建议第一版做左右拖动和缩放。先用固定时间窗和 hover tooltip，复杂交互后续再加。

前端实现建议：

- 继续使用原生 SVG 或轻量自绘组件。
- 不急着引入重型图表库。
- 当后续需要拖拽缩放、brush、复杂 tooltip 时，再评估引入专门图表库。

## Node GPU 热力图

Node 页面新增热力图模块，用于展示哪些卡在哪些时段经常使用。

维度：

- Y 轴：GPU index / GPU name。
- X 轴：北京时间时间桶。
- 颜色：平均 GPU 利用率。

Tooltip：

- 时间段
- GPU index / uuid
- `avg_gpu_utilization`
- `max_gpu_utilization`
- `avg_memory_used_mb`
- `sample_count`

颜色分档建议：

- 0-5%：近白或浅灰
- 5-30%：浅绿
- 30-70%：蓝绿
- 70-100%：橙红

热力图比“每张卡一张图”更适合回答“哪些卡在哪些时段经常使用”。

## 前端明亮主题

第一步先把整体 UI 转成明亮主题。

原则：

- 背景使用浅灰/白色，降低暗色大面积压迫感。
- 卡片仍保持 8px 圆角，边框清晰，阴影克制。
- 状态色保留：绿、青、黄、红，但降低饱和度，适配白底。
- 保留 CSS 变量结构，为后续 dark/light toggle 留空间。
- Overview 不做营销化大 hero，保持监控工具的信息密度。

建议顺序：

1. 先改全局 CSS 变量和基础布局。
2. 再调整实时 Overview 和 Node 卡片。
3. 最后新增分析模块样式。

## API 数据草案

### `/api/analytics/overview`

```json
{
  "enabled": true,
  "generated_at": 1234567890,
  "range_start": 1233963090,
  "range_end": 1234567890,
  "timezone": "Asia/Shanghai",
  "user_gpu_hours": [],
  "job_rankings": [],
  "anomalies": [],
  "off_hours": {}
}
```

### `/api/analytics/node/{node_id}`

```json
{
  "enabled": true,
  "generated_at": 1234567890,
  "range_start": 1234481490,
  "range_end": 1234567890,
  "timezone": "Asia/Shanghai",
  "bucket_seconds": 120,
  "series": [],
  "heatmap": []
}
```

## 分阶段实施

### Phase 1：明亮主题

- 调整 CSS 变量和现有组件样式。
- 保持现有功能不变。
- 验证 Overview、Node 页面和移动端布局。

### Phase 2：分析后端

- 新增 `analytics.py`。
- 实现滚动 7 天用户卡时、加权卡时。
- 实现作业合并和作业耗时/资源占用排行。
- 实现异常占用检测。
- 实现 Node 曲线和热力图聚合 API。
- 补充单元测试，覆盖卡时 overlap、GPU 权重、作业合并、异常检测。

### Phase 3：Overview 分析 UI

- 用户用卡榜。
- 作业耗时榜。
- 异常占用卡片。
- 深夜/周末趣味统计。
- 所有模块显示 `generated_at`，支持手动刷新。

### Phase 4：Node 历史 UI

- 多线历史曲线。
- GPU 选择和指标切换。
- 热力图。
- 北京时间 hover 标签。

## 暂不做

- 高效榜。
- 进程级 SM utilization 精确归因。
- 第一版图表拖拽缩放。
- 基于 task name 的异常匹配。
- 前端拉取大量明细数据后自行聚合。

## 需要后续确认

- GPU 权重是否只按型号配置，还是要支持按节点/具体 GPU 覆盖。
- 加权卡时是否用于主排序，还是只作为辅助列。
- 异常占用阈值是否固定为 2h、20GB、5%，还是后续做成配置。
- 深夜时间段是否固定为北京时间 `00:00-06:00`。
