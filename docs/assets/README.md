# README previews

The four current README images are cropped from the real frontend, with no UI restyling or compositing. Each image focuses on one feature instead of showing a full page. Images use the light theme, a 1440 px desktop viewport and 2× pixel density. Both README languages use the same assets in a 2×2 table, with each image linked to its full-resolution file.

| File | Focus |
| --- | --- |
| `01-cluster-overview.png` | Resource summary and four-node NVIDIA / Ascend inventory. |
| `02-node-detail.png` | GPU cards, telemetry, and process ownership. |
| `03-performance.png` | GPU selection, real SM Active curves, average / peak / P95 / coverage statistics. |
| `04-job-search.png` | Search results, completed training task, and four-GPU curves with inspected values. |

## Data and privacy

- Node names, users, UUIDs, PIDs, commands, and task details are synthetic examples defined in `scripts/dev/readme-demo-data.mjs`.
- Public hardware model names are illustrative inventory, not a disclosure of cluster topology.
- Performance samples were obtained through a read-only local production API request with the user's authorization. `performance-samples.json.gz` contains only allowlisted metric data, public hardware model names, and synthetic identifiers. Four H100 series were selected; their numerical samples and summary statistics were preserved without smoothing or scaling.
- Original timestamps were shifted by a constant offset to the demo timeline. Original node names, UUIDs, GPU indices, users, PIDs, job identifiers, commands, addresses, credentials, and paths are not included in that fixture.
- All other telemetry and workload history is synthetic. These images are feature illustrations, not benchmark claims.
- Capture intercepts all frontend API and WebSocket traffic. Reproduction does not access production services or any database. Unexpected API calls fail the capture.

## Reproduce

Use an existing Playwright installation with Chromium; it is a capture-only tool, not an application dependency.

From the repository root, build and start a standalone static preview:

```bash
npm --prefix frontend run build
npm --prefix frontend run preview -- --port 8875 --strictPort
```

In another terminal, from the repository root:

```bash
# If Playwright is already resolvable and its browser is installed:
node scripts/dev/capture-readme.mjs

# Otherwise point to an existing installation and browser:
PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs \
CHROME_PATH=/absolute/path/to/chrome \
node scripts/dev/capture-readme.mjs
```

The capture writes the four PNGs, `asset-manifest.json`, and `review-report.json`. Full-page review images and browser verification results stay under ignored `run/preview-readme/`. Review desktop light, desktop dark, and 390 px mobile captures; the automated report checks page overflow and browser/API errors. It does not replace visual review.

`demo-data-summary.json` describes the current dataset. Older PNG/GIF files remain as historical assets and are no longer referenced by either README; their obsolete capture tools are not needed for this workflow.
