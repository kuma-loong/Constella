# Changelog

All notable changes to Constella are documented in this file. Python
distribution versions follow PEP 440; Git tags use a hyphenated equivalent.

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
- Hardened high-resolution downsampling, GPM sampling isolation, chart refresh
  costs, and remote-agent runtime packaging.

### Compatibility

- Requires Python 3.10 or newer.
- Supports NVIDIA GPU and Ascend NPU clusters. NVIDIA GPM metrics require
  compatible NVML hardware, driver, and metric profiles.
- The TUI continues to use the existing manager HTTP and `/ws/cluster` APIs.

[0.1.3rc1]: https://github.com/kuma-loong/Constella/releases/tag/v0.1.3-rc.1
