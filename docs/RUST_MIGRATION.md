# Constella Rust 迁移预览

`codex/rust-manager-agent` 分支当前提供两个 Linux 二进制程序。Agent 使用驱动自带的 NVML 或 DCMI 动态库。Manager 使用随二进制构建的 SQLite，直接服务现有 Web 静态文件。构建机需要 Rust、C 编译器和前端构建工具。运行这两个程序需要系统 C 运行库，计算节点还需要对应的加速器驱动。

## 构建和预览

```bash
cargo build --release --locked --workspace
(cd frontend && npm ci && npm run build)
```

启动本地预览 Manager。

```bash
umask 077
mkdir -p run/preview-rust
test -s run/preview-rust/agent-token || openssl rand -hex 32 > run/preview-rust/agent-token
env -u CONSTELLA_AGENT_TOKEN \
  CONSTELLA_BIND=127.0.0.1:8875 \
  CONSTELLA_AGENT_TOKEN_FILE="$PWD/run/preview-rust/agent-token" \
  CONSTELLA_DB_PATH="$PWD/run/preview-rust/constella.sqlite3" \
  CONSTELLA_FRONTEND_DIST="$PWD/frontend/dist" \
  target/release/constella-manager
```

在另一个终端启动预览 Agent。昇腾节点使用 `CONSTELLA_DEVICE_TYPE=ascend`，可通过 `CONSTELLA_DCMI_LIBRARY` 指定驱动库的路径。

```bash
env -u CONSTELLA_AGENT_TOKEN \
  CONSTELLA_MANAGER_URL=http://127.0.0.1:8875 \
  CONSTELLA_AGENT_TOKEN_FILE="$PWD/run/preview-rust/agent-token" \
  CONSTELLA_NODE_ID=preview-rust-h100 \
  CONSTELLA_DEVICE_TYPE=nvidia \
  target/release/constella-agent
```

通过 `/api/health` 检查进程和 SQLite 状态，通过 `/api/cluster/snapshot` 检查节点与加速器。使用 `constella-agent --sample-once` 检查单次读数。HighRes 流可用 `CONSTELLA_HIGHRES_TOKEN_FILE` 配置单独的令牌。

## 阶段测量

2026-11-18 在同一台 8 卡 H100 主机上，以 1 秒采样、5 秒进程刷新、新建预览数据库和一个 WebSocket 客户端各运行约 60 秒。两版均启用 GPM。CPU 数字按一个逻辑核心的占用百分比计算。入库延迟从 Agent 开始采样计时，经独立 SQLite 连接每 20 毫秒检查一次已提交的记录。HTTP 延迟包含本地连接和 JSON 解码。每项 HTTP 查询测量 20 次。

| 指标 | Python | Rust |
| --- | ---: | ---: |
| Agent RSS 中位数 | 63 MiB | 22.5 MiB |
| Agent CPU | 1.309% | 0.958% |
| Agent 采样耗时中位数 / p95 | 16.900 / 399.000 ms | 12.768 / 232.000 ms |
| 观测样本 / 预期样本 | 60 / 60 | 61 / 61 |
| 观测漏采率 | 0% | 0% |
| Manager RSS 中位数 | 63 MiB | 4.5 MiB |
| Manager CPU | 0.795% | 0.182% |
| 采样到 SQLite 提交中位数 / p95 | 36.583 / 415.015 ms | 23.931 / 237.638 ms |
| 快照 HTTP 中位数 / p95 | 1.714 / 1.873 ms | 0.681 / 1.450 ms |
| GPU 历史 HTTP 中位数 / p95 | 1.161 / 1.249 ms | 0.677 / 1.580 ms |

这组数据对应 NVIDIA/GPM、实时推送、HighRes 性能缓存和基础历史入库阶段。开启 GPM 汇总逐样本入库后复测 Rust，Agent RSS 为 22.5 MiB、CPU 为 1.104%，Manager RSS 为 6 MiB、CPU 为 0.198%。采样耗时中位数 / p95 为 31.404 / 382.811 ms，采样到提交为 43.037 / 387.624 ms，GPU 历史 HTTP 为 0.800 / 1.294 ms。61 个预期样本全部收到，数据库记录了 32 行 GPM 汇总及 488 个有效 SM Active 读数。采样和提交耗时在两轮间波动，正式验收需重复对照。两版读取的预览历史行数随归档节奏变化，查询数据量需要在正式对照中固定。

