# Constella Lab deployment

Constella Lab is the optional user-system edition. It keeps the public monitoring
core lightweight while adding Cloudflare Access identity, local roles, audit events,
and self-service Linux account binding across one or more nodes.

The full design and threat model are in
[user-system-design-zh.md](user-system-design-zh.md).

## Build and install

From a source checkout:

```bash
uv sync --all-packages
cd frontend
npm ci
npm run build:lab
cd ..
```

For a release artifact, run `./scripts/package/build.sh` and install the resulting
`constella-gpu-lab` wheel. Generated frontend assets are release artifacts and are
not committed.

## Required environment

Keep the environment file outside Git and restrict it to the service user:

```text
CONSTELLA_AUTH_MODE=cloudflare-access
CONSTELLA_ACCESS_TEAM_DOMAIN=https://team-name.cloudflareaccess.com
CONSTELLA_ACCESS_AUD=access-application-audience-tag
CONSTELLA_LAB_DB_PATH=/absolute/private/path/identity.sqlite3
CONSTELLA_BOOTSTRAP_ADMIN_EMAIL=admin@example.com
CONSTELLA_PUBLIC_ORIGIN=https://gpu.example.com
CONSTELLA_ACCOUNT_UID_MIN=1000
CONSTELLA_ACCOUNT_UID_MAX=2147483647
CONSTELLA_ACCOUNT_DENY_USERS=root,constella,nobody
CONSTELLA_AGENT_TOKEN_FILE=/absolute/private/path/agent-token
```

Startup fails if authentication, audience, origin, bootstrap identity, or Lab
database settings are missing. There is no trusted-email development header.

## Start the manager

Keep the default loopback binding when every agent can reach the manager through
a separate private transport:

```bash
constella-lab serve --host 127.0.0.1 --port 8765
```

The Cloudflare Tunnel public hostname should target
`http://127.0.0.1:8765`. Do not expose another public inbound port.

If remote agents connect directly over a trusted private LAN, explicitly listen on
the manager's private address (or `0.0.0.0`) and restrict that port to the agent
subnet with the host or network firewall. Direct browser requests still fail closed
without a valid signed Access JWT, and `/api/agents/ws` still requires the separate
Constella agent token. The Tunnel can continue to target `127.0.0.1` on the same
port.

```bash
constella-lab serve --host 0.0.0.0 --port 8765
```

Source deployments that also use Constella's local Agent and Highres Sidecar can
use the shared service scripts by setting `EDITION=lab`. Load the required Lab
environment first; keep that environment file outside Git and mode `0600`.

```bash
set -a
. /absolute/private/path/constella-lab.env
set +a

EDITION=lab HOST=0.0.0.0 PORT=8765 \
AGENT_TOKEN_FILE=/absolute/private/path/agent-token \
DB_PATH=/absolute/private/path/constella.db \
HIGHRES_SIDECAR=1 \
HIGHRES_TOKEN_FILE=/absolute/private/path/highres-token \
./scripts/service/start.sh

EDITION=lab NODES=/absolute/private/path/nodes.yaml \
./scripts/cluster/start.sh
```

A future Internet-facing agent path must use a separate hostname with Cloudflare
Service Auth in addition to the Constella agent token. Never put machine agents
through the email OTP flow and never create an unconditional public Access bypass
for the agent endpoint.

## Agent rollout

Every bindable node, including the manager's local GPU node, runs the normal
Constella agent. The current agent declares `account_lookup_v1` and answers manager
requests through its existing authenticated WebSocket. It performs `pwd.getpwnam`
in a worker thread, runs no shell command, and needs no root permission.

Upgrade agents before exposing a node for account binding. Older agents continue
to report monitoring samples but appear as `agent upgrade required` in the binding
page.

## Agent controls and account connections

Lab administrators can open **Lab management → Node agents** to restart an online
agent, including the agent on the manager host. Upgrade the agent first so it
advertises `agent_restart_v1`; older agents remain compatible and display an
upgrade notice. Each restart requires confirmation, has a 60-second per-node
cooldown, and records the request and acknowledgement in the audit log. The UI
confirms recovery only after a new agent instance reconnects. GPU workloads and
the manager continue running; this control does not reboot the Linux host.

Each node also shows its sampling source and latest sample time from the existing
cluster snapshot, without additional requests or telemetry fields. Offline or stale
nodes label the retained source as the last source; nodes without samples show
`Not sampled`. Sample times use Asia/Shanghai (UTC+8).

Existing members can open **Profile → Connect node accounts**, or the same link in
the user menu, to add Linux users on newly connected nodes. **Refresh nodes** checks
the latest capabilities and invalidates a previous lookup. Saving rechecks all
selected users and preserves existing account connections and historical records.

The Lab manager defaults to `--graceful-timeout 10`, matching the core manager.
Idle high-resolution streams now observe disconnects so SIGTERM can reach the
database drain and close lifecycle. See the [design and investigation](design/lab-node-control.md)
for the cause, implementation, validation, and rollout boundary.

### 节点控制与账号绑定

Lab 管理员可在 **Lab management → Node agents** 重启在线 agent，包括 manager
本机的 agent。需要先升级 agent，使其声明 `agent_restart_v1`；旧版保持采集兼容，
界面提示升级。重启需确认，每节点冷却 60 秒，请求和应答记录在审计日志中。
只有新实例重连后界面才确认恢复；GPU 任务和 manager 保持运行，不会重启 Linux 主机。

节点同时展示已有集群快照中的采样来源和最近采样时间，不增加请求或遥测字段。
离线或陈旧节点标注上次采样来源，无采样时显示 `Not sampled`；时间使用 Asia/Shanghai（UTC+8）。

已有成员可通过 **Profile → Connect node accounts** 或用户菜单中的同名入口，绑定
新节点上的 Linux 用户。**Refresh nodes** 获取最新能力并作废旧预检；提交时重新查询
所有选中用户，保留既有连接及历史记录。

Lab manager 默认使用 `--graceful-timeout 10`，与核心版一致。空闲高频流现在会检测断连，
SIGTERM 可以进入数据库排空和关闭流程。根因、实施、验收及上线边界见
[设计与调查记录](design/lab-node-control.md)。

## Cloudflare Access checklist

1. Create a self-hosted application for the exact public hostname.
2. Select One-time PIN as the only identity provider if email OTP is desired.
3. Set global, application, and policy duration to `720h` for a one-month session.
4. Use exact email entries or a reviewed Access group in the Allow policy.
5. Never use `Login Methods: One-time PIN` as the only Include rule. That allows
   every valid email user.
6. Keep the Tunnel target on loopback and confirm unauthenticated public requests
   redirect to Access. A private Agent listener must be firewall-restricted.
7. Copy the application's AUD tag into `CONSTELLA_ACCESS_AUD`.

Adding a member means adding their exact email to the Access policy. The member is
created locally on first successful login and can bind multiple selected nodes in
one atomic batch. Administrators only resolve conflicts, role changes, and offboarding.

## Backup and restore

The Lab identity database is independent from telemetry history. Use the SQLite
backup API through the CLI instead of copying a live WAL database:

```bash
constella-lab backup /absolute/private/backups/identity-2026-09-06.sqlite3
```

The command creates parent and backup permissions for the service account. Test a
restore periodically by opening the backup in an isolated preview process and
confirming that users, current bindings, and audit events are present.
