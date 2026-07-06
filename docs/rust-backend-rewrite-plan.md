# Rust Backend Rewrite Plan

This branch replaces the Python backend with a Rust implementation while keeping the frontend API contract stable.

## Constraints

- Do not touch the running service on port `8765`.
- Do not reuse or mutate the existing production SQLite database during tests.
- Keep frontend source changes minimal and only adjust it when the API contract intentionally changes.
- Use TDD: migrate behavior with Rust tests derived from the existing Python test suite.
- Prefer simple, low-overhead Rust components over broad frameworks or hidden background work.

## Completed Baseline

- Created isolated worktree: `/home/guquansheng/project/Constella-rust-backend`.
- Added Rust crate and `constella serve` binary.
- Added compatible schema models for GPU, process, node, cluster, settings, and agent messages.
- Added cluster state behavior:
  - agent hello registration,
  - seq deduplication,
  - reconnect connection isolation,
  - stale/offline status,
  - in-memory per-GPU history,
  - static hardware preservation.
- Added Axum HTTP/WebSocket routes for:
  - `GET /api/health`,
  - `GET /api/cluster/snapshot`,
  - `GET /api/settings`,
  - `PATCH /api/settings`,
  - retired `GET /api/snapshot`,
  - retired `WS /ws/gpu`,
  - `WS /ws/cluster`,
  - `WS /api/agents/ws`,
  - disabled optional history/analytics/highres responses when DB/cache is absent.
- Added SQLite schema/store for:
  - nodes,
  - gpus,
  - process sessions,
  - process GPU usages,
  - raw snapshots,
  - GPU metric rollups.
- Added DB-backed history APIs:
  - `GET /api/history/gpu`,
  - `GET /api/history/tasks`,
  - `GET /api/users`.
- Added `CONSTELLA_DB_PATH` / `--db-path` runtime integration and sample persistence.
- Added high-resolution job support:
  - in-memory per-GPU sample rings,
  - cache status API,
  - job grouping and search,
  - job detail API,
  - high-res memory curve API,
  - adaptive rollup fallback for long jobs,
  - lightweight `gpu_sample` stream publishing.
- Added analytics APIs:
  - overview user/job GPU-hour aggregation,
  - GPU model weighting,
  - long low-utilization anomaly detection,
  - node rollup series,
  - node heatmap buckets.
- Added `nodes.yaml` loader:
  - manager URL and manager hostname,
  - relative agent token path resolution,
  - refresh/process interval defaults,
  - node host/user/port entries.
- Added agent sampling foundation:
  - `nvidia-smi` GPU CSV parsing,
  - `nvidia-smi` process CSV parsing,
  - `/proc/<pid>/stat` parent PID parsing,
  - `/proc/<pid>/cmdline` detail status parsing,
  - process task-name inference and command-line hashing,
  - snapshot collector refresh/process interval and history publication.
- Updated local service scripts:
  - setup builds the Rust release binary and frontend assets,
  - start runs `target/release/constella serve`,
  - manager hostname can be read from `nodes.yaml` through the Rust CLI,
  - highres is served by the Rust manager instead of a separate sidecar,
  - local agent startup is disabled until the Rust agent loop lands.
- Current Rust verification: `cargo fmt --check` and `cargo test`.

## Remaining Work

1. Agent sampling rewrite
   - Add native NVML sampler or document `nvidia-smi` as the Rust fallback path.
   - Connect collector to an agent WebSocket client loop.
   - Add hardware hello payload generation.

2. High-resolution job curves
   - Add end-to-end WebSocket integration tests against a live ephemeral server.
   - Add sidecar-compatible mode if the standalone highres sidecar remains required.

3. Analytics
   - Complete off-hours/night/weekend timezone segmentation.
   - Add broader range edge-case coverage.

4. Cluster control and YAML nodes
   - Port SSH agent deployment/status/stop flow or replace scripts with Rust CLI equivalents.
   - Keep token handling through stdin/env files, not command-line arguments.

5. Scripts and packaging
   - Restore local agent startup once the Rust agent loop lands.
   - Keep safe defaults: bind to `127.0.0.1:8765`, no DB unless `CONSTELLA_DB_PATH`/`DB_PATH` is set.
   - Add release build instructions and artifact layout.

6. Documentation
   - Update README and README_zh from Python/uv to Rust/cargo release workflow.
   - Document API compatibility and migration notes.

7. Final verification
   - Run Rust unit/integration tests.
   - Run frontend build without API source changes unless required.
   - Start Rust server on a non-8765 port for smoke tests.
   - Verify no access to the existing `run/constella.db` unless explicitly configured.