使用开发环境复测。每次运行会在 `run/preview-benchmark-*` 写入原始 JSON、日志和独立数据库。

```bash
uv sync --all-packages
.venv/bin/python scripts/dev/benchmark-runtime.py python --seconds 60
.venv/bin/python scripts/dev/benchmark-runtime.py rust --seconds 60
```

## 长期历史与 Analytics

Rust Manager 在 SQLite 写入线程上分批将 20 秒汇总提升为 2 分钟和 1 小时汇总。归档进度与目标汇总在同一事务内提交，重启后继续处理。迟到样本可重新触发仍有完整源数据的历史窗口。GPU 汇总按样本数加权，GPM 按各指标的有效读数单独加权。过期汇总和原始快照定期清理，长期未出现的进程会话标记为结束。

Jobs 的 GPU 和 Performance 曲线会在内存缓存完整覆盖短任务时使用原始采样，其余情况使用 SQLite 汇总。节点 Analytics 和总览沿用既有接口。总览与 Jobs 共用任务键生成逻辑，缺少进程启动时间的旧记录按会话首次出现时间生成链接。总览对排名分数相同的任务使用稳定排序。节点图、热力图和总览查询在同一 SQLite 读事务内生成，60 秒缓存合并同键并发查询。

在相同 8 卡主机上，两版各跑约 30 秒的补充对照：

| 指标 | Python | Rust |
| --- | ---: | ---: |
| Agent RSS / CPU | 64.5 MiB / 1.462% | 22.5 MiB / 0.980% |
| Manager RSS / CPU | 61.5 MiB / 0.798% | 6.0 MiB / 0.196% |
| Agent 采样耗时中位数 / p95 | 39.200 / 244.400 ms | 24.515 / 274.708 ms |
| 采样到 SQLite 提交中位数 / p95 | 56.766 / 265.636 ms | 36.239 / 291.537 ms |
| 观测样本 / 预期样本 | 30 / 30 | 31 / 31 |
| 节点 Analytics 冷查询 / 暖中位数 | 2.869 / 0.342 ms | 0.928 / 0.226 ms |
| 总览 Analytics 冷查询 / 暖中位数 | 2.575 / 0.351 ms | 0.715 / 0.185 ms |
| Jobs 列表 HTTP 中位数 / p95 | 1.253 / 1.299 ms | 0.683 / 1.672 ms |

该组测试使用新建预览库。后面另有从实际遥测库备份进行的历史查询测量。

## SQLite 一次性迁移与副本验证

用 SQLite 在线备份接口从只读连接创建遥测库和 Lab 身份库副本。对已有副本可先运行显式迁移命令。

```bash
CONSTELLA_DB_PATH="$PWD/run/preview-db-copy/constella.db" \
  target/release/constella-manager migrate
CONSTELLA_LAB_DB_PATH="$PWD/run/preview-db-copy/constella-lab-identity.sqlite3" \
  target/release/constella-manager migrate-lab
```

两个迁移命令均可重复执行。遥测迁移沿用原表和列，并创建归档进度表。对相同遥测副本运行两次后，原有 8 张用户表的行数一致。Lab 身份迁移支持旧表的引导状态字段回填。实际身份库副本执行两次后，用户、绑定、审计记录和迁移历史逐字段一致，且通过 `PRAGMA quick_check` 和 `PRAGMA foreign_key_check`。

Rust Manager 在遥测副本上读取的 GPU 历史、进程会话、用户汇总、Jobs 条目和三档曲线与 Python 对照通过。节点 Analytics 的四个范围与 Python 返回一致。总览的用户榜与异常条目一致，Jobs 榜单在同分位置使用稳定的任务键排序。另在隔离空库走 Agent WebSocket 接口验证了 Jobs 原始 GPU 和 GPM 曲线，以及强制切换到 20 秒汇总的行为。

## Lab 鉴权和节点管理预览

使用 `constella-manager lab` 选择 Lab 版。默认命令启动社区版，方便沿用已有环境文件中同时存在的社区版和 Lab 变量。Lab 版读取 `CONSTELLA_AUTH_MODE=cloudflare-access`、`CONSTELLA_LAB_DB_PATH`、`CONSTELLA_ACCESS_TEAM_DOMAIN`、`CONSTELLA_ACCESS_AUD`、`CONSTELLA_BOOTSTRAP_ADMIN_EMAIL` 和 `CONSTELLA_PUBLIC_ORIGIN`。通过 `CONSTELLA_FRONTEND_DIST` 指向使用 `CONSTELLA_FRONTEND_MODE=lab` 构建的静态前端。

