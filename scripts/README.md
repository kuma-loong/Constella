# Scripts

脚本按用途分类，全部从项目根目录执行也可以直接通过相对路径执行。

```text
scripts/service/       本机 manager + local agent 安装、启动、状态、停止
scripts/cluster/       远端 GPU agent 启动、状态、停止
scripts/tunnel/        Cloudflare Tunnel 启动、状态、停止
scripts/maintenance/   SQLite 历史库维护
scripts/dev/           开发和采样 benchmark
```

常用入口：

```bash
./scripts/service/setup.sh
./scripts/service/start.sh
./scripts/service/start.sh --device ascend
LOCAL_AGENT=0 ./scripts/service/start.sh
./scripts/cluster/start.sh
./scripts/cluster/status.sh
./scripts/maintenance/db.sh
```

`scripts/service/start.sh` 默认启动 manager 和本机 NVIDIA agent；Ascend 主机使用
`--device ascend`。脚本会在需要时自动生成 `run/agent-token`。manager pid/log 使用
`run/constella.pid` 和 `logs/constella.log`；本机 agent 使用 `run/local-agent.pid`、
`logs/local-agent.log` 和 `run/local-agent-state.json`。

SQLite 历史库默认关闭。启用后，`scripts/maintenance/db.sh` 运行 `uv run constella db maintain`，负责关闭 stale session、聚合 rollup、清理过期 rollup 和低频 raw snapshot。

## Agent 守护

`scripts/service/agent_watchdog.py` 每 5 分钟检查配置中的远端节点，在 Manager 健康时通过 SSH 启动离线节点的 Agent。

```bash
.venv/bin/python scripts/service/agent_watchdog.py start \
  --nodes run/nodes.internal.yaml --interval 300
.venv/bin/python scripts/service/agent_watchdog.py status
.venv/bin/python scripts/service/agent_watchdog.py stop
```

检查结果：`run/agent-watchdog-state.json`；日志：`logs/agent-watchdog.log`。
`service/stop.sh` 会先停止守护，`service/status.sh` 会显示守护状态。维护远端节点前先停止守护，完成后再启动。
