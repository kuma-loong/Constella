<p align="center">
  <img src="frontend/public/logo-readme.svg" alt="Constella logo" width="260">
</p>

<h1 align="center">Constella</h1>

<p align="center">
  <strong>轻量级异构加速器集群监控与任务历史追踪</strong>
</p>

<p align="center">
  今天监控，明天复盘。
</p>

<div align="center" id="constella-badges">

[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![NVIDIA NVML](https://img.shields.io/badge/NVIDIA-NVML-76B900?logo=nvidia&logoColor=white)](https://docs.nvidia.com/deploy/nvml-api/)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/kuma-loong/Constella)

</div>

<p align="center"><a href="README.md">English</a> | 简体中文</p>

<div align="center">
  <blockquote>
    <em>如同星座中的群星，<strong>Constella</strong> 将独立的加速器节点汇聚成一个可观测的集群。</em>
  </blockquote>
</div>

Constella 是一个面向实验室、AI 团队和个人计算服务器的轻量级加速器监控平台，
原生支持多种硬件组成的异构集群；0.1.3 版本支持 NVIDIA GPU 与 Ascend NPU。

不同于只能查看当前状态的终端工具，Constella 会自动记录加速器任务历史，
方便在训练或推理结束后回看曲线。它支持单机和小型异构集群，不需要先部署
一整套 Prometheus/Grafana 监控系统。

## 截图

<table>
  <tr>
    <th>集群总览</th>
    <th>GPU 与进程详情</th>
  </tr>
  <tr>
    <td><img src="docs/assets/01-overview-realtime-cluster.png" alt="Constella 集群总览"></td>
    <td><img src="docs/assets/02-node-gpu-process-detail.png" alt="Constella GPU 进程详情"></td>
  </tr>
</table>

**任务曲线**

<p align="center">
  <img src="docs/assets/05-job-curve-interaction.gif" alt="Constella 任务曲线交互">
</p>

## 功能

**任务历史追踪**

- 自动记录已完成任务的 GPU 曲线。
- 回看最近 7 天内的训练和推理任务。
- 短任务优先使用高分辨率内存缓存，持久化历史由 SQLite rollup 提供。

**加速器监控**

- 在一个 Web UI 中监控单机或小型 GPU 集群。
- 查看 GPU 利用率、显存、功耗、温度、时钟、进程、用户、PID 和命令指纹。
- 在支持的 NVIDIA GPU 上，通过 NVML GPM 查看 SM 活跃度、占用率、Tensor Core
  合计活跃度、DRAM 带宽以及非 Tensor FP16/FP32/FP64 流水线。
- NVIDIA 优先使用 NVML 并以 `nvidia-smi` 兜底；Ascend 优先使用 DCMI
  并以 `npu-smi` 兜底。

**多用户分析**

- 查看用户 GPU 使用排行、作业用时排行、节点趋势和按时间窗自适应的热力图。
- 检测低利用率占用和非工作时段活动。
- 即使不启用历史分析，实时监控也可以正常工作。

**轻量部署**

- 不需要 root 权限、system service、Prometheus 或 Grafana。
- 一个 manager 进程接收本机和远端加速器 agent 的数据。
- 远端节点只需要 Python、对应厂商驱动/运行时和 SSH 访问权限。

## 为什么是 Constella？

| 能力 | nvitop | Prometheus/Grafana | Constella |
| --- | --- | --- | --- |
| 实时 GPU 状态 | 支持 | 支持 | 支持 |
| 任务历史追踪 | 不支持 | 需要配置 | 内置 |
| 小型集群视图 | 有限 | 支持 | 支持 |
| 轻量部署 | 支持 | 不轻量 | 支持 |
| Web UI | 不支持 | 支持 | 支持 |
| 用户/作业分析 | 不支持 | 需要自定义看板 | 内置 |

Constella 介于终端监控和完整可观测性系统之间：比 `nvitop` 更适合历史追踪和多人共享，又比 Prometheus/Grafana 更适合小实验室快速部署。

## 快速开始

安装 PyPI 发行包并启动本机托管服务：

```bash
pip install constella-gpu
constella service start
```

`constella-gpu` 是包含后端、Web UI 与 TUI 的完整安装包。只需要部分功能时，
可安装 `constella-gpu-web`、`constella-gpu-tui` 或
`constella-gpu-backend`；功能矩阵见[打包说明](docs/PACKAGING.md)。

Ascend 主机使用 `constella service start --device ascend`。

从源码检出启动 manager 和本机加速器 agent：

```bash
cd Constella
./scripts/service/setup.sh
./scripts/service/start.sh
```

Ascend 主机需要显式选择硬件后端：

```bash
./scripts/service/start.sh --device ascend
```

打开：

```text
http://127.0.0.1:8765/overview
```

也可以直接使用键盘优先的终端界面：

```bash
constella tui
```

TUI 与 Web UI 复用同一条实时集群数据流。远程管理端可通过
`constella tui --url https://gpu.example.com` 连接，也可以使用等价入口
`constella-tui`。

TUI 的第五个视图以紧凑点图展示 NVIDIA GPM 高分辨率性能指标，并与其他视图
复用节点、GPU 和时间范围交互。

如果服务运行在远端服务器，在本地电脑转发端口：

```bash
ssh -N -L 8765:127.0.0.1:8765 <user>@<server>
```

需要任务历史和分析看板时启用 SQLite：

```bash
DB_PATH=run/constella.db ./scripts/service/start.sh
```

如果希望把短作业高分辨率曲线缓存拆到独立进程，启动 highres sidecar：

```bash
DB_PATH=run/constella.db HIGHRES_SIDECAR=1 ./scripts/service/start.sh
```

sidecar 默认监听 `127.0.0.1:8766`，订阅 manager 的 `ws://127.0.0.1:8765/api/highres/stream`。普通部署可以先不启用 sidecar，manager 进程内置的 `/api/highres/*` 接口仍可工作。

NVIDIA agent 默认自动探测 NVML GPM，且性能 provider 与基础 NVML 路径隔离。
`CONSTELLA_NVML_GPM=off` 可关闭采集；
`CONSTELLA_NVIDIA_GPM_ROLLUP=off` 可保留实时性能数据但停止持久化。Ascend agent
不会初始化 NVIDIA provider。`CONSTELLA_NVIDIA_GPM_HIGHRES=off` 可单独关闭内存中的
高精度性能曲线。

## 集群模式

准备远端节点清单：

```bash
cp docs/nodes.example.yaml nodes.yaml
```

编辑 `manager_url`、`manager_hostname` 以及每个节点的 `device`（`nvidia` 或
`ascend`），并配置 manager 到各节点的 SSH 免密访问。

```mermaid
flowchart LR
  M["Manager<br/>FastAPI + Web UI"] -->|"SSH 安装/控制"| A["gpu-node-a<br/>agent"]
  M -->|"SSH 安装/控制"| B["gpu-node-b<br/>agent"]
  M -->|"SSH 安装/控制"| C["gpu-node-c<br/>agent"]
  A -->|"WebSocket 采样数据"| M
  B -->|"WebSocket 采样数据"| M
  C -->|"WebSocket 采样数据"| M
```

启动远端 GPU agents：

```bash
./scripts/cluster/start.sh
```

- `scripts/service/start.sh` 首次启动本机 agent 时会自动创建 `run/agent-token`，`scripts/cluster/start.sh` 使用同一个 token 配置远端 agent。
- 如果 manager 主机不采集本机 GPU，启动时加 `LOCAL_AGENT=0`。
- 远端节点不需要安装 `uv`，manager 会同步最小 agent runtime。

## Ascend NPU 支持

Constella 使用两条显式选择、互不串行回退的硬件链路：

- `nvidia`：NVML，失败后回退 `nvidia-smi`。
- `ascend`：DCMI（`libdcmi.so`），失败后回退 `npu-smi`。

DCMI 后端采集 AICore/HBM 利用率、内存、温度、功耗、PCI 标识、驱动/DCMI
版本和进程显存。
双 Die 设备仍按每个 Die 一个卡片展示。API 增加 `card_id`、`die_id`、
`card_count` 和 `accelerator_count`；实时功耗和额定功耗按物理卡只统计一次，
跨 Die 的相同 PID 按一个活跃进程统计。

## 架构

```mermaid
flowchart LR
  LA["本机 agent<br/>显式选择设备链路"] -->|"WS /api/agents/ws"| M["Manager<br/>FastAPI ingest"]
  RA["远端 agents<br/>NVML→nvidia-smi 或 DCMI→npu-smi"] -->|"WS /api/agents/ws"| M
  M --> S["ClusterState<br/>latest snapshots + 120 点短历史"]
  S --> API["HTTP /api/cluster/snapshot"]
  S --> WS["WebSocket /ws/cluster"]
  S -.可选.-> DB["SQLite<br/>rollups + sessions"]
  DB -.可选.-> AN["Analytics + job curves"]
  S -.可选.-> HR["Highres cache / sidecar"]
  API --> UI["Vite TypeScript UI"]
  WS --> UI
  AN --> UI
  HR --> UI
```

manager 不直接采样 GPU；本机节点和远端节点都通过同一条 agent WebSocket 路径上报当前采样点。SQLite、分析 API 和高分辨率作业曲线都是可选旁路，不阻塞实时快照。完整设计见 [设计说明](docs/DESIGN.md)。

## 文档

- [设计说明](docs/DESIGN.md)：架构、数据路径、低开销策略和数据契约。
- [运维手册](docs/OPERATIONS.md)：启动、访问、集群 agent 管理、状态和验证命令。
- [SQLite 历史库](docs/HISTORY.md)：持久化、rollup、维护和作业曲线。
- [NVIDIA GPM 性能监控](docs/NVML_GPM_METRICS_zh.md)：支持条件、指标解读、数据保留和故障排查。
- [Cloudflare Tunnel](docs/CLOUD_TUNNEL.md)：无入站端口的域名访问方案。
- [节点清单示例](docs/nodes.example.yaml)：远端 agent 的 `nodes.yaml` 模板。
- [PyPI CLI](docs/PYPI_CLI.md)：安装包的 service、probe、agent 和 cluster 命令。
- [打包说明](docs/PACKAGING.md)：构建并安全烟测 wheel 和源码包。
- [脚本说明](scripts/README.md)：service、cluster、tunnel、maintenance、dev 脚本入口。

## 项目结构

```text
packages/backend/       Python 后端、agent、cluster manager、采样器、API/WebSocket
packages/web/           可安装的生产 Web 静态资源
packages/tui/           Textual 终端客户端、主题与使用说明
src/constella_gpu/      完整发行包的元数据包
frontend/               Vite + TypeScript 前端
scripts/                按 service、cluster、tunnel、maintenance、dev 分类的脚本
docs/                   设计和运维文档
tests/                  单元测试
```

## 开发

```bash
uv sync
uv run pytest

cd frontend
npm install
npm run build
```

前端开发模式：

```bash
cd frontend
npm run dev
```

发布时运行 `scripts/package/build.sh`，它会把前端构建到 Web 发行包中，并生成
四组 wheel 与源码包。

## API

- `GET /api/health`：服务健康状态。
- `GET /api/cluster/snapshot`：当前集群快照。
- `GET /api/settings`：当前运行时设置。
- `PATCH /api/settings`：更新全局刷新率。
- `WS /ws/cluster`：实时集群快照流。
- `WS /api/agents/ws`：agent 上报通道。
- `GET /api/history/gpu`：可选 GPU 历史指标。
- `GET /api/history/tasks`：可选任务历史。
- `GET /api/users`：可选用户任务聚合。
- `GET /api/analytics/overview`：可选 Overview 历史分析。
- `GET /api/analytics/node/{node_id}`：可选节点历史曲线和热力图。
- `GET /api/highres/status`：高分辨率内存缓存状态。
- `GET /api/highres/jobs`：作业搜索。
- `GET /api/highres/jobs/{job_key}`：作业详情。
- `GET /api/highres/jobs/{job_key}/gpu`：作业 GPU 曲线。
- `GET /api/highres/performance`：NVIDIA GPM 高精度性能曲线和区间统计。
- `GET /api/highres/jobs/{job_key}/performance`：作业所在设备的性能曲线。
- `GET /api/docs`：FastAPI OpenAPI 文档。

未启用 SQLite 时，历史、分析和作业曲线搜索 API 返回 `enabled:false`；实时集群监控仍然通过 `/api/cluster/snapshot` 和 `/ws/cluster` 工作。

## License

[MIT](LICENSE)
