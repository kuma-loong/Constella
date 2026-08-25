# Constella Agent Guide

## Priorities

- Keep Constella lightweight, fast, and suitable for public release.
- Prefer small, reviewable changes over framework migrations or broad rewrites.
- Do not add dependencies when the existing Preact, TypeScript, CSS, uPlot, FastAPI, and Python stack can solve the problem.
- Preserve compatibility with existing agents, databases, APIs, light/dark themes, and mobile layouts.

## Production safety

- Never stop, restart, reconfigure, or bind over the production services on ports `8765` and `8766` unless the user explicitly requests it.
- Never open the production database in write mode for development or testing.
- Use isolated preview ports such as `8875` and `8876`, with tokens, state, logs, and SQLite data under a dedicated `run/preview-*` directory.
- Resolve and verify exact PIDs and command lines before stopping any preview process.

## Git and change management

- Use a dedicated branch with the `codex/` prefix for feature work.
- Preserve unrelated user changes and untracked files. Do not use destructive Git commands.
- Keep commits focused and use clear imperative messages.
- Do not commit generated frontend `dist` files unless packaging explicitly requires them.

## Code organization

- Keep source files below 2,000 lines. Split by responsibility before a file grows beyond that limit.
- Extract repeated logic longer than roughly five lines into a shared function, component, or data definition.
- Prefer typed data-driven definitions over repeated conditionals and markup.
- Frontend styles are organized as:
  - `styles-base.css`: tokens and shared application primitives
  - `styles-performance.css`: Performance page and guide
  - `styles-analytics.css`: history, Jobs, tables, and charts
  - `styles-responsive.css`: breakpoint-specific behavior
- Keep performance collection capability-driven. Unsupported NVLink metrics must not be queried repeatedly or shown in the UI.

## Frontend design rules

- Design for a dense infrastructure console: `DESIGN_VARIANCE 4`, `MOTION 2`, `VISUAL_DENSITY 8`.
- Use Geist for interface text and Geist Mono or tabular figures for dense numeric data.
- Avoid generic card grids, excessive shadows, decorative gradients, glass effects, and unnecessary animation.
- Use one restrained green accent for application state. GPU telemetry curves use the cobalt `--telemetry` color rather than monitoring green.
- Shape system:
  - structural data surfaces, charts, detail strips, and notices use `--radius-panel` and are square;
  - inputs, selects, and action buttons use `--radius-control` (`6px`);
  - compact filter labels use `--radius-label` (`4px`);
  - `--radius-round` is reserved for true circular markers.
- Labels belong above form controls. Search and metric controls must not resize when their value or result state changes.
- Prefer continuous data bands and divided rows over multiple nested rounded cards.
- Preserve visible hover, active, focus, loading, empty, error, and disabled states.
- Maintain WCAG-readable contrast in both themes and a visible keyboard focus ring.
- At widths below `760px`, explicitly stack content and keep search, metric, refresh, chart, legend, and modal actions usable without horizontal page overflow.
- Use the existing Lucide icon set. Do not hand-draw SVG icons or mix icon families.

## Performance UI conventions

- Keep Performance groups ordered as Compute, Memory, Interconnect, then Non-Tensor Pipelines.
- Display PCIe and NVLink throughput in `MiB/s` or `GiB/s`.
- Read `supported_metrics` before exposing optional interconnect curves; hide unsupported NVLink metrics.
- Reserve enough y-axis width for complete throughput labels on inline and expanded charts.

## Required validation

Run checks proportional to the change, and run the full set before release:

```bash
cd frontend && npm run build
uv run pytest -q
uv run ruff check .
git diff --check
```

- Visually inspect affected pages in desktop light mode, desktop dark mode, and a narrow mobile viewport.
- Confirm preview health and verify that production PIDs, ports, and database paths are unchanged.