Lab 从配置的 Access Team 域获取 HTTPS JWKS，校验 `Cf-Access-Jwt-Assertion` 中的应用令牌以及签名、颁发者、受众和时间。缓存仍有效且已知签名密钥的请求可以继续处理，同时新密钥的网络刷新按需串行执行。浏览器的状态变更要求同源标记，WebSocket 按令牌有效期关闭，并定期复查账号状态。Lab 的机器 HighRes 流要求独立令牌。 [Cloudflare Access JWT 验证文档](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)

账户管理保留显示名、只读引导、同邮箱的新身份复核、最后一位活跃管理员保护，以及绑定、解绑、验证、转移和审计。Manager 通过 Agent 已鉴权的 WebSocket 请求 Linux NSS 查询。Agent 重启仅对选定实例执行重载，PID 保持，实例 ID 更新，审计记录同时保留请求与结果。

```bash
uv sync --all-packages
.venv/bin/python scripts/dev/check-rust-lab.py
```

该测试在隔离端口使用本地 HTTPS JWKS、临时身份库及单个实际 Agent，覆盖身份验证、CSRF、WebSocket 到期、授权和账户、重启操作。已在桌面浅色、桌面深色、390 px 窄屏检查 Lab 管理和账号设置页面。

在相同本地 JWKS 夹具及已登录账号上，各运行 80 次热 `GET /api/lab/me` 查询。Python HTTP 中位数 / p95 为 0.563 / 0.865 ms，Rust 为 0.410 / 0.434 ms。独立的社区版 8 卡采样回归在约 30 秒内收到 31/31 个预期样本。Rust Agent RSS / CPU 为 22.5 MiB / 0.689%，Manager 为 9 MiB / 0.131%。采样中位数 / p95 为 9.838 / 139.146 ms，采样到入库为 21.254 / 146.745 ms。该轮未包含真实 Cloudflare 网络往返或大规模历史库负载。

Lab 个人活动按节点和 UID 归属历史会话。旧会话没有 UID 时使用同节点的用户名。初次绑定可以认领已记录的历史，已转移的会话按各次绑定的有效期归属，转移之间的空档保留为未绑定。双 die 的重叠时间按物理卡合并。本地签名夹具已与 Python 对照个人时间线、Jobs 归属、跨用户分页隔离和总览。个人活动页也通过桌面浅色、桌面深色及 390 px 窄屏浏览器检查。

## 发布包复测与切换

[构建、部署、备份及回滚](DEPLOYMENT.md)覆盖两个 Manager 版本及 Agent。已生成 Linux x86_64 和 ARM64 两套包并验证 SHA-256。两套包均需要 glibc 2.34 或更新版本。发布包里的 `backup`、`backup-lab`、`migrate` 和 `migrate-lab` 已在预览副本上验证。重复迁移后，遥测源表行数和身份库各表内容保持一致，两库通过 SQLite 完整性和外键检查。Agent 在空 PATH 的环境中使用驱动库直接采样。现有 Web 前端及 TUI 源码保持原样。

在同一台 8 卡 H100 主机串行跑相同设置的 90 秒采样对照。Python 使用旧版 Agent 和 Manager，Rust 使用发布包。进程刷新每 5 秒；采样到入库的探针分辨率为 20 毫秒。

| 指标 | Python | Rust |
| --- | ---: | ---: |
| Agent RSS 中位数 / CPU | 63.0 MiB / 1.148% | 22.5 MiB / 0.683% |
| Manager RSS 中位数 / CPU | 61.5 MiB / 0.784% | 10.5 MiB / 0.165% |
| Agent 采样耗时中位数 / p95 | 16.800 / 390.300 ms | 7.525 / 297.699 ms |
| 观测样本 / 预期样本 | 90 / 90 | 91 / 91 |
| 采样到 SQLite 提交中位数 / p95 | 37.902 / 404.865 ms | 23.144 / 312.197 ms |
| GPU 历史 HTTP 中位数 / p95 | 1.068 / 1.202 ms | 0.988 / 1.051 ms |
| 节点 Analytics 冷查询 / 暖中位数 | 6.678 / 0.429 ms | 1.161 / 0.276 ms |
| 总览 Analytics 冷查询 / 暖中位数 | 1.998 / 0.350 ms | 0.718 / 0.186 ms |
| Jobs 列表 HTTP 中位数 / p95 | 1.175 / 1.813 ms | 0.599 / 0.680 ms |

