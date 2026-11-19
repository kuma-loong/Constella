# Constella deployment / 部署指南

## Build and install

Build on Linux with the same architecture as the deployment host. Use a build environment whose glibc is no newer than the oldest deployment host. The build uses Rust, a C compiler, Node.js, and npm. The resulting binaries use the host C runtime. Agent loads the installed NVIDIA NVML or Ascend DCMI driver library as needed. If the direct driver interface fails, Agent falls back to the installed vendor CLI and retries the library after two minutes. Manager uses bundled SQLite and serves the packaged frontend.

From the repository root, run `scripts/package/build.sh`. The script places the Agent, community Manager, and Lab Manager archives and SHA-256 manifest under `target/constella-release`. An optional first argument chooses another output directory. Verify the matching platform manifest with `sha256sum -c SHA256SUMS-<version>-linux-<arch>` before extracting.

For cross-building Linux ARM64 on Linux x86_64, install the Rust `aarch64-unknown-linux-gnu` target and an ARM64 GNU compiler and sysroot. Then run:

```bash
CONSTELLA_RUST_TARGET=aarch64-unknown-linux-gnu \
  CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc \
  CC_aarch64_unknown_linux_gnu=aarch64-linux-gnu-gcc \
  scripts/package/build.sh
```

The included user systemd units expect binaries and Manager's `web/` directory in `~/.local/lib/constella/`, environment files in `~/.config/constella/`, and units in `~/.config/systemd/user/`. Use absolute paths inside environment files. Set directory permissions to `0700` and secret file permissions to `0600`. Use an existing service account with GPU access and sufficient access to the monitored processes. For boot startup of user units, configure lingering for that service account if your installation uses it.

### Operate a cluster without systemd

From a source checkout, `scripts/service/setup.sh RUNTIME_DIR AGENT_ARCHIVE MANAGER_OR_LAB_ARCHIVE` installs matching packages without starting a service.

Put the Manager and Agent binaries and the Manager archive's `ops/stack.sh` and `ops/process.sh` in the runtime directory's `bin/`. Keep `lab.env` (or `community.env`) and `agent.env` under `config/`. Use absolute paths in the environment files. Create `config/stack.env` and `config/nodes.tsv`, with SSH aliases and one remote node per row. The local Agent uses `agent.env`.

```dotenv
CONSTELLA_EDITION=lab
CONSTELLA_HEALTH_URL=http://127.0.0.1:8875/api/health
CONSTELLA_CLUSTER_URL=ws://manager.internal:8875/api/agents/ws
CONSTELLA_WATCHDOG_INTERVAL_SECONDS=300
CONSTELLA_REMOTE_INSTANCE=rust-manager
```

```text
# node-id     ssh-host     backend
compute-02   compute-02   nvidia
```

```bash
bin/stack.sh check
bin/stack.sh start
bin/stack.sh status
bin/stack.sh stop
bin/stack.sh backup /path/to/new-telemetry-backup.sqlite3
bin/stack.sh backup-lab /path/to/new-identity-backup.sqlite3
```

`check` verifies local files and SSH access. `start` waits for Manager health before starting the local Agent, distributing the Agent to same-architecture remote nodes, and running the watchdog. Supervisors restart exited processes. The watchdog restarts missing remote Agent supervisors after hosts reconnect, while the Manager is healthy. Use `logs/` and `state/` for diagnosis. To also start a Tunnel during cutover, put `cloudflared` in `bin/`, set `TUNNEL_TOKEN` in `config/cloudflared.env`, and use `start --with-tunnel`. The Manager performs history maintenance in-process.

For a full recovery of a deployment with Tunnel configured, run `bin/restart-production.sh`. It checks the configuration, stops the stack, restarts it, and verifies live nodes, continuing database writes, and Tunnel connections. Use `--status` for the same verification without restarting services.

### Agent environment

```dotenv
CONSTELLA_NODE_ID=compute-01
CONSTELLA_MANAGER_URL=https://manager.example.com
CONSTELLA_AGENT_TOKEN_FILE=/home/operator/.config/constella/agent-token
CONSTELLA_DEVICE_TYPE=nvidia
CONSTELLA_REFRESH_SECONDS=1
CONSTELLA_PROCESS_SECONDS=5
```

Set `CONSTELLA_DEVICE_TYPE=ascend` for Ascend nodes. Set `CONSTELLA_DCMI_LIBRARY` to an absolute path if the CANN driver library is installed outside the usual locations. Preserve each existing node ID so historical telemetry and Lab bindings continue to match. Agents can use `--sample-once` to check the installed driver and permissions independently.

### Manager environment

```dotenv
CONSTELLA_BIND=127.0.0.1:8875
CONSTELLA_DB_PATH=/home/operator/constella-preview/telemetry.sqlite3
CONSTELLA_FRONTEND_DIST=/home/operator/.local/lib/constella/web
CONSTELLA_AGENT_TOKEN_FILE=/home/operator/.config/constella/agent-token
```

