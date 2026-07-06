# High-Resolution Job GPU Curves Plan

## Goal

Add a high-resolution GPU curve view for recent short jobs without changing the long-term
SQLite history design.

The unified job curve feature should serve jobs whose end time is within the last 7 days.
Within that scope, the high-resolution memory path should serve only:

- jobs whose duration is less than 1 hour
- GPU curve queries that need finer detail than the existing 20s rollup

Long jobs inside the 7-day window should fall back to existing rollup history. Jobs older
than 7 days are outside the job curve search/detail scope.

The user-facing feature should not be limited to high-resolution jobs. It should be a
unified job GPU curve view:

- recent short jobs use the sidecar's high-resolution memory cache when available
- long jobs use existing rollup data
- jobs older than 7 days are outside the job curve search/detail scope
- partial high-resolution coverage can fall back to rollup or return a mixed response with
  explicit coverage metadata

## Current State

Constella already has the metadata needed to find the GPUs used by a job:

- `process_sessions` stores process/session identity, user, command-derived task name,
  process name, command hash/text, first seen time, last seen time, duration, and status.
- `process_gpu_usages` links a session to one or more `gpu_uuid` values with first/last
  usage timestamps.
- `gpus` maps `node_id + gpu_uuid` to GPU index and model.
- `gpu_metric_rollups` stores 20s, 2m, and 1h GPU metric rollups.

The missing piece is high-resolution GPU metrics. The normal database path intentionally
does not write raw 1s GPU samples. It aggregates accepted manager samples into 20s buckets
in memory and writes only rollups.

That design should remain unchanged.

## Proposed Architecture

Use a dedicated high-resolution cache sidecar on the manager node.

```text
agent samples
  -> manager ingest
      -> realtime cluster state
      -> SQLite rollup writer
      -> lightweight local GPU sample stream
            -> highres sidecar
                  -> in-memory per-GPU ring buffers
                  -> job search and curve API
```

The manager should not own the high-resolution cache. It only exposes a thin local stream
of accepted GPU metric points. The sidecar owns buffering, querying, retention, and API
behavior.

The agent should not change. It remains a lightweight sampler that reports the current
sample to the manager.

## Manager Stream

Add a local-only stream endpoint or Unix socket for high-resolution consumers. The payload
should be much smaller than `/ws/cluster`.

Example message:

```json
{
  "type": "gpu_sample",
  "node_id": "node-a",
  "sampled_at": 1782800000.12,
  "refresh_interval": 1.0,
  "gpus": [
    {
      "uuid": "GPU-0",
      "gpu_index": 0,
      "utilization_gpu": 73,
      "utilization_mem": 28,
      "memory_used_mb": 20480,
      "memory_total_mb": 81920,
      "power_watts": 310.5,
      "temperature_c": 66
    }
  ]
}
```

Stream rules:

- Emit only after the manager accepts an agent sample.
- Do not include process lists, command lines, node history arrays, or frontend-only data.
- Bind to loopback or a Unix socket by default.
- Require an internal token if exposed over TCP.
- Use bounded per-consumer queues. If the sidecar is slow, drop stream messages rather
  than slowing agent ingest or realtime UI.

This keeps the manager's role limited to fan-out of already accepted samples.

## Sampling Frequency

High-resolution data must follow the user's actual sampling settings. It should not assume
1s samples.

The sidecar stores the original `sampled_at` timestamp for every point and uses the
observed sample interval in API metadata.

Expected cases:

- `refresh_interval = 0.5s`: up to 14,400 samples per GPU for a 2h cache.
- `refresh_interval = 1s`: up to 7,200 samples per GPU for a 2h cache.
- `refresh_interval = 2s`: up to 3,600 samples per GPU for a 2h cache.
- `refresh_interval = 5s`: up to 1,440 samples per GPU for a 2h cache.

The sidecar should not resample on ingest. It should preserve actual timestamps and let
the frontend plot irregular timestamps if samples are delayed or missed.

## Ring Buffer Design

Maintain one ring buffer per logical GPU key:

```text
(node_id, gpu_uuid) -> GpuMetricRing
```

Each ring stores only the fields needed for charts:

- `sampled_at`
- `utilization_gpu`
- `utilization_mem`
- `memory_used_mb`
- `memory_total_mb`
- `power_watts`
- `temperature_c`

Use compact arrays instead of a `deque[dict]` or per-sample Python objects.

Suggested implementation:

