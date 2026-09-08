# Changelog

All notable changes to Constella are documented in this file. Python
distribution versions follow PEP 440; Git tags use a hyphenated equivalent.

## [0.1.5] - 未发布 / Unreleased

### 新增 / Added

- 新增 Lab 个人活动面板，支持每日任务历史、GPU 小时归属统计、筛选、关联任务详情及最长 30 天的历史记录。
  Added a personal Lab activity dashboard with daily workload history, GPU-hour
  attribution, filters, linked job details, and up to thirty days of history.
- 新增只读用户引导流程，无需绑定 Linux 账号。
  Added read-only onboarding for users who do not need to bind a Linux account.

### 修复 / Fixed

- 浏览器休眠、锁屏挂起或网络中断后自动恢复实时监控，并丢弃已停用 WebSocket 连接的消息。
  Recover live monitoring after browser sleep, lock-screen suspension, or network
  interruption, and discard messages from retired WebSocket connections.
- 同步刷新节点历史曲线和热力图，合并重复请求；失败或卡住的请求可以重试，同时保留已显示的数据。
  Refresh Node history and heatmaps together, deduplicate matching requests, and
  allow failed or stalled requests to be retried without hiding existing data.
- 区分历史数据临时请求错误、无历史数据和存储未启用三种状态；为前端历史、快照及 Lab 身份请求增加超时限制。
  Distinguish temporary history errors from empty history and disabled storage;
  time out frontend history, snapshot, and Lab identity requests.
- 关闭 WebSocket 连接前先停止数据推送任务，避免客户端在刷新间隔内断开后仍向其发送数据。
  Stop WebSocket producers before closing connections and avoid sending after a
  client disconnects during the refresh interval.
- 将监控数据库写入移出管理器事件循环，历史查询使用独立只读连接，维护操作失败后按退避策略重试。
  Run telemetry database writes outside the manager event loop, use independent
  read-only history connections, and back off failed maintenance operations.
- 结合节点和用户名归属旧版活动记录，并将桌面服务排除在任务跟踪之外。
  Attribute legacy activity using both node and username, and exclude desktop
  services from workload tracking.

### 调整 / Changed

- 展开的每日活动最多显示 10 条任务记录，Lab 版本继续隐藏节点进程详情入口。
  Limit expanded activity days to ten workload rows and keep node process-detail
  entry points hidden in the Lab edition.
- 在临时源码副本中构建发布包，避免打包过程替换正在运行的源码部署所使用的前端资源。
  Build release packages in a temporary source copy so packaging cannot replace
  the frontend assets used by a running source deployment.

### 兼容性 / Compatibility

- 保留五个发行包的布局，以及现有 Agent HTTP/WebSocket API。
  Keeps the five distribution layout and existing agent HTTP/WebSocket APIs.
- 要求 Python 3.10 或更高版本，保留现有监控数据库和 Lab 数据库。
  Requires Python 3.10 or newer. Existing monitoring and Lab databases are retained.
- 当前为待审核版本；正式发布和创建 Git 版本标签须经审核。
  This is a prepared release; publication and a Git release tag require review.

## [0.1.4] - 2026-09-06

### Added

- Added the optional `constella-gpu-lab` distribution with Cloudflare Access
  identity verification, local roles, lifecycle administration, audit records,
  and an independent SQLite identity database.
- Added atomic, self-service Linux account binding across multiple selected
  nodes, backed by a capability-gated account lookup RPC on existing agents.
- Added numeric process UIDs to live snapshots and process-session history so
  workload attribution remains stable when usernames change.

### Security

- Added issuer, audience, signature, expiry, and application-type validation for
  Cloudflare Access assertions, plus fail-closed startup configuration.
- Added server-side role enforcement, Origin and request-marker CSRF checks,
  session-bounded WebSockets, account lookup limits, and immutable audit events.
- Preserved separate token authentication for Agent and high-resolution machine
  connections; Linux account lookup executes no shell commands and needs no root.

### Changed

- Added a generic core extension boundary and edition entry point so the public
  Web and backend packages remain independent of Lab-only code and dependencies.
- Updated the frontend build dependency lock to resolve published Browserslist
  security advisories.

## [0.1.3] - 2026-08-25

Final release of 0.1.3, incorporating the release candidate and the following
refinements.

### Added

- Added PCIe and capability-gated NVLink throughput to the Web and TUI
  Performance views.
- Added a bilingual in-product guide for performance metrics and their
  interpretation.

### Fixed

- Stabilized TUI Performance curves with fixed time bins so live samples scroll
  left instead of reflowing the full chart on every refresh.
- Corrected swapped NVML permission and buffer-size return codes that caused
  normal process queries to fall back to a slow `nvidia-smi` subprocess.

### Changed

- Refined the Web Performance workspace, Jobs surfaces, responsive layout, and
  chart presentation across desktop and mobile viewports.
- Reordered performance groups as Compute, Memory, Interconnect, and Non-Tensor
  Pipelines, with complete throughput labels and consistent MiB/s or GiB/s
  units.
- Send NVIDIA GPM `supported_metrics` on the first sample and capability changes
  instead of repeating the unchanged list in every agent snapshot.
- Cache slow-changing NVML device metadata for 60 seconds and prefer the v2
  memory query without issuing the legacy query first.

### Removed

- Removed retired single-node API shims and unreferenced backend compatibility
  helpers superseded by the cluster API and current collector paths.

## [0.1.3rc1] - 2026-08-23

First release candidate for 0.1.3.

### Added

- Added isolated NVIDIA NVML GPM collection with profile-aware metric groups.
- Added in-memory high-resolution performance curves and SQLite performance
  rollups with independent retention controls.
- Added performance status, history, and job-level API endpoints.
- Added the Web performance workspace with metric selection, summaries, and
  interactive charts.
- Added a keyboard-first TUI Performance view with seven compact Braille curves,
  range and GPU navigation, live pause/resume, summaries, and capability states.

### Changed

- Split deployment responsibilities across four PyPI distributions:
  `constella-gpu` for the complete installation, `constella-gpu-web` for the
  central Web service, `constella-gpu-backend` for the API and collectors, and
  `constella-gpu-tui` for the standalone terminal client.
- Removed the backend dependency from `constella-gpu-tui`; it now installs only
  the client-side Textual and WebSocket runtime.
- Changed TUI History to overlay every GPU on the selected node on shared
  utilization and memory charts, with eight hue-separated colors and GPU
  highlighting.
- Hardened high-resolution downsampling, GPM sampling isolation, chart refresh
  costs, and remote-agent runtime packaging.

### Compatibility

- Requires Python 3.10 or newer.
- Supports NVIDIA GPU and Ascend NPU clusters. NVIDIA GPM metrics require
  compatible NVML hardware, driver, and metric profiles.
- The TUI continues to use the existing manager HTTP and `/ws/cluster` APIs.

[0.1.5]: https://github.com/kuma-loong/Constella/releases/tag/v0.1.5
[0.1.4]: https://github.com/kuma-loong/Constella/releases/tag/v0.1.4
[0.1.3]: https://github.com/kuma-loong/Constella/releases/tag/v0.1.3
[0.1.3rc1]: https://github.com/kuma-loong/Constella/releases/tag/v0.1.3-rc.1