Use a dedicated database path, bind address and token for previews. For deployment, point the existing reverse proxy or tunnel at the selected Manager and forward WebSocket upgrades. The Manager includes HighRes in the same process. If a machine consumes `/api/highres/stream`, set `CONSTELLA_HIGHRES_TOKEN_FILE` and use the corresponding Bearer token. Lab requires a token for this machine stream.

Set `CONSTELLA_RAW_SNAPSHOT_SECONDS` to a positive interval in seconds to retain debug snapshots for up to 12 hours. The default is `0` (disabled). When enabled, Manager samples each node independently.

For Lab, use the Lab archive and its systemd unit. Add these settings to the Manager environment:

```dotenv
CONSTELLA_AUTH_MODE=cloudflare-access
CONSTELLA_LAB_DB_PATH=/home/operator/constella-preview/identity.sqlite3
CONSTELLA_ACCESS_TEAM_DOMAIN=https://team.cloudflareaccess.com
CONSTELLA_ACCESS_AUD=your-application-audience
CONSTELLA_BOOTSTRAP_ADMIN_EMAIL=admin@example.com
CONSTELLA_PUBLIC_ORIGIN=https://manager.example.com
```

Cloudflare Access must forward the application assertion to both the HTTP and browser WebSocket routes. Preserve the existing Access app, public origin, and private machine routing where available. Agent sends to `/api/agents/ws` with the Constella Agent Bearer token.

For a machine route published through Cloudflare, configure a separate hostname and a Service Auth policy restricted to the intended service token. Route that hostname to the Agent WebSocket path. Set these values on the Agent in addition to its Bearer token:

```dotenv
CONSTELLA_MANAGER_URL=https://agents.example.com
CONSTELLA_ACCESS_CLIENT_ID_FILE=/home/operator/.config/constella/access-client-id
CONSTELLA_ACCESS_CLIENT_SECRET_FILE=/home/operator/.config/constella/access-client-secret
```

Agent sends both credentials on each TLS WebSocket handshake. See [Cloudflare Access service tokens](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/).

### SQLite backups

With the source paths set in the environment, create consistent backups through the same bundled Manager binary:

```bash
constella-manager backup /path/to/new-telemetry-backup.sqlite3
constella-manager backup-lab /path/to/new-identity-backup.sqlite3
```

Each command opens the source read-only and requires a new destination. The identity backup is only needed for Lab. Use the backup copies with the migration commands, and retain a separate pre-cutover copy for rollback.

### Coordinated cutover

1. Test the same archive set on isolated ports. Use SQLite online backup copies of telemetry and Lab identities, matching node IDs, dedicated tokens, and a separate Access test route. Run `constella-manager migrate` against the telemetry copy and `constella-manager migrate-lab` against the identity copy with the environment paths above. Check health, live sampling, history, Jobs, HighRes, and Lab login, ownership, and administration. Verify hardware sampling on representatives of every deployed driver and host architecture.
2. Arrange a maintenance window. Stop the old Agents and all services that write to the original databases. Make a consistent final backup and preserve it, along with the old binaries, configuration, and frontend. If copying SQLite files directly while services are stopped, include any corresponding WAL files. Prepare separate working copies for the rollout.
3. Run both migration commands on the working copies where applicable. Start the selected Rust Manager with those paths and the correct frontend. Verify database health and existing history before starting the new Agents as a group. Confirm node count, live graphs, and Lab access after routing switches. Use one Manager writer per telemetry database.
4. For rollback, stop the new Agents and Manager, restore the prior route and service configuration, and restart the previous set against the preserved databases. Keep the Rust working copies for inspection and reconciliation of telemetry collected during the rollout. The TUI distribution remains separate.

## 构建与安装

在与部署机架构一致的 Linux 环境中构建。构建环境的 glibc 版本应不高于最旧的部署机。构建机需要 Rust、C 编译器、Node.js 和 npm。运行二进制程序需要系统 C 运行库。Agent 按设备类型加载系统中的 NVIDIA NVML 或昇腾 DCMI 驱动库。驱动库采样失败时使用已安装的厂商命令行工具，两分钟后重新尝试驱动库。Manager 使用内置 SQLite 并直接提供打包的 Web 前端。

从仓库根目录运行 `scripts/package/build.sh`。默认在 `target/constella-release` 生成 Agent、社区版 Manager、Lab 版 Manager 和 SHA-256 清单。第一个参数可以指定输出目录。解包前使用 `sha256sum -c SHA256SUMS-<version>-linux-<arch>` 校验对应平台的产物。

在 Linux x86_64 上交叉构建 ARM64 包时，先安装 Rust 的 `aarch64-unknown-linux-gnu` 目标、ARM64 GNU 交叉编译器和相应的 sysroot，再运行上方的交叉构建命令。

