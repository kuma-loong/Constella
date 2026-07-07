# 运维手册

## 安装

```bash
cd Constella
./scripts/service/setup.sh
```

## 后台启动

```bash
./scripts/service/start.sh
```

可配置项：

```bash
HOST=127.0.0.1 PORT=8765 REFRESH=1.0 PROCESS_REFRESH=3.0 ./scripts/service/start.sh
```

默认会启动两个后台进程：

```text
manager:     constella serve
local agent: constella agent --manager-url ws://127.0.0.1:8765/api/agents/ws
```

如果本机只作为 manager，不采集本机 GPU：

```bash
LOCAL_AGENT=0 ./scripts/service/start.sh
```

本机 agent 开启时，脚本会在缺省情况下自动创建 `run/agent-token`，权限为 `600`。也可以显式配置 agent token：

```bash
AGENT_TOKEN_FILE=run/agent-token ./scripts/service/start.sh
```

manager 日志写入 `logs/constella.log`，PID 写入 `run/constella.pid`。本机 agent 日志写入 `logs/local-agent.log`，PID 写入 `run/local-agent.pid`，状态文件写入 `run/local-agent-state.json`。

## 访问

推荐只绑定本机地址，通过 SSH 转发：

```bash
ssh -N -L 8765:127.0.0.1:8765 <user>@<server>
```

浏览器访问：

```text
http://127.0.0.1:8765/overview
```

## 集群 agent 管理

准备 manager agent token。若本机 agent 已通过 `scripts/service/start.sh` 启动，通常已经存在 `run/agent-token`：

```bash
mkdir -p run
umask 077
printf '%s\n' 'replace-with-a-random-token' > run/agent-token
chmod 600 run/agent-token
AGENT_TOKEN_FILE=run/agent-token ./scripts/service/start.sh
```

准备节点清单：

```bash
cp docs/nodes.example.yaml nodes.yaml
```

`nodes.yaml` 中的 `manager_url` 必须是 GPU 节点能访问到的 manager WebSocket 地址，例如：

```text
ws://manager-host:8765/api/agents/ws
```

`manager_hostname` 是 manager 主机本机 agent 在前端中的显示名，`scripts/service/start.sh` 会把它作为默认 `LOCAL_AGENT_NODE_ID`。也可以用环境变量临时覆盖：

```bash
MANAGER_HOSTNAME=H100 ./scripts/service/start.sh
```

或者直接设置本机 agent 节点名：

```bash
LOCAL_AGENT_NODE_ID=H100 ./scripts/service/start.sh
```

启动、状态、停止：

```bash
./scripts/cluster/start.sh
./scripts/cluster/status.sh
./scripts/cluster/stop.sh
```

重复执行 `./scripts/cluster/start.sh` 是幂等的：远端 pid 存活时返回 running；pid 过期时清理后重启。

普通用户部署限制：

- 不使用 sudo，不写 `/etc`，不安装 system service。
- GPU 节点不需要安装 `uv` 或 Python runtime；manager 会同步 Rust release binary。
- agent 默认写入 `~/.constella/run/agent.pid`、`~/.constella/logs/agent.log`、`~/.constella/run/agent-state.json`。
- 节点重启后 agent 不保证自动恢复；重新执行 `./scripts/cluster/start.sh` 即可。
- token 通过 stdin 写入远端 env 文件，不放在 SSH 命令行参数中。

## 可选组件

- SQLite 历史库默认关闭，只在需要持久化 GPU/任务历史和分析看板时启用。配置和维护见 [SQLite History](HISTORY.md)。
- Cloudflare Tunnel 是可选部署方式，用于在不开放服务器入站端口的情况下绑定域名访问。配置见 [Cloudflare Tunnel](CLOUD_TUNNEL.md)。

启用 SQLite 时显式传入 `DB_PATH`：

```bash
DB_PATH=run/constella.db ./scripts/service/start.sh
```

数据库路径由部署环境决定，项目脚本不假设系统盘或专用数据盘。Rust manager 会通过后台 DB writer 异步写入快照、生成 20s/2m/1h rollup，并定期清理过期数据。可按部署压力调整：

```bash
DB_PATH=run/constella.db DB_QUEUE_SIZE=2048 RAW_SNAPSHOT_SECONDS=30 ./scripts/service/start.sh
```

未启用 SQLite 时，历史分析 API 返回 `enabled:false`，不影响实时监控：

```bash
curl -s http://127.0.0.1:8765/api/analytics/overview
curl -s http://127.0.0.1:8765/api/analytics/node/<node_id>
```

作业曲线页面位于：

```text
http://127.0.0.1:8765/jobs
```

Rust manager 内置高精度缓存和 `/api/highres/*` 接口，不需要单独 sidecar 进程。
如需保护 `/api/highres/stream` WebSocket，可设置 `HIGHRES_TOKEN_FILE`；客户端使用 `Authorization: Bearer <token>` 访问。

相关状态与接口验证：

```bash
curl -s http://127.0.0.1:8765/api/highres/status
curl -s 'http://127.0.0.1:8765/api/highres/jobs?limit=20'
```

`/api/highres/status` 即使未启用 SQLite 也可查看内存 ring buffer 状态；作业搜索和曲线详情需要 `DB_PATH`，因为作业元数据来自 SQLite。

## 状态、停止、重启

```bash
./scripts/service/status.sh
./scripts/service/stop.sh
./scripts/service/start.sh
```

## 验证采样

```bash
target/release/constella probe --pretty
COUNT=20 ./scripts/dev/bench_probe.sh
```

正常情况下 `probe` 的 `source` 为 `nvml`。如果 NVML 不可用但 `nvidia-smi` 可用，source 会回退为 `nvidia-smi`；如果当前机器没有可用 NVIDIA 工具，probe 会返回错误快照。服务模式下，本机采样警告在 `logs/local-agent.log` 中。

## 验证集群 API

```bash
curl -s http://127.0.0.1:8765/api/cluster/snapshot
```
