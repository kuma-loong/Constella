# SQLite History

SQLite history is optional. Constella keeps the realtime dashboard in manager memory, so the service works without a database. Enable SQLite only when persisted GPU rollups, task history, and analytics dashboards are needed.

The database is a side path:

```text
agent sample -> manager memory state -> UI / WebSocket
             -> optional bounded DB queue -> rollups / task sessions
```

Raw 1s GPU metric samples are not written to SQLite in the normal path. The writer keeps only the open 20s rollup buckets in memory, then flushes closed buckets to `gpu_metric_rollups`.

## Enable

Start the manager with `DB_PATH`:

```bash
DB_PATH=run/constella.db RAW_SNAPSHOT_SECONDS=30 ./scripts/service/start.sh
```

`RAW_SNAPSHOT_SECONDS` controls optional low-frequency raw debug snapshots. It defaults to `0`, which disables raw snapshot writes. Raw snapshot retention is controlled by `RAW_RETENTION_SECONDS` during maintenance.

## Retention

- 20s rollups: 7 days
- 2m rollups: 60 days
- 1h rollups: 365 days
- process sessions and process-GPU usage: long-lived
- raw snapshots: optional, default maintenance retention is 12 hours

## Maintenance

Run the bundled maintenance script:

```bash
./scripts/maintenance/db.sh
```

Or run the same command directly:

```bash
uv run constella db maintain --path run/constella.db
```

The maintenance script accepts retention settings:

```bash
DB_PATH=run/constella.db \
RAW_RETENTION_SECONDS=43200 \
SESSION_STALE_SECONDS=300 \
./scripts/maintenance/db.sh
```

Individual commands are also available:

```bash
uv run constella db rollup --path run/constella.db --from-bucket-seconds 20 --to-bucket-seconds 120
uv run constella db rollup --path run/constella.db --from-bucket-seconds 120 --to-bucket-seconds 3600
uv run constella db prune-rollups --path run/constella.db
uv run constella db prune-raw --path run/constella.db
uv run constella db close-sessions --path run/constella.db
```

For an old database that already contains `gpu_metric_samples`, run a one-time migration before pruning or archiving the old raw rows:

```bash
uv run constella db migrate-samples --path run/constella.db --bucket-seconds 20
```

## Runtime Behavior

Database writes use a bounded background queue. Slow or disabled SQLite storage does not block realtime WebSocket snapshots because the dashboard reads the manager's latest in-memory state.

If the DB queue is full, the sink drops that DB write and increments its internal dropped sample counter. Agent ingest, `ClusterState`, `/api/cluster/snapshot`, and `/ws/cluster` continue normally.

When the database is not enabled, history APIs return an empty disabled response:

```json
{"enabled":false,"items":[]}
```

Relevant APIs:

- `GET /api/history/gpu`
- `GET /api/history/tasks`
- `GET /api/users`
- `GET /api/analytics/overview?range=7d`
- `GET /api/analytics/node/{node_id}?range=24h`
- `GET /api/highres/status`
- `GET /api/highres/jobs`
- `GET /api/highres/jobs/{job_key}`
- `GET /api/highres/jobs/{job_key}/gpu`

Analytics APIs read only from SQLite rollups and task session tables. They return `enabled:false` when SQLite is disabled and do not participate in the realtime WebSocket path.

Overview analytics includes weighted user GPU hours, job rankings, low-utilization reservation signals, and Beijing-time after-hours activity. User-facing `GPU hours` values are weighted by GPU model so mixed hardware is easier to compare.

Node analytics includes downsampled per-GPU time series and utilization heatmaps. The frontend supports local multi-select GPU highlighting for the trend chart without refetching or rebuilding the heatmap. Heatmap resolution is range-aware: `1h` uses 5 minute buckets, `24h` uses 1 hour buckets, `7d` uses 6 hour buckets, and `30d` uses 1 day buckets.

Supported ranges are `24h`, `7d`, and `30d` for Overview, and `1h`, `24h`, `7d`, and `30d` for Node history.

## Job GPU Curves

Constella exposes a unified job curve view at `/jobs` when SQLite history is enabled. Job discovery reads `process_sessions`, `process_gpu_usages`, and `gpus`; it groups sessions with the same analytics job identity and returns one user-facing job with its PIDs, sessions, and GPU set. Job search and job detail are limited to jobs seen within the last 7 days.

Recent short jobs can use an in-memory high-resolution GPU cache. The cache is a fixed-capacity per-GPU ring buffer populated only after an agent sample is accepted by the manager. It does not write raw 1s samples to SQLite. The default retention is 2 hours and the default capacity is sized for 0.5s samples.

The preferred deployment is a separate high-resolution sidecar process. The manager publishes a lightweight local WebSocket stream at `/api/highres/stream`; `constella highres-sidecar` subscribes to that stream, owns the high-resolution memory cache, and exposes `/api/highres/*` APIs. The manager also keeps same-process `/api/highres/*` endpoints for simple deployments and tests.

Curve source selection:

- jobs shorter than 1 hour use high-resolution memory data only when the cache fully covers the padded job window
- long jobs and cache misses fall back to existing rollup history
- jobs older than 7 days are not returned by the job curve APIs
- responses include `source`, coverage timestamps, resolution, expiration state, and warnings so the frontend does not imply precision that is not available
