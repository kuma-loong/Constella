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
LOCAL_AGENT=1 ./scripts/service/start.sh
./scripts/cluster/start.sh
./scripts/cluster/status.sh
./scripts/maintenance/db.sh
```

`scripts/service/setup.sh` 构建 Rust release binary，并构建前端静态资源。`scripts/service/start.sh` 启动 Rust manager，默认监听 `127.0.0.1:8765`，pid/log 使用 `run/constella.pid` 和 `logs/constella.log`。`LOCAL_AGENT=1` 会启动 Rust local agent，通过 `/api/agents/ws` 接入 manager，pid/state/log 使用 `run/local-agent.pid`、`run/local-agent-state.json` 和 `logs/local-agent.log`。默认仍关闭 local agent，便于迁移验证时避免影响已有 8765 服务。

SQLite 历史库默认关闭。设置 `DB_PATH=run/constella.db` 后，Rust manager 会写入节点、GPU、任务和历史查询所需数据。`scripts/maintenance/db.sh` 调用 Rust `constella db maintain`，用于关闭过期任务 session、聚合 rollup 并清理过期 raw/rollup 数据。
