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
- Current Rust verification: `cargo fmt --check` and `cargo test`.

## Remaining Work

1. Agent sampling rewrite
   - Replace Python NVML / `nvidia-smi` / procfs sampling with Rust.
   - Preserve process attribution fields and task inference behavior.
   - Add hardware hello payload generation.

2. High-resolution job curves
   - Port high-res in-memory GPU sample cache.
   - Port job grouping and job detail APIs.
   - Port adaptive rollup fallback for long jobs.
   - Implement `WS /api/highres/stream` publishing beyond the current hello stub.

3. Analytics
   - Port overview analytics.
   - Port node series and heatmap analytics.
   - Preserve range handling and Asia/Shanghai timezone semantics.

4. Cluster control and YAML nodes
   - Port `nodes.yaml` loading.
   - Port SSH agent deployment/status/stop flow or replace scripts with Rust CLI equivalents.
   - Keep token handling through stdin/env files, not command-line arguments.

5. Scripts and packaging
   - Update service scripts to run the Rust binary.
   - Keep safe defaults: bind to `127.0.0.1:8765`, no DB unless `CONSTELLA_DB_PATH` is set.
   - Add release build instructions and artifact layout.

6. Documentation
   - Update README and README_zh from Python/uv to Rust/cargo release workflow.
   - Document API compatibility and migration notes.

7. Final verification
   - Run Rust unit/integration tests.
   - Run frontend build without API source changes unless required.
   - Start Rust server on a non-8765 port for smoke tests.
   - Verify no access to the existing `run/constella.db` unless explicitly configured.

