# Lab agent controls, manager shutdown, and account connections

## Design goals

- Let Lab administrators restart an online Constella agent, including the agent on
  the manager host. Restarting must preserve GPU workloads, manager availability,
  launch arguments, credentials, PID files, and supervisor ownership.
- Make manager shutdown complete when a high-resolution stream has no data, while
  retaining WebSocket cleanup and the telemetry database drain/close lifecycle.
- Give existing members a visible way to connect Linux accounts on new nodes,
  reusing UID lookup, uniqueness checks, atomic creation, and historical bindings.
- Keep existing agents and database schemas compatible. Add no dependencies and
  keep the public core free of Lab administrator APIs.

## Investigation and implementation plan

1. Record production process identities, ports, and database paths. Develop on
   `codex/lab-node-control-shutdown-bindings`; run previews only on isolated ports
   with state, tokens, logs, databases, and frontend output under
   `run/preview-lab-control/`. Never send restart requests to production agents.
2. Reproduce the shutdown failure with a real isolated Lab/Uvicorn process and an
   idle `/api/highres/stream` connection before changing the implementation.
3. Fix disconnect handling, add a bounded Lab CLI shutdown timeout, and verify
   application shutdown actually runs with pending telemetry writes.
4. Extend the existing authenticated manager/agent RPC with capability-gated
   restart messages and a per-process instance identifier. Acknowledge the request,
   unwind sender/receiver tasks, stop the collector, wait for worker threads, close
   the event loop, then re-execute the same Python command with `os.execv`.
5. Add an admin-only node list and restart API with same-origin CSRF checks, a
   60-second per-node cooldown, and durable requested/accepted/rejected/unconfirmed
   audit events. Show reconnect confirmation only after the instance ID changes.
   An acknowledgement alone is not proof that the agent restarted.
6. Add account-connection links to Profile and the user menu, linking directly to
   the existing settings form. Refresh node capabilities on entry and on demand;
   remove unavailable selections, invalidate old previews on refresh, freeze edits
   during lookup/creation, and clear selections after a successful connection.
7. Validate RPC correlation, access control, legacy/offline agents, cooldown,
   missing acknowledgements, actual re-exec/reconnect, binding on new nodes,
   shutdown/draining, core and Lab builds, Python checks, and desktop light/dark
   plus narrow mobile layouts. Recheck production process identities and paths.

## Confirmed shutdown cause

The high-resolution stream awaited `queue.get()` and never received WebSocket
disconnect messages. With no new samples, closing the transport left the ASGI task
blocked. Uvicorn waits for ASGI tasks **before** invoking application lifespan
shutdown, so database draining and extension cleanup could not begin. The Lab CLI
also omitted `timeout_graceful_shutdown`, leaving that wait unbounded.

An isolated reproduction with a single idle stream remained at “Waiting for
background tasks to complete” after SIGTERM. The fix races queue messages against
a disconnect receiver and cancels/joins both tasks before unsubscribing. The Lab
CLI now explicitly enables lifespan and defaults `--graceful-timeout` to 10 seconds,
matching the core CLI. Collector shutdown also waits for any in-flight sample
before closing driver handles, which is necessary for safe agent restarts.

## API and rollout

- `GET /api/lab/admin/nodes`: current connectivity, agent version, restart
  capability, manager-host label, and agent instance ID.
- `POST /api/lab/admin/nodes/{node_id}/restart`: `202` means acknowledged;
  `409` covers offline/legacy/unconfirmed disconnect, `429` cooldown, `504`
  unconfirmed timeout, and `502` rejection or RPC failure. Cooldown is local to the
  running manager process. Requests are not automatically retried.
- Agent capability: `agent_restart_v1`; messages:
  `agent_restart_request` / `agent_restart_response`. RPC correlation includes
  node ID, request ID, response type, and the current connection identity.
- Older agents remain connected and continue collecting; the UI disables restart
  until they are upgraded. A manager upgrade cannot add capability to a running
  legacy agent. Upgrade each agent through its existing deployment mechanism.
- Agent execution is restarted using the current executable, original Python
  arguments and inherited environment. PID and supervisor ownership are retained;
  runtime manager settings are resent on reconnect. No SSH, shell execution,
  host reboot, or manager restart is involved in the control API.
- Code and isolated preview validation can be completed without changing the
  production services. Activating the updated manager/agents in production needs
  a separately authorized rollout because it requires restarting those services.

## Verification results (2026-10-24)

- `uv run pytest -q`: 490 passed, including real manager shutdown with an idle
  stream and queued telemetry, real agent re-exec via both console and module
  entry points, PID preservation, restart/send races, RPC correlation, permission,
  CSRF, cooldown, legacy agent, timeout audit, and existing-member binding tests.
- `npm --prefix frontend test`: 18 passed. Core and isolated Lab production builds,
  `uv run ruff check .`, and `git diff --check` passed.
- Playwright exercised the isolated Lab API and frontend in desktop light, desktop
  dark, and 390px mobile layouts. Restart cancellation, reconnect confirmation,
  cooldown, legacy/offline disablement, direct account links, invalidating previews
  on refresh, and successful additional bindings passed. No horizontal page
  overflow was detected; mobile binding fields and actions stack vertically.
- The original idle-stream SIGTERM reproduction blocked beyond 2.5 seconds until
  forced; after the fix the same process completed application shutdown in about
  0.2 seconds without a forced cancellation.
