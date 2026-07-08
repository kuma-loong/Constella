# Constella Assets

This folder contains high-resolution promotional captures generated from an isolated demo instance. The dataset is synthetic and uses generic labels such as `user1`, `node-a`, and `GPU Type 1 Accelerator`.

## Contents

| File | Purpose |
| --- | --- |
| `01-overview-realtime-cluster.png` | Dark theme realtime cluster overview with live node and GPU fabric status. |
| `02-node-gpu-process-detail.png` | Dark theme per-node GPU cards, live utilization, memory, power, thermal status, and process ownership. |
| `03-node-history-curves.png` | Dark theme node-level 24h historical analytics curves and GPU heatmap context. |
| `04-highres-job-curves.png` | Dark theme job search and high-resolution GPU curve view for a selected task. |
| `05-job-curve-interaction.gif` | Dark theme interaction demo that switches job curve metrics and opens the expanded chart. |
| `light-01-overview-realtime-cluster.png` | Light theme realtime cluster overview. |
| `light-02-node-gpu-process-detail.png` | Light theme per-node GPU and process detail view. |
| `light-03-node-history-curves.png` | Light theme node-level 24h historical analytics curves and heatmap context. |
| `light-04-highres-job-curves.png` | Light theme job search and high-resolution GPU curve view. |
| `light-05-job-curve-interaction.gif` | Light theme interaction demo for job curve metric switching. |
| `asset-manifest.json` | Capture metadata including viewport, scale factor, and file sizes. |
| `demo-data-summary.json` | Synthetic dataset summary used for privacy and consistency review. |
| `review-report.json` | Automated asset review: required files, dimensions, and forbidden text checks. |

## Reproduce

Run from the repository root in this worktree:

```bash
cd frontend
npm install
npm run build
cd ..
uv run uvicorn tools.promo_demo_server:app --host 127.0.0.1 --port 8876
node tools/capture_promo_assets.mjs
uv run python tools/review_promo_assets.py
```

The demo server uses an isolated synthetic database and port `8876`. It does not use the default `8765` service port and does not read or write any production database.

## Privacy Notes

- No hostnames, user names, paths, commands, or database records from a real environment are used.
- Users are named `user1` through `user6`.
- Nodes are named `node-a`, `node-b`, and `node-c`.
- GPUs use generic names such as `GPU Type 1 Accelerator`.
- Commands and task names are synthetic examples created for the demo.

## Review Checklist

- Confirm every screenshot is at least 1900 px wide and has clear text.
- Confirm visible users, nodes, GPU names, paths, and task names are generic.
- Confirm the visuals match intended Constella capabilities: realtime monitoring, GPU cards, user/process attribution, full-range 24h analytics curves, heatmaps, and high-resolution job curves.
- Confirm the local production service on port `8765` is not stopped, restarted, or modified during generation.
