# SQLite History

SQLite history is optional. Constella keeps the realtime dashboard in manager memory, so the service works without a database. Enable this only when you need persisted GPU metric samples, rollups, process sessions, and process-GPU usage.

## Enable

Start the manager with `DB_PATH`:

```bash
DB_PATH=run/constella.db RAW_SNAPSHOT_SECONDS=30 ./scripts/service/start.sh
```

`RAW_SNAPSHOT_SECONDS` controls the low-frequency raw snapshot write interval. Raw snapshot retention is controlled by `RAW_RETENTION_SECONDS` during maintenance.

## Maintenance

Run the bundled maintenance script:

```bash
./scripts/maintenance/db.sh
```

Or run individual commands:

```bash
uv run constella db rollup --path run/constella.db --bucket-seconds 10
uv run constella db prune-raw --path run/constella.db
uv run constella db close-sessions --path run/constella.db
```

The maintenance script also accepts retention settings:

```bash
DB_PATH=run/constella.db \
ROLLUP_BUCKET_SECONDS=10 \
RAW_RETENTION_SECONDS=43200 \
SESSION_STALE_SECONDS=300 \
./scripts/maintenance/db.sh
```

## Runtime Behavior

Database writes use a bounded background queue. Slow or disabled SQLite storage does not block realtime WebSocket snapshots because the dashboard reads the manager's latest in-memory state.

When the database is not enabled, history APIs return an empty disabled response:

```json
{"enabled":false,"items":[]}
```

Relevant APIs:

- `GET /api/history/gpu`
- `GET /api/history/tasks`
- `GET /api/users`
