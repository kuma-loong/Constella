# Changelog

All notable changes to Constella are documented in this file. Python
distribution versions follow PEP 440; Git tags use a hyphenated equivalent.

## [0.1.4] - 2026-09-09

### English

#### Constella Lab: monitoring with a user system

Constella Lab brings identity, user management, and personal workload history to
shared GPU clusters. It is available as the optional `constella-gpu-lab` package;
the standard monitoring editions remain independent of the Lab user system.

- **Cloudflare Access sign-in:** connect Lab to Cloudflare Access for authenticated
  access, including email one-time PIN when configured in Access. Lab validates
  signed identities and requires a valid session for protected pages and APIs.
- **Users, roles, and administration:** manage read-only users, members, and
  administrators, with account enable/disable controls, binding management, and
  audit records for administrative changes.
- **Self-service account binding:** link a Lab identity to Linux accounts on one
  or more cluster nodes to attribute workloads to their owner. Users who only
  need to view monitoring can choose read-only access without binding an account.
- **Personal activity dashboard:** review daily workloads and GPU-hour usage,
  filter activity, and open linked job details across up to 30 days of history.

#### Monitoring and usability

- **Reliable recovery after sleep or network interruptions:** live monitoring
  reconnects automatically, and manual refresh reloads both Node history and
  heatmaps. Temporary request failures remain retryable without clearing
  previously loaded history.
- **More responsive historical queries:** database writes and history reads are
  isolated to reduce interference with live monitoring, with improved recovery
  from database maintenance failures.
- **Clearer node diagnostics:** inspect process details and abnormal GPU memory
  occupancy in the standard Web edition, with improved mobile Performance views
  and page navigation.

### 中文

#### Constella Lab：带用户系统的集群监控版本

Constella Lab 面向多人共享 GPU 集群，在监控基础上提供身份认证、用户管理和个人任务历史。
通过独立的可选发行包 `constella-gpu-lab` 安装，标准监控版本保持独立，不依赖 Lab 用户系统。

- **Cloudflare Access 登录：** 接入 Cloudflare Access 身份认证，支持在 Access 中配置邮箱一次性验证码登录。Lab 校验签名身份，受保护页面和 API 均要求有效会话。
- **用户、角色与管理后台：** 提供只读用户、成员和管理员角色，支持账号启用与停用、绑定关系管理，以及管理操作的审计记录。
- **自助绑定 Linux 账号：** 用户可将 Lab 身份绑定到一个或多个集群节点上的 Linux 账号，将任务归属到本人；仅需查看监控的用户可选择只读访问，无需绑定账号。
- **个人活动面板：** 按天查看任务与 GPU 小时用量，筛选活动并进入关联任务详情，支持最长 30 天的历史记录。

#### 监控体验与稳定性

- **休眠和断网后可靠恢复：** 浏览器恢复后自动重连实时监控，手动刷新同步更新节点历史曲线与热力图；临时请求失败后可以重试，并保留已加载的历史数据。
- **历史查询更流畅：** 隔离数据库写入与历史读取，减少对实时监控的干扰，并改善数据库维护失败后的恢复能力。
- **节点排障更直观：** 标准 Web 版本支持进程详情与异常 GPU 显存占用提示，并改善移动端 Performance 页面和页面导航体验。

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

[0.1.4]: https://github.com/kuma-loong/Constella/releases/tag/v0.1.4
[0.1.3]: https://github.com/kuma-loong/Constella/releases/tag/v0.1.3
[0.1.3rc1]: https://github.com/kuma-loong/Constella/releases/tag/v0.1.3-rc.1
