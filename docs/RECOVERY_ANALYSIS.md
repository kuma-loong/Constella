# Browser recovery through Cloudflare Access and Tunnel

## English

### Investigation (2026-09-14)

Reviewed `ea229474f022971fb3e8f9046f258e4a540bf151`, the current Lab authentication path, production process metadata and recent application logs, and Cloudflare configuration through read-only API calls.

The published hostname `gpu.kumapower.com` resolves to the healthy `gputop-H100_server` tunnel, which routes to `http://127.0.0.1:8765`. WebSockets are enabled. Access application and allow-policy session durations are both `720h`. No custom zone rulesets or Page Rules were present. The inactive `autodl-Constella` tunnel serves a different hostname. No Cloudflare changes were made; these checks do not exclude intermittent network or edge failures.

The application log contains repeated groups of accepted WebSockets, snapshot requests and overview analytics queries. It does not include sufficient timing or browser-side failures to attribute every slow refresh to one cause.

### Code defects and changes

- Wake events could independently replace a socket and cancel/restart HTTP requests. Recovery now coalesces overlapping events and shares one in-flight HTTP probe. Manual refresh preserves an existing socket.
- The telemetry watchdog also timed out an unfinished handshake after roughly 15 seconds. Handshakes now have a separate 30-second first-message deadline and failures use bounded exponential backoff.
- Generic WebSocket failures did not probe HTTP authentication; only application close codes `4401` and `4403` did. Every retry now checks the snapshot endpoint, so opaque handshake failures can reveal an Access `401`.
- Socket retries and watchdog recovery also forced historical queries. Only explicit refresh and wake recovery refresh history now; connection retries use the lightweight snapshot endpoint.
- With no new samples, the backend sent no messages, so a healthy idle connection appeared dead. It now sends a fresh snapshot at least every five seconds (or the configured refresh interval, if longer), even when its sequence is unchanged.
- Jobs requests bypassed the shared timeout and Access AJAX headers. They now use the shared request helper, as do settings requests. The helper preserves caller headers and asks Access for a `401` on expired AJAX sessions.
- Snapshot rendering could overwrite socket status, including marking an idle connected cluster as connecting. Transport status now belongs to the connection controller; node health is displayed separately.
- Lab automatically reloaded the page on a `401`. It now unmounts the live application and displays a Reconnect action, preventing repeated background retries and reload loops. Clicking Reconnect performs a top-level navigation so Access can verify the session.

The existing stale-response isolation and independent history database reader are retained. No schema changes, new application dependencies, or production configuration changes are required.

### Verification and deployment boundary

Validation passed: 230 Python tests, 15 frontend tests, both frontend builds, Ruff and whitespace checks. A deterministic replay of one wake-event burst produced three additional sockets and three HTTP recoveries before the change, versus one of each afterward. Desktop light/dark and 390px mobile previews showed no page overflow or JavaScript exceptions; simulated session expiration stopped retries without automatic navigation. Real browser offline/online recovery and an 18-second idle connection check also passed.

Unit tests cover overlapping wake events, slow handshakes, opaque authentication failures, retry backoff, expired AJAX sessions, stale responses, and idle backend keepalives. Browser verification uses a local preview with no production database, synthetic identity responses and simulated Access expiration; it does not validate a real user's authenticated Cloudflare session.

Deploy the manager code and the Lab frontend bundle together through the normal release procedure. Building the ordinary frontend alone does not update the packaged Lab assets. After deployment, verify a normal reload, offline/online recovery, sleep/wake recovery, an idle cluster and expired-session reauthentication through the public hostname. Production restart remains a separate explicitly authorized operation.

References: [Access AJAX session handling](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/session-management/#ajax), [Cloudflare WebSockets](https://developers.cloudflare.com/network/websockets/).

## 中文

### 排查范围（2026-09-14）

检查了 `ea229474f022971fb3e8f9046f258e4a540bf151`、当前 Lab 鉴权链路、生产进程信息与近期应用日志，并通过只读 API 检查 Cloudflare 配置。

公开域名 `gpu.kumapower.com` 指向健康的 `gputop-H100_server` Tunnel，回源为 `http://127.0.0.1:8765`。WebSocket 已开启，Access 应用与允许策略的会话时长均为 `720h`，未发现自定义区域规则集或 Page Rules。离线的 `autodl-Constella` Tunnel 服务另一个域名。未修改 Cloudflare 配置；这些检查不能排除间歇性网络或边缘节点故障。

应用日志可见反复成组出现的 WebSocket 接受连接、快照请求和概览历史查询，但缺少请求耗时和浏览器侧失败信息，不能把所有刷新缓慢都归因于同一个原因。

### 代码缺陷与修复

- 多个唤醒事件可分别重建连接、中断并重发 HTTP 请求。现在合并重叠事件，共享一个进行中的 HTTP 探测；手动刷新保留已有连接。
- 遥测看门狗也会在约 15 秒后中断未完成的握手。现在使用独立的 30 秒首条消息等待期限，失败后按有上限的指数退避重试。
- 普通 WebSocket 失败不检查 HTTP 鉴权，只有 `4401/4403` 才检查。现在每次重试都探测快照接口，让浏览器无法解释的握手失败也能通过 Access 的 `401` 识别会话失效。
- 重连及看门狗恢复会强制查询历史数据。现在只有手动刷新和唤醒恢复刷新历史，连接重试只请求轻量快照。
- 没有新样本时服务端不发送消息，健康的空闲连接会被判断为失效。现在即使序列号不变，也至少每五秒发送重新生成的快照；配置的刷新间隔更长时遵循该间隔。
- Jobs 请求未使用共享超时与 Access AJAX 请求头。现在 Jobs 和设置请求统一使用共享请求函数，保留调用方请求头，并要求 Access 对过期 AJAX 会话返回 `401`。
- 快照渲染会覆盖连接状态，包括把已经连接的空集群重新标记为 connecting。现在由连接控制器维护传输状态，并单独表达节点健康情况。
- Lab 遇到 `401` 会自动刷新整页。现在卸载实时应用并显示 Reconnect 操作，停止后台重试和自动刷新循环；点击后进行顶层导航，让 Access 重新验证会话。

保留了已有的旧响应隔离和独立历史数据库读取逻辑。不需要数据库迁移、新增应用依赖或修改生产配置。

### 验证与部署边界

验证通过：230 项 Python 测试、15 项前端测试、两种前端构建、Ruff 和空白检查。确定性重放同一组唤醒事件时，修改前额外建立三条连接并触发三次 HTTP 恢复，修改后各一次。桌面明暗主题及 390px 手机预览均无页面横向溢出或 JavaScript 异常，模拟会话过期后停止重试且不自动跳转。实际浏览器断网再联网恢复及 18 秒空闲连接检查也已通过。

单元测试覆盖重叠唤醒事件、慢握手、不透明鉴权失败、重试退避、AJAX 会话过期、旧响应隔离及服务端空闲保活。浏览器验证使用不连接生产数据库的本地预览、模拟用户身份和模拟 Access 过期，不能替代真实用户通过 Cloudflare 登录后的端到端验证。

部署时需通过正常发布流程同时更新 manager 代码和 Lab 前端包；只构建普通前端不会更新 Lab 打包资源。部署后应通过公开域名验证正常刷新、断网恢复、休眠恢复、空闲集群与会话过期后的重新认证。生产重启仍需单独明确授权。

参考：[Access AJAX 会话处理](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/session-management/#ajax)、[Cloudflare WebSockets](https://developers.cloudflare.com/network/websockets/)。
