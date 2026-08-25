# Changelog

All notable changes to Constella are documented in this file. Python
distribution versions follow PEP 440; Git tags use a hyphenated equivalent.

## [0.1.3] - 2026-08-25

Final release of 0.1.3, incorporating the release candidate and the following
refinements.

### Added

- Added PCIe and capability-gated NVLink throughput to the Web and TUI
  Performance views.
- Added a bilingual in-product guide for performance metrics and their
  interpretation.

### Fixed

- Stabilized TUI Performance curves with fixed time bins so live samples scroll
  left instead of reflowing the full chart on every refresh.
- Corrected swapped NVML permission and buffer-size return codes that caused
  normal process queries to fall back to a slow `nvidia-smi` subprocess.

### Changed

- Refined the Web Performance workspace, Jobs surfaces, responsive layout, and
  chart presentation across desktop and mobile viewports.
- Reordered performance groups as Compute, Memory, Interconnect, and Non-Tensor
  Pipelines, with complete throughput labels and consistent MiB/s or GiB/s
  units.
- Send NVIDIA GPM `supported_metrics` on the first sample and capability changes
  instead of repeating the unchanged list in every agent snapshot.
- Cache slow-changing NVML device metadata for 60 seconds and prefer the v2
  memory query without issuing the legacy query first.

### Removed

- Removed retired single-node API shims and unreferenced backend compatibility
  helpers superseded by the cluster API and current collector paths.

## [0.1.3rc1] - 2026-08-23

First release candidate for 0.1.3.

### Added

- Added isolated NVIDIA NVML GPM collection with profile-aware metric groups.
- Added in-memory high-resolution performance curves and SQLite performance
  rollups with independent retention controls.
- Added performance status, history, and job-level API endpoints.
- Added the Web performance workspace with metric selection, summaries, and
  interactive charts.
- Added a keyboard-first TUI Performance view with seven compact Braille curves,
  range and GPU navigation, live pause/resume, summaries, and capability states.

### Changed

- Split deployment responsibilities across four PyPI distributions:
  `constella-gpu` for the complete installation, `constella-gpu-web` for the
  central Web service, `constella-gpu-backend` for the API and collectors, and
  `constella-gpu-tui` for the standalone terminal client.
- Removed the backend dependency from `constella-gpu-tui`; it now installs only
  the client-side Textual and WebSocket runtime.
- Changed TUI History to overlay every GPU on the selected node on shared
  utilization and memory charts, with eight hue-separated colors and GPU
  highlighting.
- Hardened high-resolution downsampling, GPM sampling isolation, chart refresh
  costs, and remote-agent runtime packaging.

### Compatibility

- Requires Python 3.10 or newer.
- Supports NVIDIA GPU and Ascend NPU clusters. NVIDIA GPM metrics require
  compatible NVML hardware, driver, and metric profiles.
- The TUI continues to use the existing manager HTTP and `/ws/cluster` APIs.

[0.1.3]: https://github.com/kuma-loong/Constella/releases/tag/v0.1.3
[0.1.3rc1]: https://github.com/kuma-loong/Constella/releases/tag/v0.1.3-rc.1
