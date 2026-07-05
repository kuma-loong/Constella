# 数据分析与看板 Stage 2 改造计划

## 落地状态

已在 `codex/data-analysis-dashboard` 分支落地 Stage 2：

- 后端异常占用检测固定使用最近 24 小时窗口，并在 payload 中补充 GPU index 和 PID 摘要。
- Node 热力图 bucket 调整为 `1h=5m`、`24h=1h`、`7d=6h`、`30d=1d`，避免短窗口过粗和长窗口过密。
- SQLite schema 初始化补充分析查询索引，覆盖 rollup、process usage 窗口和用户 last seen 查询。
- Node History 改为上下布局：趋势曲线独立于活动热力图。
- GPU 曲线选择改为 `selectedGpuUuids: Set<string>` 多选；图例点击只更新已有 SVG/DOM class 和摘要文案，不重新请求 API、不重建热力图。
- 热力图改为无缝连续 band，增加北京时间横轴、颜色语义 legend 和包含 avg/max/sample 的 tooltip。
- Overview 合并用户/作业排行为 Usage rankings，用户排行只展示加权后的 `GPU hours`，异常占用和 After-hours lab life 独立成模块。

## 目标

Stage 2 在第一版历史分析看板基础上，修正交互边界、信息层级和可读性问题。核心目标：

- GPU 曲线选择变成纯前端局部状态更新，不造成整块刷新感。
- GPU 曲线支持同时选择多张卡，方便横向对比。
- 热力图与曲线解耦，作为静态时间分布概览。
- 热力图改成更连续的渐变表达，并补充清晰时间横坐标。
- Overview 中的用户卡时排行、异常占用、非工作时段活动拆成更独立的展示模块。

继续保持原则：

- 实时链路不变：`agent sample -> manager memory state -> frontend websocket`。
- 历史分析仍是可选 SQLite 旁路；DB 未启用时不影响实时页面。
- 前端继续轻量实现，优先 SVG / CSS / 原生 DOM，不引入重型图表库。
- 页面布局允许留白。内容量少的模块不强行占满整行，优先使用合适比例和清晰层级。
- 如果轻量图表库能明显减少重复造轮子，并且符合当前时间序列 / heatmap 场景，可以引入。
- 数据处理继续贯彻“低占用”：查询、聚合、降采样和前端渲染都要避免对实时监控造成压力。

## Node History 交互

### 当前问题

现在 GPU 曲线点击图例时，虽然没有重新请求后端数据，但会重新渲染整个 Node History 区域。这会带来两个错觉：

- 像是 GPU 曲线和热力图发生了联动。
- 像是切卡触发了数据刷新。

实际上 GPU 曲线选择应该只是本地视觉状态切换。

### 改造方案

曲线选择改为局部更新：

- 不调用整块 `renderNode(...)`。
- 不重新请求 `/api/analytics/node/{node_id}`。
- 不重建热力图 DOM。
- 只更新曲线 SVG 和图例按钮的 class / opacity。

可以称为“局部增量渲染”，但这里更准确的含义是：

```text
已有 SVG/DOM 节点保持不变
只更新被选 GPU 集合和对应 DOM class
```

### 多选 GPU

GPU 曲线选择从单选改为多选：

- 默认状态：全部 GPU 高亮。
- 点击某张 GPU：
  - 如果当前是全部高亮，则切换为只高亮该 GPU。
  - 如果已有选择集合，则 toggle 该 GPU。
  - 如果取消到空集合，则回到全部高亮。
- 提供 `All` 按钮，一键恢复全部高亮。

视觉规则：

- 选中 GPU：原色、较高不透明度。
- 未选 GPU：颜色保留但显著变淡，例如 `opacity: 0.12-0.2`。
- 不隐藏未选 GPU，保留上下文。

状态结构建议：

```ts
selectedGpuUuids: Set<string>
```

渲染规则：

```text
selectedGpuUuids 为空 => 全部 selected
selectedGpuUuids 非空 => 集合内 selected，集合外 muted
```

## 曲线与热力图解耦

### 交互边界

曲线响应：

- 时间窗切换。
- 指标切换。
- GPU 多选高亮。

热力图响应：

- 时间窗切换。
- 后端数据刷新。

热力图不响应：

- 曲线指标切换。
- GPU 图例选择。
- 曲线 hover。

原因：

- 曲线回答“某几张卡的指标趋势如何”。
- 热力图回答“整个节点哪些卡在什么时段更活跃”。

二者并排出现时容易被误解成同一个交互系统，所以 Stage 2 需要在布局和交互上明确边界。