- `array("d")` for timestamps and floating-point metrics
- `array("f")` for metrics where float precision is enough
- `array("I")` or `array("H")` for integer metrics if useful
- fixed capacity per GPU, derived from retention and the minimum supported interval

For simplicity and predictable memory, capacity can be based on the worst supported
manager interval:

```text
capacity = ceil(2h / 0.5s) = 14,400 points per GPU
```

This wastes some slots when users sample every 1s/2s/5s, but avoids resizing complexity.

## Memory Estimate

With compact arrays, each point is roughly 24-40 bytes per GPU, depending on exact types
and array overhead. Use 40 bytes per point for planning.

Worst case at 0.5s sampling:

```text
14,400 points/GPU * 40 bytes ~= 576 KB/GPU
```

Estimated cache size:

| GPUs | 2h at 0.5s | 2h at 1s |
|---:|---:|---:|
| 8 | ~4.6 MB | ~2.3 MB |
| 80 | ~46 MB | ~23 MB |
| 400 | ~230 MB | ~115 MB |
| 800 | ~461 MB | ~230 MB |

This is acceptable for the intended small to medium cluster profile as long as the
implementation avoids dictionaries or dataclasses per point. With object-per-sample
storage, memory can be an order of magnitude higher and should be avoided.

## Python Memory Management

The sidecar must be designed as a fixed-memory service. Do not rely on Python garbage
collection to clean up unbounded per-sample objects.

Rules:

- Preallocate or fixed-size allocate ring arrays per GPU.
- Overwrite old samples in place when the ring wraps.
- Do not store per-sample dicts, dataclasses, closures, or nested objects.
- Do not retain full manager messages after extracting the metric fields.
- Keep stream consumer queues bounded and drop old/new messages according to an explicit
  policy when backpressure appears.
- Remove rings for GPUs or nodes that have not appeared for longer than the retention
  window plus a grace period.
- Avoid building large temporary lists on every ingest. Temporary chronological views are
  acceptable on query because queries are user-driven and bounded.
- Be careful with long-lived exception tracebacks, task references, and reconnect loops;
  clear failed task references after handling them.

Operational safeguards:

- Expose current ring count, point capacity, valid point count, approximate bytes, dropped
  stream messages, reconnect count, and oldest/newest sample timestamps in
  `/api/highres/status`.
- Add a soft memory limit config. If estimated memory exceeds the limit, stop creating new
  rings or evict oldest inactive rings first.
- Run a periodic cleanup task for inactive rings and stale per-query caches.
- In tests, include wraparound and repeated reconnect/query loops to catch accidental
  growth.

Implementation note:

CPython may not immediately return freed memory to the operating system, especially after
large allocations. The goal is therefore to avoid repeated large allocation/free cycles and
keep memory usage stable after warm-up.

## Query Performance Without Database Indexes

The ring buffer does not need database-style indexes for curve lookup.

The job search step uses SQLite metadata. The ring buffer step already knows the exact
`node_id`, `gpu_uuid`, and time window from the selected job. It should not scan all GPUs.

Curve query flow:

```text
job_key
  -> SQLite lookup gives job sessions and process_gpu_usages
  -> get small set of (node_id, gpu_uuid, start, end) windows
  -> direct dict lookup for each GpuMetricRing
  -> binary search timestamps inside that ring
  -> return points in [start - padding, end + padding]
```

Complexity:

```text
O(number_of_job_gpus * (log ring_size + returned_points))
```

For a 2h cache:

- 1s sampling: ring size is about 7,200 points.
- 0.5s sampling: ring size is about 14,400 points.
- A 10 minute job at 1s returns about 600 points per GPU.
- An 8 GPU job at 1s returns about 4,800 points, which is small.

This is fast because the lookup is targeted. The expensive operation would be scanning
all rings to discover jobs, but that is not part of the design. Job discovery should stay
with SQLite.

Implementation detail: ring buffers wrap around, so expose a method that returns a
chronologically ordered view of valid samples. For 14,400 points, copying or stitching one
GPU's timestamp array during query is still cheap. If needed later, maintain a monotonic
logical sequence and implement binary search over the two physical ring segments.

## Job Unit

The user-facing unit should be a job, not an individual process.

Use the existing analytics job identity as the starting point:

```text
node_id + user + parent_start_time/process_start_time/first_seen_at + parent_pid/pid
```

The API should return one job containing:

- all sessions that belong to the job
- all PIDs
- all GPUs touched by those sessions
- a unified `started_at` and `last_seen_at`
- status derived from member sessions

For multi-process or multi-GPU jobs, the curve response should include multiple series in
one chart:

