# Performance page profiling — 2026-10-24

## English

Correction on 2026-10-26: the shared chart budget was reverted. Every curve again requests up to 1,000 points, regardless of the number of selected GPUs. The historical “After” timings below include a reduced budget of 300 points per curve for eight GPUs; they are not a comparison at equal resolution and do not describe the restored budget.

The slow path combined repeated history scans, large responses, overlapping polling, and drawing every chart on page entry. This investigation used Python cProfile and Chrome CPU profiles through Playwright/CDP. Production was queried read-only; benchmarks used an isolated preview on port 8875 and synthetic in-memory data, without opening a production database.

The stress fixture contains 8 GPUs, 11 metrics, and 14,400 samples per GPU: a full two-hour ring at 0.5-second intervals, including missing samples. The baseline is commit `18ca326`. Browser measurements use a 1440 × 1000 viewport; the network case uses 10 Mbps download and 80 ms latency. First-chart time runs from navigation until a chart canvas is ready; switching includes the click, response, and drawing. These are local observations, not public-endpoint latency guarantees or Core Web Vitals measurements.

| Observation | Before | After |
| --- | ---: | ---: |
| First chart, throttled network | 3.70 s | 1.31 s |
| Switch to two hours, throttled network | 4.75 s | 0.97 s |
| Switch to two hours, local network | 2.83 s | 0.72 s |
| Switch node / toggle GPU, throttled network | — | 0.30 s / 0.38 s |
| Fifteen-minute API, median of three requests | 285 ms | 74 ms |
| Two-hour API, median of three requests | 864 ms | 499 ms |
| Two-hour response transferred | 2.30 MB | 0.256 MB |
| Maximum simultaneous browser requests | 2 | 1 |

Python profiling identified `metric_series` scanning and materializing the same GPU time window once per metric. Queries now locate the window once per GPU with binary searches and contiguous array slices. Wrapped rings, inclusive boundaries, duplicate timestamps, and backwards clock adjustments retain their behavior. Summary-only requests do not construct drawing timelines. Min/max sampling avoids Python comparison callbacks and redundant per-bucket sorting.

Browser profiling identified bulk canvas creation and large repeated downloads. The frontend now draws plots near the viewport, skips redundant initial data/size updates, reuses time formatters, and uses an independent 1,000-point budget per series. The initial shared 2,400-point chart budget was reverted because it reduced resolution when more GPUs were selected. Existing min/max sampling preserves bucket extrema and missing-data gaps; Avg, Peak, P95, and Coverage still use every original sample in the selected interval. Curves remain straight lines.

Polling waits for completion before scheduling its next refresh. Filter changes abort obsolete requests, and manual refresh follows the same cancellation path. Requests reuse the existing authentication and 15-second timeout handling. Only the performance HTTP endpoint negotiates gzip, using the existing Starlette middleware at compression level 1; WebSockets and other endpoints are unchanged. No dependency was added.

The final browser trace recorded no main-thread tasks longer than 50 ms. Separate regression runs covered responses slower than the two-second polling interval, obsolete responses after node changes, node/GPU filters, scrolling into plots, folding/unfolding, interval selection, and resuming live mode. Desktop light/dark and a 390-pixel viewport were inspected. Validation passed: 542 Python tests, 18 frontend tests, Core/Lab builds, Ruff, and `git diff --check`. Production processes and database configuration remained unchanged.

Reproduce the backend measurements without a database:

```bash
uv run python scripts/dev/profile_performance.py --gpus 8 --max-points 1000 \
  --profile run/performance-query.prof
uv run python -m pstats run/performance-query.prof
```

The benchmark defaults to the restored 1,000-point budget. Use `--max-points 300` only to reproduce the initial, lower-resolution eight-GPU benchmark. The benchmark refuses to run if `CONSTELLA_DB_PATH` is set. The local raw profiles, timing JSON, and visual captures are retained under `run/preview-performance-latency/`; generated artifacts are not committed.

## 中文