## Node History 布局

### 当前问题

曲线和热力图并排放在同一行时，曲线需要较大高度，而热力图内容通常较短。同等高度下，热力图卡片底部会出现大量空白，看起来不平衡。

### 推荐布局

改成上下结构：

```text
Node History
  Toolbar: range / metric / refresh

  Trend panel
    大尺寸 GPU 曲线
    曲线图例与多选按钮

  Activity panel
    渐变热力图
    时间横坐标
    颜色图例
```

优点：

- 曲线可以占满横向空间，更适合多 GPU 对比。
- 热力图可以自然使用较低高度，不再被曲线高度绑架。
- 时间轴可以和热力图宽度对齐，便于读时间段。

桌面端建议：

- 曲线高度：`280-340px`。
- 热力图高度：按 GPU 数量自适应，每张 GPU 一条水平 band。
- 热力图卡片不强行拉到和曲线等高。
- 内容少的辅助模块可以使用 `span-4 / span-5 / span-6` 这类中等宽度，不必铺满 12 列。
- 允许页面右侧或模块间出现有意留白，避免为了“填满”而把低信息密度内容拉得很散。

移动端建议：

- 仍然上下排列。
- 热力图允许横向滚动，避免时间轴挤压到不可读。

## 热力图视觉方案

### 从格子改为渐变 band

当前格子热力图的问题：

- 1h 窗口下格子太少时显得粗糙。
- 24h 窗口下格子太多时显得琐碎。
- 缺少横坐标，不容易判断具体时间段。

Stage 2 改成“连续渐变时间带”：

```text
GPU0 | 低利用率浅色 -> 高利用率暖色 -> 中等利用率青绿色 ...
GPU1 | ...
GPU2 | ...
```

实现方式仍然可以保持轻量：

- 后端仍返回按 bucket 聚合的数据。
- 前端用 SVG 绘制每张 GPU 的 horizontal gradient strip。
- 每个 bucket 可以画成相邻窄 rect，但视觉上去掉明显 gap，形成连续色带。
- 或使用 SVG `linearGradient` stop 近似连续过渡。

建议第一版使用“无缝 rect band”：

- 比纯 CSS gradient 更容易保留 tooltip。
- 比传统格子更连续。
- 不需要引入 canvas 或图表库。

### 时间横坐标

热力图底部增加时间轴：

- 左端：range_start。
- 中间：按窗口显示 2-6 个 tick。
- 右端：range_end。

示例：

```text
1h:  14:00  14:15  14:30  14:45  15:00
24h: 00:00  06:00  12:00  18:00  now
7d:  Mon Tue Wed Thu Fri Sat Sun
30d: 06/01 06/08 06/15 06/22 06/29
```

所有时间继续按 `Asia/Shanghai` 渲染。

### 颜色刻度

保留简单清晰的四段语义：

```text
0-5%     idle       near-white / light gray
5-30%    low        soft green
30-70%   active     teal / blue-green
70-100%  hot        orange / red
```

热力图底部增加小型 legend：

```text
idle  low  active  hot
```

不要只靠颜色判断，可以在 hover tooltip 中显示：

- 时间段。
- GPU index。
- 平均 GPU 利用率。
- 最大 GPU 利用率。
- 平均显存。
- sample_count。

## 热力图分辨率

### 当前问题

固定 `24h -> 30m` 会产生 48 列，对小容器来说偏密；但 `1h -> 30m` 只有 2 列，信息量太低。

Stage 2 按时间窗重新设计目标列数：

```text
1h   -> 5m   bucket，约 12 列
24h  -> 1h   bucket，约 24 列
7d   -> 6h   bucket，约 28 列
30d  -> 1d   bucket，约 30 列
```

这组配置的目标：

- `1h` 足够细，能看到短时波动。
- `24h` 不过密，适合看全天时段模式。
- `7d` 适合看工作日 / 周末节奏。
- `30d` 适合看长期活跃分布，不追求小时级细节。

如果后续要更精细，可以让前端传容器宽度目标列数，但第一版不建议加复杂度。

## Overview 排行模块

### 用户 GPU hours

只展示加权后的 GPU hours，并直接命名为 `GPU hours`。

不再同时展示原始卡时和加权卡时，避免用户困惑。

说明文案放在表头或卡片小字：

```text
gpu卡时根据卡的性能加权算得
```

推荐列：

```text
User | GPU hours | Jobs | Models | Last seen
```

字段含义：

