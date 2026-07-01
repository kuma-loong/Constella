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

集群 manager 可额外配置 agent token：

```bash
AGENT_TOKEN_FILE=run/agent-token ./scripts/service/start.sh
```

日志写入 `logs/constella.log`，PID 写入 `run/constella.pid`。

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

准备 manager agent token：

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

`manager_hostname` 是管理节点在前端中的显示名；未设置 `CONSTELLA_NODE_ID` 时，它也会作为本机 manager 节点名，因此对应详情页为 `/nodes/<manager_hostname>`。也可以用环境变量临时覆盖：

```bash
MANAGER_HOSTNAME=H100 ./scripts/service/start.sh
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
- GPU 节点不需要安装 `uv`；只要求 `python3 >= 3.10`。manager 会同步最小 agent runtime。
- agent 默认写入 `~/.constella/run/agent.pid`、`~/.constella/logs/agent.log`、`~/.constella/run/agent-state.json`。
- 节点重启后 agent 不保证自动恢复；重新执行 `./scripts/cluster/start.sh` 即可。
- token 通过 stdin 写入远端 env 文件，不放在 SSH 命令行参数中。

## 可选组件

- SQLite 历史库默认关闭，只在需要持久化 GPU/任务历史时启用。配置和维护见 [SQLite History](HISTORY.md)。
- Cloudflare Tunnel 是可选部署方式，用于在不开放服务器入站端口的情况下绑定域名访问。配置见 [Cloudflare Tunnel](CLOUD_TUNNEL.md)。

## 状态、停止、重启

```bash
./scripts/service/status.sh
./scripts/service/stop.sh
./scripts/service/start.sh
```

## 验证采样

```bash
uv run constella probe --pretty
COUNT=20 ./scripts/dev/bench_probe.sh
```

正常情况下 `probe` 的 `source` 为 `nvml`。如果为 `nvidia-smi`，说明 NVML 路径失败但兜底仍可用；查看 `logs/constella.log` 中的警告。

## 验证集群 API

```bash
curl -s http://127.0.0.1:8765/api/cluster/snapshot
```