```json
{
  "source": "high_res_memory",
  "job_key": "node-a:alice:...",
  "range_start": 1782800000.0,
  "range_end": 1782800600.0,
  "resolution_seconds": 1.0,
  "series": [
    {
      "node_id": "node-a",
      "gpu_uuid": "GPU-0",
      "gpu_index": 0,
      "label": "node-a GPU0",
      "points": [
        {"sampled_at": 1782800000.1, "utilization_gpu": 42, "memory_used_mb": 12000}
      ]
    }
  ]
}
```

If several processes in the same job use the same GPU, do not duplicate the GPU line.
Show one line per `(node_id, gpu_uuid)`.

## API Shape

Sidecar APIs:

```text
GET /api/highres/jobs
GET /api/highres/jobs/{job_key}
GET /api/highres/jobs/{job_key}/gpu
GET /api/highres/status
```

`GET /api/highres/jobs` filters:

- `q`: command/task/process text search
- `user`: username
- `pid`: process id
- `node_id`
- `status`
- `since`
- `until`
- `max_duration_seconds`, default 3600
- `recent_seconds`, default 7200
- `limit`

`GET /api/highres/jobs/{job_key}/gpu` behavior:

- Reject or roll up jobs with duration >= 1h.
- Return high-resolution memory data only when the job window is covered by the 2h cache.
- Include `padding_seconds`, default 10-30s.
- If high-resolution coverage is missing, return a structured fallback response pointing
  the frontend to the existing 20s rollup endpoint or proxy the rollup data directly.

Response metadata should include:

- `source`: `high_res_memory`, `rollup`, or `mixed`
- `coverage_start`
- `coverage_end`
- `cache_retention_seconds`
- `resolution_seconds`
- `expired`
- `warnings`

## Search Design

Search should be lightweight and high-performance.

Preferred first version: use SQLite plus FTS5.

Reasons:

- Job metadata already lives in SQLite.
- FTS5 is built into the local Python SQLite in this environment.
- It avoids adding a search service or a heavy dependency.
- It is enough for command name, task name, executable path, process name, and command text.

