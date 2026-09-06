# Profile design prototype

Open `index.html` in a browser from this checkout after installing the existing frontend dependencies. Fonts, Lucide, and the logo are loaded from the repository; no new dependency or remote API is used.

The Chinese design specification is [Profile 个人看板设计](../../profile-dashboard-design-zh.md).

Implemented prototype interactions:

- Light/dark theme, 7/30-day periods, animated totals and interactive activity traces.
- Date filtering, task status filters, name/node/PID search, running-task shortcut.
- Task drawer, GPU/Performance metric examples, unsupported-device state, command copying.
- Empty, unbound, partial-record, and error scenarios.

All values are fixtures. Account settings and Jobs navigation show their proposed destination; they do not call real services or change accounts. Historical charts illustrate device-level telemetry, not process-exclusive performance. The prototype is deliberately separate from production routing and bundles.

Review at desktop light/dark and 320–390 px mobile widths. During this task, an isolated static preview was served on `127.0.0.1:8875`, with browser state and captures under `run/preview-profile-design/`. The preview has no database or authentication service.

The interface uses plain English for lab members. Daily usage and hourly activity use thin cobalt lines with subtle area fills. The daily chart supports pointer/touch selection and arrow-key navigation followed by Enter to filter jobs. Implementation details stay in the design specification.
