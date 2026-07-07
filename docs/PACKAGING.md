# Packaging Constella for PyPI

This project supports two deployment modes:

- Source deployment from a checkout with `scripts/service/*`.
- Installed CLI deployment from a wheel or PyPI package with the `constella` command.

Do not upload artifacts as part of the local packaging flow. Build locally, install locally, test locally, then upload only from an explicit release process.

## Build

```bash
./scripts/package/build.sh
```

The script builds the frontend into `src/constella/frontend/dist`, then creates `dist/*.whl` and `dist/*.tar.gz`. The generated frontend directory and `dist/` are ignored by git, but included in local build artifacts.

## Installed CLI Usage

For a full command and parameter reference, see [PyPI CLI Usage](PYPI_CLI.md).

One-command service start:

```bash
constella service start
```

By default this starts the manager, creates `run/agent-token`, enables SQLite at `run/constella.db`, and starts a local GPU agent. Use explicit paths when running outside a project checkout:

```bash
constella service start \
  --host 0.0.0.0 \
  --port 8765 \
  --run-dir ~/.constella/run \
  --log-dir ~/.constella/logs
```

Stop or inspect that local service stack:

```bash
constella service status
constella service stop
```

Optional service flags:

```bash
constella service start --no-local-agent
constella service start --db-path /data/constella.db
constella service start --no-db
constella service start --highres-sidecar --highres-port 8766
constella service start --cluster-nodes nodes.yaml
constella service stop --cluster-nodes nodes.yaml --stop-cluster
```

The lower-level commands remain available for manual process managers, custom supervisors, and debugging:

```bash
constella serve --host 127.0.0.1 --port 8765 --db-path run/constella.db
constella agent --manager-url ws://127.0.0.1:8765/api/agents/ws --token-file run/agent-token
constella highres-sidecar \
  --host 127.0.0.1 \
  --port 8766 \
  --db-path run/constella.db \
  --manager-stream-url ws://127.0.0.1:8765/api/highres/stream
```

SQLite maintenance:

```bash
constella db maintain --path run/constella.db
```

Cluster agent control remains available through:

```bash
constella cluster start --nodes nodes.yaml
constella cluster status --nodes nodes.yaml
constella cluster stop --nodes nodes.yaml
```

When installed from a wheel, `cluster start` builds the remote agent runtime from the installed package and writes temporary build files under the user cache directory.

## Source Deployment

Source deployment remains unchanged:

```bash
./scripts/service/setup.sh
./scripts/service/start.sh
./scripts/service/status.sh
./scripts/service/stop.sh
```

The source scripts build and serve `frontend/dist`. Installed packages serve the packaged frontend assets. `CONSTELLA_FRONTEND_DIST` or `constella serve --frontend-dir` can override the frontend directory for tests or custom deployments.

## Safe Local Smoke Test

Never reuse a production port or database during packaging validation. If `8765` is already serving Constella, use a different port and a temporary database:

```bash
python3 -m venv /tmp/constella-wheel-test
/tmp/constella-wheel-test/bin/pip install dist/constella-*.whl
/tmp/constella-wheel-test/bin/constella service start \
  --host 127.0.0.1 \
  --port 18875 \
  --run-dir /tmp/constella-smoke/run \
  --log-dir /tmp/constella-smoke/logs
```