2026-10-26 更正：已撤销每图共享点数预算，恢复每条曲线独立请求最多 1,000 点，不随所选 GPU 数量变化。下表历史“优化后”结果包含八张 GPU 时每条仅 300 点的降分辨率设置，并非同分辨率对比，也不代表恢复预算后的耗时。

慢路径由重复扫描历史、大响应体、轮询请求重叠以及进入页面时一次性绘制所有图表共同造成。本次使用 Python cProfile 和通过 Playwright/CDP 获取的 Chrome CPU profile 调查。生产环境仅进行只读请求；基准测试使用 8875 端口的隔离预览和内存中的模拟数据，没有打开生产数据库。

压力测试包含 8 张 GPU、11 个指标、每张 GPU 14,400 个采样点，相当于以 0.5 秒间隔填满两小时环形缓存，并包含缺失采样。基线为提交 `18ca326`。浏览器视口为 1440 × 1000，限速场景设置为下载 10 Mbps、延迟 80 ms。首次出图统计从导航开始到图表画布就绪；切换耗时包括点击、响应和绘图。这些是本机观测结果，不是公网端点的延迟保证，也不是 Core Web Vitals 测量值。

| 观测项 | 优化前 | 优化后 |
| --- | ---: | ---: |
| 限速网络首次出图 | 3.70 秒 | 1.31 秒 |
| 限速网络切换两小时 | 4.75 秒 | 0.97 秒 |
| 本机网络切换两小时 | 2.83 秒 | 0.72 秒 |
| 限速网络切换节点 / 勾选 GPU | — | 0.30 秒 / 0.38 秒 |
| 十五分钟接口，三次请求的中位数 | 285 ms | 74 ms |
| 两小时接口，三次请求的中位数 | 864 ms | 499 ms |
| 两小时响应实际传输量 | 2.30 MB | 0.256 MB |
| 浏览器最大同时请求数 | 2 | 1 |

Python profiling 发现 `metric_series` 会为每个指标重复扫描、构造同一张 GPU 的时间窗口。现在每张 GPU 通过二分查找只定位一次窗口，再使用连续数组切片。缓存绕回、包含边界、重复时间戳和时钟回拨保持原有行为。仅统计请求不再构造绘图时间线；最小值/最大值采样移除了 Python 比较回调和每个桶内多余的排序。

浏览器 profiling 发现了集中创建画布和重复大文件下载的开销。现在只绘制视口附近的图表，跳过重复的初始数据与尺寸更新，复用时间格式化器，并使用每条曲线独立 1,000 点的预算。最初每图共享 2,400 点的预算已撤销，因为它会在选择更多 GPU 时降低分辨率。现有最小值/最大值采样保留桶内极值和缺失数据断点；Avg、Peak、P95 和 Coverage 仍由所选时间段的全部原始样本计算。曲线继续使用直线连接。

轮询等待当前请求完成，再安排下一次刷新。切换筛选条件会取消旧请求，手动刷新也使用同一取消路径。请求复用已有鉴权和 15 秒超时处理。仅 Performance HTTP 接口通过现有 Starlette 中间件协商 gzip，压缩级别为 1；WebSocket 和其他端点保持原有行为。没有新增依赖。

最终浏览器 trace 没有记录到超过 50 ms 的主线程任务。独立回归验证覆盖超过两秒轮询间隔的慢响应、切换节点后的旧响应、节点/GPU 筛选、滚动到图表、折叠/展开、区间选择与恢复实时模式，并检查了桌面亮/暗色和 390 像素宽度布局。检查通过：542 项 Python 测试、18 项前端测试、Core/Lab 构建、Ruff 和 `git diff --check`。生产进程和数据库配置保持不变。

使用上方命令可在不使用数据库的情况下复测后端。基准程序默认使用已恢复的 1,000 点预算；`--max-points 300` 仅用于复现最初八张 GPU 时降低分辨率的基准结果。设置了 `CONSTELLA_DB_PATH` 时，基准程序会拒绝运行。本机原始 profile、耗时 JSON 和视觉截图保存在 `run/preview-performance-latency/`，生成文件不会提交。
