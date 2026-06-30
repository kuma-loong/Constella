# Scripts

脚本按用途分类，全部从项目根目录执行也可以直接通过相对路径执行。

```text
scripts/service/       本机 manager 安装、启动、状态、停止
scripts/cluster/       远端 GPU agent 启动、状态、停止
scripts/tunnel/        Cloudflare Tunnel 启动、状态、停止
scripts/maintenance/   SQLite 历史库维护
scripts/dev/           开发和采样 benchmark
```

常用入口：

```bash
./scripts/service/setup.sh
AGENT_TOKEN_FILE=run/agent-token ./scripts/service/start.sh
./scripts/cluster/start.sh
./scripts/cluster/status.sh
./scripts/maintenance/db.sh
```