Suggested FTS table:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS process_sessions_fts USING fts5(
  session_id UNINDEXED,
  pid UNINDEXED,
  user,
  task_name,
  process_name,
  exe,
  cmdline_text,
  tokenize='unicode61'
);
```

Because current writes happen in `SQLiteStore`, there are two possible integration paths:

1. Add FTS maintenance to the main DB writer when process sessions are inserted or updated.
2. Let the sidecar build and refresh an in-memory search index from SQLite every few
   seconds for only the recent 2h window.

For minimal impact on the existing DB writer, prefer option 2 initially. It can query recent
sessions and keep an in-memory list keyed by `job_key`. For substring-style command search
over only the recent 2h of jobs, simple normalized string matching may already be enough.
If fuzzy matching is needed later, consider a small optional dependency such as RapidFuzz.

PID and username filters:

The current schema already persists both `pid` and `user`, so exact filters for process id
and username can be supported without schema changes. PID filtering should search member
sessions before job grouping, then return the containing job so multi-process jobs still
open as one unit.

## Frontend

Use uPlot and reuse the existing node history chart implementation where possible.

Existing reusable pieces:

- chart wrapper and legend structure
- `uplot-theme` CSS
- axis formatting helpers
- metric tabs
- aligned series conversion pattern
- resize observer behavior

Needed additions:

- job search panel
- job result list grouped by job
- job detail curve panel
- multi-line chart with one line per GPU
- source/coverage indicator for high-res vs rollup

The chart should support at least:

- GPU utilization
- memory used
- power
- temperature

For multi-GPU jobs, draw all GPU lines in one uPlot chart. The legend should identify
`node_id`, `GPU index`, and optionally GPU model.

## Fallback Rules

Use high-resolution data only when all of these are true:

- job duration is less than 1 hour
- job intersects the sidecar's 2h cache window
- the selected GPU ring buffers have enough coverage for the job window

Otherwise use existing rollup history.

Rollup-backed job curves are still part of this feature. For jobs that are too long for
high-resolution display, or whose high-resolution cache has expired, the same job detail
view should render curves from `gpu_metric_rollups` using the job's `node_id`, `gpu_uuid`
set, and time window.

Rollup source selection:

- duration less than 1 hour and covered by sidecar cache: high-resolution memory data
- duration less than 1 hour but not covered: 20s rollup when within 20s retention
- duration 1 hour or longer: 20s/2m/1h rollup selected by the existing history bucket rules
- very old jobs: coarser rollup according to available retention

The frontend should use the same uPlot component for both high-resolution and rollup
curves. Only the series point shape and metadata differ.

Suggested behavior:

| Case | Result |
|---|---|
| 5 minute job ended 10 minutes ago | high-res memory curve |
| 50 minute job ended 30 minutes ago | high-res memory curve if covered |
| 90 minute job ended 10 minutes ago | 20s rollup |
| 5 minute job ended 6 hours ago | 20s rollup with high-res expired marker |
| 6 hour job ended 30 minutes ago | rollup curve, not high-res |
| 3 day old job | rollup curve at the available retained bucket |
| sidecar restarted after job ended | 20s rollup or partial high-res with warning |

## Reliability Boundaries

This feature intentionally does not guarantee permanent high-resolution history.

Known boundaries:

- Sidecar restart loses high-resolution data.
- Manager restart interrupts the stream and may leave a coverage gap.
- Agent disconnects or delayed samples create gaps.
- Very short jobs can still be missed if process sampling never records them.
- Current process sampling frequency can be lower than GPU sampling frequency.

The API should make coverage explicit so the frontend does not imply precision that does
not exist.

## Implementation Phases

### Phase 1: Sidecar Skeleton and Stream

- Add a lightweight manager stream for accepted GPU sample points.
- Add sidecar process entry point, config, and reconnect loop.
- Add compact per-GPU ring buffers with 2h retention.
- Add `/api/highres/status`.

### Phase 2: Job Lookup and Search

- Implement recent job query from SQLite using `process_sessions`, `process_gpu_usages`,
  and `gpus`.
- Group sessions into job records using the existing analytics job key logic.
- Support filters by command/task text, username, node, status, time, and duration.
- Support exact `pid` filtering and return the containing job.

### Phase 3: Curve API

- Implement high-resolution curve extraction by job key.
- Return one series per `(node_id, gpu_uuid)`.
- Add coverage metadata and fallback markers.
- Proxy or document fallback to the existing rollup endpoint for expired/long jobs.

### Phase 4: Frontend

- Add job search UI.
- Add job result list grouped by job.
- Add uPlot job curve chart by reusing node analytics chart helpers.
- Support metric switching and multi-GPU legends.
- Display source and coverage state.

### Phase 5: Hardening

- Add bounded stream queue metrics.
- Add sidecar memory usage/status reporting.
- Add tests for ring buffer wraparound, binary-search window extraction, job grouping,
  and fallback behavior.
- Add tests or soak checks for stable memory across ring wraparound, stream reconnects,
  and repeated curve queries.
- Add operational docs for enabling the sidecar.

## Open Decisions

- Whether the manager stream should be WebSocket over loopback or a Unix domain socket.
- Whether fallback rollup data should be fetched by the frontend from the manager or
  proxied by the sidecar.
- Whether the sidecar should be started by the existing service script or a separate script.

## Implementation Status

Implemented in the first public-ready slice:

- The manager exposes a lightweight `/api/highres/stream` WebSocket that publishes only
  accepted GPU sample points for local high-resolution consumers.
- `constella highres-sidecar` runs a separate FastAPI sidecar process, subscribes to the
  manager stream, maintains its own fixed-capacity per-GPU memory cache, and exposes
  `/api/highres/*` APIs.
- The existing manager `/api/highres/*` endpoints remain available for same-process
  operation, but the sidecar path is implemented and can be launched independently.
- `GET /api/highres/status` reports ring count, point capacity, valid point count,
  approximate bytes, retention, dropped samples, and sample time bounds.
- `GET /api/highres/jobs` searches recent SQLite job metadata, groups sessions by the
  existing analytics job key, and supports text, user, PID, node, status, time, duration,
  and limit filters.
- `GET /api/highres/jobs/{job_key}` returns the grouped job detail.
- `GET /api/highres/jobs/{job_key}/gpu` returns one curve series per `(node_id, gpu_uuid)`;
  it uses high-resolution memory data only when the full padded job window is covered and
  otherwise falls back to existing rollup history.
- The frontend adds a `/jobs` route with job search, result selection, metric switching,
  multi-GPU uPlot curves, and source or warning metadata.
- Tests cover ring wraparound, 7-day job lookup, manager stream publication, sidecar API
  behavior, job grouping, and high-resolution curve API behavior.

Current boundaries:

- Job search and job detail are limited to the most recent 7 days.
- High-resolution memory data is retained for 2 hours.
- Long jobs and cache misses use rollup fallback.