- `GPU hours`：加权后的 `weighted_gpu_hours`。
- `Jobs`：启发式合并后的作业数量 `job_count`。
- `Models`：用户主要使用过的 GPU 型号，按贡献卡时排序。
- `Last seen`：最近一次任务出现时间。

`task_count` 暂不展示，避免 session / job 的概念混在一个表格里。

### 作业排行

作业排行和用户排行可以放在同一个 `UsageRankings` 模块内，因为二者共享同一次 overview analytics 数据。

推荐列：

```text
Task | User | Node | GPU hours | Runtime | Status
```

同样只展示加权 GPU hours。

### 布局策略

用户排行和作业排行信息密度较高，可以使用较大宽度：

- 宽屏下并排展示，各占 6/12。
- 中等屏幕下堆叠。
- 如果某个榜单数据很少，不强行拉成巨型表格，可以使用较短卡片高度。

异常占用、off-hour 这类模块内容量波动更大，应允许中等宽度：

- 有异常时可以占 6/12 或 8/12，突出行动信息。
- 无异常时只需要一个短状态卡片，不必铺满整行。
- off-hour 更像洞察卡片，默认可占 4/12 或 5/12。

页面留白是允许的。重点是信息密度和可读性，不是把每一行填满。

## 异常占用模块

### 模块边界

异常占用应作为独立模块，不和排行表绑定。

第一版可以仍使用 overview API 返回的数据，但后端异常计算固定使用过去 24h：

```text
anomaly_window = [now - 24h, now]
```

后续可拆独立 API：

```text
GET /api/analytics/anomalies?range=24h
```

### 计算口径

异常触发规则保持低误报：

```text
duration_seconds >= 2h
AND avg_memory_mb >= 20GB
AND recent_avg_gpu_utilization < 5%
```

recent 利用率使用最近 30-60 分钟 rollup；若 recent 数据不足，再退化到 24h 窗口内生命周期平均。

### 展示字段

异常卡片必须能直接定位问题：

- User
- Node
- GPU index，例如 `GPU0, GPU3`
- PID，例如 `pid 1234` 或 `pid 1234 +2`
- Task
- Memory
- Recent GPU util
- Idle tail
- Last seen

如果一个作业跨多卡/多进程：

- 主卡片展示紧凑摘要。
- hover/title 或展开区域展示完整 GPU uuid 和 pid 列表。

## Off-hour Activity 模块

### 当前问题

现在像普通 KPI，意义不够明显，也不够有趣。

### 新方向

改成轻量“非工作时段小剧场”，保留中性但可以有一点调侃。

推荐标题：

```text
After-hours lab life
```

根据数据动态生成一句主文案：

```text
夜间为 0：昨晚很安静，GPU 也睡了个整觉
周末较高：周末算力没有休假
夜间较高：凌晨 0-6 点仍有训练在跑
都很低：这段时间大家作息相当健康
```

指标展示：

- Night jobs
- Weekend jobs
- Night GPU hours
- Weekend GPU hours
- Most active off-hour user

Top 用户可以用轻量标签：

- `Night owl`
- `Weekend regular`
- `Off-hour regulars`

注意：

- 文案不能变成评价用户行为。
- 不使用“摸鱼”“内卷”等强负面/强评价词。
- 如果某项为 0，不要让大面积卡片只显示 `0`，改成状态句。

## 前端模块拆分建议

Stage 2 继续拆分前端，不把所有逻辑堆到 `main.ts`。

建议结构：

```text
frontend/src/
  main.ts              realtime shell / route / cluster rendering
  format.ts            formatting and escaping helpers
  analytics/
    index.ts           analytics controller
    types.ts           API response types
    api.ts             fetch overview/node analytics
    rankings.ts        user/job rankings render
    anomalies.ts       anomaly panel render
    offHours.ts        off-hour panel render
    nodeHistory.ts     trend chart and heatmap render
```

第一步可先从 `analytics.ts` 继续拆，不必一次性拆到最细。

拆分边界：

- `main.ts` 不关心 analytics payload 结构。
- analytics controller 负责 fetch/cache/range state。
- node history 内部负责多选 GPU 的局部 DOM 更新。
- rankings / anomalies / off-hours 各自负责自己的展示口径。

## 图表库评估

第一版为了轻量和控制复杂度，使用原生 SVG / CSS。Stage 2 可以重新评估轻量图表库，前提是它确实减少重复造轮子，并且不会破坏低占用理念。

可接受标准：

- 打包体积小，最好只引入必要模块。
- 支持基础时间序列、axis、tooltip。
- 能方便控制多 GPU 线条的 opacity / highlight。
- 支持自定义 heatmap / band / rect 绘制，或至少不妨碍我们自绘热力图。
- 不强依赖复杂运行时、主题系统或状态管理。
- 不把前端交互变成大规模 diff / 重绘。

