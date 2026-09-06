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