附带的 systemd 用户服务示例从 `~/.local/lib/constella/` 读取二进制和 Manager 的 `web/`，从 `~/.config/constella/` 读取环境文件，服务定义放在 `~/.config/systemd/user/`。在环境文件中填写绝对路径，配置目录和令牌文件权限分别设为 `0700` 和 `0600`。使用具备相应设备和进程读取权限的服务账号。需要开机自动启动用户服务时，按现场配置启用该账号的 lingering。

### 一键运维

把所选 Manager 和 Agent 二进制，以及 Manager 发布包里的 `ops/stack.sh` 和 `ops/process.sh` 放到运行目录的 `bin/`。在 `config/` 配置 `lab.env` 或 `community.env`、本机 `agent.env` 以及上方示例的 `stack.env` 和 `nodes.tsv`。节点表只列远端节点，每行填写节点 ID、SSH 别名及采样后端。

`bin/stack.sh check` 只检查本机配置和远端 SSH。确认切换时执行 `bin/stack.sh start`，依次启动并检查 Manager、启动本机 Agent、将 Agent 分发给同架构的远端节点并启动看门狗。守护进程会重启退出的程序。Manager 健康时，看门狗会在远端恢复连接后补启缺失的 Agent 守护进程。用 `status` 查看状态、`stop` 停止这套服务，SQLite 在线备份用 `backup` 或 `backup-lab`。日志和 PID 分别存放在 `logs/` 和 `state/`。需要同时启动 Tunnel 时，把二进制和令牌分别放在 `bin/cloudflared` 和 `config/cloudflared.env`，再用 `start --with-tunnel`。Manager 进程内维护历史库。

配置好 Tunnel 后，可用 `bin/restart-production.sh` 一次完成检查、停机、重启，并核验节点在线、数据库持续入库和 Tunnel 连接。加 `--status` 只核验状态。

使用上方环境模板。保留原节点 ID，避免历史记录和 Lab 绑定失去关联。昇腾节点设置 `CONSTELLA_DEVICE_TYPE=ascend`，驱动库不在常规目录时用 `CONSTELLA_DCMI_LIBRARY` 指向绝对路径。Agent 的 `--sample-once` 用于独立检查驱动和权限。

预览使用独立数据库、端口和令牌。部署时由现有反向代理或隧道转发 HTTP 与 WebSocket。Manager 进程内提供 HighRes。机器客户端使用 `CONSTELLA_HIGHRES_TOKEN_FILE` 对应的令牌访问流接口，Lab 的机器流必须配置令牌。

为 `CONSTELLA_RAW_SNAPSHOT_SECONDS` 设置正数秒间隔可以启用调试快照，保留 12 小时。默认值为 `0`，表示关闭。启用后 Manager 对各节点分别计时。

Lab 使用 Lab 前端包及对应服务定义。保持 Access 应用、公开源和已有的私网机器路由，确保浏览器 HTTP 和 WebSocket 都转发应用断言。Agent 使用 Bearer 令牌连接 `/api/agents/ws`。经 Cloudflare 发布机器入口时，使用单独的域名和限定服务令牌的 Service Auth 策略，并将该域名路由到 Agent WebSocket 路径。Agent 在 Bearer 令牌之外使用上方配置的 Client ID 和 Client Secret，并在每次 TLS WebSocket 握手时发送两项凭据。[Cloudflare Access 服务令牌](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)

### 一致备份与整体切换

使用上方 `backup` 和 `backup-lab` 命令生成一致备份。命令以只读方式连接源库，目标必须是新文件。仅 Lab 需要身份库备份。迁移使用工作副本，另外保留一份切换前备份供回滚。

1. 在隔离端口验证整套同批产物。使用遥测库和 Lab 身份库的 SQLite 在线备份、相同节点 ID、独立令牌和单独测试 Access 路由。用上方环境变量指向副本，分别运行 `constella-manager migrate` 和 `constella-manager migrate-lab`。检查健康、实时采样、历史、Jobs、HighRes、Lab 登录、历史归属和管理功能。在部署范围内各类架构和驱动的代表节点上验证实机采样。
2. 安排维护窗口，停止旧 Agent 和所有写入原库的服务。生成并保留最终一致备份，同时保留旧二进制、配置和前端。直接复制停机后的 SQLite 文件时，把对应的 WAL 文件一起保留。为切换准备单独工作副本。
3. 对工作副本执行迁移，使用正确的前端和数据库路径启动选定版本的 Rust Manager。确认健康和历史数据后，成组启动 Rust Agent。完成路由切换后检查节点数量、实时曲线和 Lab 访问。每个遥测库使用一个 Manager 写入进程。
4. 回滚时停止新版本 Agent 和 Manager，恢复原路由和服务配置，使用保留的数据库启动旧版组件。保留工作副本供检查和补录切换期间的遥测。TUI 继续独立分发。
