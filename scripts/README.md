# 运维脚本

按[部署指南](../docs/DEPLOYMENT.md)准备发布包和配置。仓库脚本默认使用 `run/` 作为运行目录。运行目录在其他位置时，设置 `CONSTELLA_RUNTIME`。

| 操作 | 入口 |
| --- | --- |
| 构建发布包 | `scripts/package/build.sh` |
| 安装本地二进制和前端 | `scripts/service/setup.sh RUNTIME_DIR AGENT_ARCHIVE MANAGER_ARCHIVE` |
| 启动、状态及停止 | `scripts/service/start.sh`、`scripts/service/status.sh`、`scripts/service/stop.sh` |
| 仅管理远端 Agent | `scripts/cluster/start.sh`、`scripts/cluster/status.sh`、`scripts/cluster/stop.sh` |
| 仅管理 Tunnel | `scripts/tunnel/start.sh`、`scripts/tunnel/status.sh`、`scripts/tunnel/stop.sh` |
| 在线备份 | `scripts/maintenance/db.sh backup DEST` 或 `backup-lab DEST` |

发布包安装后，可在运行目录直接使用 `bin/stack.sh check`、`start`、`status` 和 `stop`。看门狗随整套服务启动，也可随远端 Agent 一起单独启停。Manager 自行维护历史库。

带 `-legacy.sh` 后缀的入口和 `scripts/service/agent_watchdog.py` 用于旧 Python 部署。