- Local evidence, screenshots, preview-only identities/tokens and databases are
  kept under `run/preview-lab-control/` and excluded from Git. Production process
  identities, listener ports and configured database paths are checked against
  `production-baseline.json`; production services are not restarted or reconfigured.

## 设计目标

- Lab 管理员可重启在线 Constella agent，包括 manager 本机的 agent；保留 GPU
  任务、manager 可用性、启动参数、凭证、PID 文件和进程托管关系。
- 高频流没有采样时 manager 仍可正常关闭，执行 WebSocket 清理和遥测数据库排空、关闭。
- 已有成员可以直接绑定新节点上的 Linux 用户，复用 UID 查询、唯一性约束、原子写入和历史绑定。
- 兼容既有 agent 和数据库结构，不增加依赖；Lab 管理接口不进入公开核心版。

## 调查与实施计划

1. 记录生产进程身份、端口和数据库路径，在专用分支开发。预览使用隔离端口，状态、令牌、
   日志、数据库和前端产物均位于 `run/preview-lab-control/`，不向生产 agent 发送重启命令。
2. 修改前启动真实隔离 Lab/Uvicorn 进程，通过空闲高频流连接复现关闭问题。
3. 修复断连检测，补充 Lab CLI 关闭超时，验证遥测仍有待写数据时也执行应用关闭流程。
4. 扩展已有认证 RPC，以能力声明控制重启，提供每进程实例标识。先确认请求，再清理收发任务、
   停止采集、等待工作线程、关闭事件循环，最后以相同 Python 命令执行 `os.execv`。
5. 增加管理员节点列表和重启 API，复用同源 CSRF 校验，设置每节点 60 秒冷却，持久记录
   请求、接受、拒绝和结果未确认的审计事件。仅实例标识变化后显示已重连，接受请求不等于重启完成。
6. 在个人页和用户菜单增加直达设置表单的绑定入口。进入和手动刷新时获取节点能力，移除不可用
   选择，刷新时作废旧预检；查询和提交时冻结编辑，绑定成功后清空选择。
7. 验证 RPC 关联、权限、旧版与离线节点、冷却、无应答、真实进程重启与重连、新节点绑定、
   关闭排空、两版构建和 Python 检查，以及桌面明暗主题和窄屏；最后复核生产进程身份及路径。

## 已确认的关闭根因

高频流只等待 `queue.get()`，不接收 WebSocket 断连消息；没有新采样时，传输连接关闭后
ASGI 任务仍被阻塞。Uvicorn 先等待 ASGI 任务，再触发应用 lifespan 关闭，因此数据库排空
和扩展清理无法开始。Lab CLI 还缺少 `timeout_graceful_shutdown`，导致等待没有上限。

隔离复现实验只保留一个空闲流，SIGTERM 后仍停在等待后台任务。修复让队列消息和断连接收
并行等待，结束时取消并回收任务，再解除订阅。Lab CLI 显式启用 lifespan，默认关闭超时
10 秒，与核心版一致。采集器关闭还会等待正在执行的采样，再释放驱动句柄，保障 agent 重启安全。

## 接口与上线边界

- 管理员节点查询提供连接、版本、重启能力、manager 本机标记和 agent 实例标识。
- 重启接口的 `202` 表示已应答；`409` 表示离线、旧版或断连后结果未确认；`429` 表示冷却；
  `504` 表示超时后结果未确认；`502` 表示拒绝或 RPC 失败。冷却仅在当前 manager 进程内有效，
  请求不自动重试。
- 能力名为 `agent_restart_v1`，使用 `agent_restart_request` / `agent_restart_response`。
  RPC 同时校验节点、请求 ID、应答类型和当前连接身份。
- 旧 agent 持续采集，升级前禁用重启按钮。更新 manager 不会使运行中的旧 agent 自动获得能力，
  需要通过原有部署方式逐节点升级。
- agent 使用当前解释器、原 Python 参数和继承的环境重新执行，保留 PID 和托管关系，
  重连后重新接收 manager 运行参数。控制 API 不使用 SSH、shell、主机重启或 manager 重启。
- 本次可在不改变生产服务的情况下完成代码和隔离验证；生产激活需要重启相关服务，应单独授权上线。

## 验收结果（2026-10-24）

- `uv run pytest -q`：490 项通过，覆盖空闲流与待写遥测条件下的真实 manager 退出、
  console 和 module 两种入口的真实 agent 重新执行、PID 保持、重启与发送竞态、RPC
  关联、权限、CSRF、冷却、旧 agent、超时审计和已有成员追加绑定。
- 前端测试 18 项通过；核心版和隔离 Lab 版生产构建、Ruff 和 Git 差异空白检查均通过。
- Playwright 使用隔离 Lab 接口与前端检查桌面明暗主题和 390px 手机布局，重启取消、
  重连确认、冷却、旧版与离线禁用、绑定直达入口、刷新作废预检和追加绑定均通过。
  未检测到页面横向溢出，手机端绑定字段及操作纵向排列。
- 原始空闲流 SIGTERM 实验超过 2.5 秒仍阻塞，需强制终止；修复后相同实验约 0.2 秒
  完成应用关闭，无需强制取消。
- 本地验证记录、截图、预览专用身份与令牌、数据库均保留于忽略目录
  `run/preview-lab-control/`。对照 `production-baseline.json` 检查生产进程身份、
  监听端口和数据库配置路径，不重启或修改生产服务配置。
