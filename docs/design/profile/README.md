# Profile design prototype

Open `index.html` in a browser from this checkout after installing the existing frontend dependencies. Fonts, Lucide, and the logo are loaded from the repository; no new dependency or remote API is used.

The current direction is documented in [Profile dashboard research and proposal](../../profile-dashboard-research-zh.md). This activity timeline replaces the earlier smooth usage curves in the [initial specification](../../profile-dashboard-design-zh.md).

Implemented prototype interactions:

- Light/dark theme and 7/30-day activity timelines with hourly ticks.
- Expand a date into individual task tracks; cross-midnight jobs retain their identity.
- GPU-model and date filtering, task status filters, name/node/PID search, running-task shortcut. Running jobs stay visible when browsing historical activity.
- GPU hours, active time, model shares and busiest hours calculated from the same fixture intervals, with physical GPUs counted once when shared.
- Task drawer, GPU/Performance metric examples, unsupported-device state, command copying.
- Empty, unbound, partial-record, loading, and error scenarios, available at the bottom of the page. Missing observation intervals are excluded from totals and visibly marked.
- Mobile day selection with readable individual task tracks; reduced-motion support and keyboard controls.

All values are fixtures. Account settings and the full Jobs analysis are preview placeholders; they do not call real services or change accounts. “All jobs” expands the matching fixture list. Historical charts illustrate device-level telemetry, not process-exclusive performance. The prototype is deliberately separate from production routing and bundles.

Review at desktop light/dark and 320–390 px mobile widths. During this task, an isolated static preview was served on `127.0.0.1:8875`, with browser state and captures under `run/preview-profile-design/`. The preview has no database or authentication service.

The interface uses plain English for lab members. Cobalt time segments encode concurrent GPU counts; green is reserved for running state. Day rows support Up/Down, Home/End, and native Enter/Space activation. Expanding a day uses a short transition; there is no recap or automatic chart animation.