候选方向：

- `uPlot`：非常轻量，适合高性能时间序列；适合作为 GPU 曲线候选。
- `Observable Plot`：表达力强，API 简洁，但需要评估 bundle 体积和定制交互成本。
- `D3` 子模块：只取 scale / axis / shape 等小模块，适合保留自定义 SVG 渲染。

不建议第一优先级：

- ECharts / Plotly / full Grafana panel 级方案。能力强，但对当前需求偏重。

建议决策：

- 如果只需要 axis、scale、line path 和 tooltip，优先考虑 `D3` 子模块或继续自绘。
- 如果曲线交互后续需要 hover sync、性能优化、多 series 高亮，可以评估 `uPlot`。
- 热力图渐变 band 第一版仍建议自绘 SVG，因为需求很定制，库未必更省。

## 低占用数据处理策略

数据分析必须继续服务于“低占用”理念，不能因为历史看板影响实时监控。

### 后端查询

- 分析 API 只读 SQLite rollup 和 session 表，不访问实时采样路径。
- Overview 排行限制 Top N，例如用户 Top 20、作业 Top 20、异常 Top 20。
- Node 曲线按时间窗选择已有 rollup，再二次降采样，避免返回过多点。
- 热力图按目标列数聚合，不返回原始 bucket 大表。
- 异常检测固定 24h，避免默认 7d/30d 时扫描过多近期无关数据。
- 必要 SQL 增加或复用索引，例如：
  - `gpu_metric_rollups(node_id, bucket_seconds, bucket_start)`
  - `process_gpu_usages(node_id, first_seen_at, last_seen_at)`
  - `process_sessions(user, last_seen_at)`

### 后端计算

- 加权 GPU hours、job 合并、异常检测在 API 层聚合，不让浏览器拉大表。
- 同一次 API 请求内复用中间结果，例如 usage rows 只扫一次。
- 对 expensive 子查询保持窗口约束和 limit。
- 后续如果数据量增大，再考虑短 TTL 内存缓存，例如 10-30 秒，但第一版先保持简单。

### 前端渲染

- GPU 图例切换只更新 class，不触发 fetch，不重建热力图。
- 时间窗/指标变化才重新渲染对应图表。
- SVG 节点数量控制在合理范围：
  - 曲线每条线目标 300-800 点。
  - 热力图目标 12-42 列。
- 不在 WebSocket 每次实时更新时重新请求 analytics API。
- 对内容少的组件使用较小布局，减少无意义大面积 DOM 和视觉噪音。

### 运维边界

- DB 慢、DB 关闭、分析 API 失败，都不能影响 `/ws/cluster` 和 `/api/cluster/snapshot`。
- 前端分析模块失败时显示 disabled/error 状态，实时卡片继续工作。
- 维护任务继续负责 rollup / prune，避免 SQLite 无限增长。

## 实施顺序

1. 后端调整：
   - 异常占用固定 24h。
   - 异常 payload 增加 GPU index 和 pid 摘要。
   - 热力图 bucket 策略改为 `1h=5m, 24h=1h, 7d=6h, 30d=1d`。
   - 复核分析查询索引和 Top N 限制，避免大表扫描。

2. 前端 Node History：
   - 曲线 GPU 单选改多选。
   - 图例点击改局部 DOM class 更新。
   - 热力图不响应 GPU 选择。
   - 曲线和热力图改上下布局。
   - 热力图改连续渐变 band，并加时间横坐标。
   - 评估是否引入 `uPlot` 或 D3 子模块；不满足轻量和定制要求时继续自绘。

3. 前端 Overview：
   - 用户排行只展示加权 GPU hours，并命名为 GPU hours。
   - `Tasks` 改为 `Jobs`。
   - Models 保留为主要 GPU 型号。
   - 异常占用拆成独立 panel。
   - Off-hour 改成更有趣的洞察卡片。
   - 内容较少的模块不强行占满整行，按信息量选择宽度。

4. 测试：
   - 后端补异常 24h 固定窗口测试。
   - 后端补 heatmap bucket 策略测试。
   - 前端至少跑 `npm run build`。
   - 如引入可测纯函数，补轻量单元测试。

## 暂不做

- 不引入 ECharts / Plotly / Grafana panel 级重型库。
- 不做拖拽缩放和 brush。
- 不做后端按前端像素宽度动态 bucket。
- 不对 off-hour 做强评价或绩效化解释。
