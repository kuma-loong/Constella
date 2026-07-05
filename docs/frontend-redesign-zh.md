# Constella 前端重构设计与实现说明

## 设计定位

本次前端重构把 Constella 定位为高频使用的 GPU 集群运行台，而不是展示型网页。设计目标是让集群健康、GPU 利用率、显存、功耗、任务和历史分析在同一界面中保持清晰层级，适合长时间打开和反复扫描。

设计参数：

- `DESIGN_VARIANCE: 5`：保留清晰网格和少量不对称信息布局。
- `MOTION_INTENSITY: 3`：只使用 hover、active、宽度变化等轻交互，不引入滚动劫持或复杂动效。
- `VISUAL_DENSITY: 7`：信息密度偏高，但用分组、留白和折叠降低认知负担。

## 现状审计

原前端功能完整，包含实时 WebSocket、集群概览、节点详情、任务表、历史分析和刷新率设置。主要问题集中在设计系统和体验一致性：

- 只有亮色模式，长时间使用时视觉负担较高。
- 使用系统字体链和较普通的卡片堆叠，品牌识别度弱。
- 多个强调色同时承担装饰和语义职责，状态层级不够稳定。
- 顶部导航在节点数量增加时容易拥挤。
- 任务表等非核心区域始终展开，节点页面信息压力偏大。
- 空态、禁用态、加载态有基础实现，但视觉表达较弱。

## 重构内容

### 视觉系统

- 引入自托管 `Geist Variable` 和 `Geist Mono Variable` 字体，避免依赖本机字体环境。
- 使用 CSS 变量建立完整 token：背景、表面、边框、文本、状态色、阴影、圆角和焦点环。
- 支持 `system`、`light`、`dark` 三种主题模式，默认跟随系统。
- 暗色模式不是简单反色，而是重新定义表面层级和状态色，保证对比度和可读性。
- 统一圆角体系：容器 `14px`，内部控件 `9px`。

### 布局与信息层级

- 顶部改为紧凑 command bar：品牌、节点导航、刷新频率、主题、实时状态、暂停和手动刷新同层呈现。
- 概览页保留五个核心指标，下面是集群 fabric 节点矩阵和历史分析。
- 节点页保留指标摘要、GPU 卡片、任务表和节点历史。
- 任务表加入折叠控制，折叠状态保存在 `localStorage`。
- 所有多列布局都定义了移动端单列降级。

### 交互与状态

- 主题按钮循环切换 `system -> light -> dark`。
- 刷新频率继续调用 `PATCH /api/settings`，不改变后端契约。
- WebSocket 仍使用 `/ws/cluster`，手动刷新仍使用 `/api/cluster/snapshot`。
- 历史分析仍使用 `/api/analytics/overview` 和 `/api/analytics/node/{node_id}`。
- 低运动强度策略：只使用 `transform`、`opacity`、颜色和宽度过渡，并尊重 `prefers-reduced-motion`。
- 增加 skip link 和可见 focus ring，改善键盘导航。

## API 与后端边界

本次重构没有改动后端、数据库、cluster manager 或 agent 逻辑。前端保留以下 API：

- `GET /api/cluster/snapshot`
- `GET /api/settings`
- `PATCH /api/settings`
- `WS /ws/cluster`
- `GET /api/analytics/overview`
- `GET /api/analytics/node/{node_id}`

未启用 SQLite 时，历史分析继续按原逻辑显示禁用态，不影响实时监控。

## 新增依赖

仅新增两个字体包：

- `@fontsource-variable/geist`
- `@fontsource-variable/geist-mono`

没有引入 React、Vue、GSAP、Motion 或大型组件库。这样可以保留当前 Vite + TypeScript 的轻量运行模型，避免增加过高开销。

## 构建与部署

前端构建命令：

```bash
cd frontend
npm run build
```

构建产物输出到：

```text
frontend/dist
```

生产服务仍由 FastAPI 托管 `frontend/dist`。重新构建后，启动或重启 Constella manager 即可加载新版前端。

本地预览命令：

```bash
cd frontend
npm run preview
```

## 维护约定

- 新增页面或组件优先使用现有 CSS token，不直接写固定色值。
- 新增非核心区域建议使用 `data-collapse-section` 和 `data-collapse-target` 接入现有折叠模式。
- 新增状态色只用于语义状态，不作为装饰色。
- 不在前端假设 GPU 数量、节点名称、刷新区间或测试机器配置。
- 修改 API 字段前必须同步后端契约和文档。