历史查询对照使用同一份约 937 MiB 的遥测库源副本及同一实际节点。测量中无 Agent 接入，冷查询指应用进程内缓存未命中，系统文件缓存未清空。

| 历史查询 | Python | Rust |
| --- | ---: | ---: |
| GPU 历史 HTTP 中位数 / p95 | 9.831 / 10.074 ms | 10.040 / 11.000 ms |
| 30 天节点 Analytics 冷查询 / 暖中位数 | 382.728 / 2.758 ms | 176.251 / 2.660 ms |
| 7 天总览 Analytics 冷查询 / 暖中位数 | 762.544 / 0.486 ms | 348.933 / 0.343 ms |
| Jobs 列表 HTTP 中位数 / p95 | 583.376 / 1017.998 ms | 168.534 / 172.895 ms |

可用同一份 SQLite 副本复测，脚本以只读方式生成各次运行的工作库。

```bash
.venv/bin/python scripts/dev/benchmark-runtime.py python --history-db /path/to/telemetry-copy.sqlite3 --history-node node-id
.venv/bin/python scripts/dev/benchmark-runtime.py rust --history-db /path/to/telemetry-copy.sqlite3 --history-node node-id
```

短时对照使用同一台共享主机。每轮的驱动耗时受当时负载影响，漏采率仅指该次观测窗口。发布包已在 H100 上验证 NVML 和 `nvidia-smi` 回退，也用模拟昇腾设备验证 DCMI、`npu-smi`、共享 PCI 地址的多 die、实时推送及各节点独立的调试快照。

在 QEMU 下运行 ARM64 发布包，Agent 通过模拟的 ARM64 DCMI 采集双 die 和进程数据，经 WebSocket 分别写入社区版和 Lab 的 SQLite 预览库。ARM64 Lab 还通过 HTTPS JWKS 登录及身份库迁移检查。

本地 TLS 机器入口夹具验证了服务令牌和 Agent Bearer 令牌同时生效。缺少服务令牌时夹具拒绝握手，只提供服务令牌时 Manager 拒绝接入。Agent 在明文连接配置中会拒绝加载这对凭据。昇腾按双方确认的模拟采样范围验收。

## 实际节点并行验收

两套独立 Rust Manager 各自使用一份生产遥测 SQLite 在线备份，Lab 另外使用生产身份库副本。现有五台在线生产计算节点全部启动了配套的独立 Rust Agent，沿用节点 ID，每套 Manager 都收到 26 张 GPU 的真实 NVML 遥测及进程数据。远端 Agent 使用 SSH 转发连接预览 Manager 的回环端口。社区版浏览器 WebSocket 及两版 HighRes 机器流均收到全部五台节点的数据。

同一固定历史小时的 4,680 行 GPU 汇总与两份预览副本逐行匹配。Lab 账户和绑定匹配，比较时排除复制后生产仍会更新的登录及更新时间。连续 45 秒内，两个 Rust Manager 各提交 226 个样本，三套服务均保持 5 节点、26 卡在线，数据库丢弃计数未增加。

预览域名通过现有 Cloudflare Tunnel 到达独立 Lab。已验证公网入口跳转到现有 Cloudflare Access 团队，预览身份库也记录了上线后通过签名校验的实际登录。验收实例继续独立运行。

在三套服务并行处理真实流量时，又核对了最新五个全部入库的 20 秒窗口。每套数据库均包含同样的 130 个 GPU 桶。另在 41 秒的本机观察中，两套 Rust Manager 各提交 200 次采样，数据库丢弃计数未增加。Rust Lab 的进程 RSS 中位数为 39.28 MiB，CPU 占单核 0.902%。生产 Lab 的运行时长和页面负载与预览不同，因此独立的受控对照仍以上面的 H100 测量为准。

## 多客户端实时推送

同一版本的集群快照在 HTTP 和 WebSocket 客户端间共享一次 JSON 编码。当采样、设置或节点在线状态变化时刷新，空闲时最多保留五秒。使用五台实际节点的遥测样本回放，在隔离 Manager 各运行 25 秒并连接 16 个客户端，测试关闭了 SQLite 入库。每个客户端均收到至少 26 个快照，末帧约 236 KB。每个客户端单独编码时占单核 1.677%，共享编码后为 0.679%。此改动单独验证和打包，正在使用的验收实例保持原部署版本。
