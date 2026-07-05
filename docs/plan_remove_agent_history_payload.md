# 去除 Agent 全量短历史上报计划

## 实施状态

已落地。当前协议约定：

- agent `sample.snapshot` 不再包含 `history`。
- manager 在 `ClusterState.ingest_sample()` 接受当前采样点后，通过 `HistoryAccumulator` 生成每个节点的 120 点实时短历史。
- `/api/cluster/snapshot`、`/ws/cluster` 和前端使用的 `NodeSnapshot.history` 结构保持不变。
- 新旧 agent 不混跑；升级后需要重启 manager 和所有 local/remote agent。

## 目标

避免 agent 每次 sample 都携带完整 `history`，降低远程传输和大集群 fan-in 压力。

保持最小改动：

- agent 只上报当前采样点。
- manager 维护短历史。
- 本地 agent 和远程 agent 走同一条 ingest/history 路径。
- frontend 继续读取 `NodeSnapshot.history`，尽量不改前端。
- 不兼容旧 agent：所有 agent 会统一重启到新协议。

## 当前问题

当前链路：

```text
SnapshotCollector._publish()
  -> snapshot.history = 120 点短历史
agent_sample()
  -> snapshot.to_dict()
  -> 每次 WebSocket sample 都带完整 history
manager ingest
  -> node_snapshot_from_agent_sample() 解析 history
frontend
  -> node.history 画 sparkline
```

问题：

- 远程 agent 每次重复传历史数组。
- GPU 数越多，payload 放大越明显。
- 大集群下会增加 manager ingest、JSON parse 和网络压力。

## 方案

### 0. 保持本地/远程 agent 路径一致

本次改动不区分本地 agent 和远程 agent：

```text
local agent  -> WS /api/agents/ws -> manager ingest -> HistoryAccumulator
remote agent -> WS /api/agents/ws -> manager ingest -> HistoryAccumulator
```

要求：

- 本地 agent sample payload 不包含 `snapshot.history`。
- 远程 agent sample payload 不包含 `snapshot.history`。
- 两者都由 manager 侧生成 `/api/cluster/snapshot` 中的 `NodeSnapshot.history`。
- 不为 local agent 开共享内存或本地直连特例。

这样 profiling、部署和后续 gateway 演进都保持一致。

### 1. Agent sample 不发送 history

在 `agent_sample()` 里生成 payload 后删除 `snapshot["history"]`。

建议做一个小 helper，避免改 `Snapshot.to_dict()` 的通用语义：

```python
def snapshot_to_agent_payload(snapshot: Snapshot) -> dict[str, Any]:
    payload = snapshot.to_dict()
    payload.pop("history", None)
    return payload
```

然后 `agent_sample()` 使用这个 helper。

好处：

- `probe`、测试、单机内部转换仍可保留完整 `Snapshot.to_dict()`。
- 改动范围小。

### 2. Manager 内部增加轻量 HistoryAccumulator

不建议现在拆独立 gateway 进程。短历史维护很轻，真正问题是重复传输 history。

建议先做 manager 内部的 in-process gate：

```text
manager ingest
  -> parse current sample
  -> HistoryAccumulator.update(node_snapshot)
  -> ClusterState stores latest node snapshot
```

新增轻量类 `HistoryAccumulator`，职责只做短历史维护：

```python
class HistoryAccumulator:
    def __init__(self, history_size: int = 120): ...
    def update(self, snapshot: NodeSnapshot) -> None: ...
    def payload_for_node(self, snapshot: NodeSnapshot) -> dict[str, dict[str, list[float]]]: ...
```

复用当前 history 数据结构：

- `gpu`
- `memory`
- `power`
- `temperature`

在 `ingest_sample()` 成功解析出 `NodeSnapshot` 后，基于当前 `snapshot.gpus` 更新 history：

```python
for gpu in snapshot.gpus:
    history[gpu.gpu_id]["gpu"].append(gpu.utilization_gpu)
    history[gpu.gpu_id]["memory"].append(gpu.memory_percent)
    history[gpu.gpu_id]["power"].append(gpu.power_percent)
    history[gpu.gpu_id]["temperature"].append(gpu.temperature_c)
snapshot.history = history_payload_for_node(snapshot.gpus)
```

`history_size` 继续使用现有默认 `120`。

边界要求：

- `ClusterState` 可以持有 `HistoryAccumulator`。
- history 维护逻辑不要散落在 agent 或 app websocket handler 中。
- 未来如果需要独立 gateway，可以迁移 `HistoryAccumulator + ingest`，不改 agent payload schema。

### 3. 不兼容旧 agent，简化协议

所有 agent 都会重新启动，因此不保留旧 agent sample 中携带 `history` 的兼容逻辑。

要求：

- 新 agent sample payload 不包含 `snapshot.history`。
- manager 不再依赖 agent sample payload 中的 `history`。
- `node_snapshot_from_agent_sample()` 可以忽略 payload 里的 `history`，或者直接不解析该字段。
- manager 统一用 `HistoryAccumulator` 从当前点生成短历史。

这样实现更轻，不需要合并旧 history、不需要处理新旧协议混跑。

### 4. 前端不改

manager 输出的 `NodeSnapshot.history` 结构保持不变：

```text
gpu_id -> metric -> values[]
```

因此 frontend 继续使用：

```ts
node.history[gpu.gpu_id]
```

无需改 UI。

## 已改动文件

- `src/constella/agent.py`
  - 新增 `snapshot_to_agent_payload()`
  - `agent_sample()` 改用该 helper，保留 `Snapshot.to_dict()` 通用语义

- `src/constella/cluster.py`
  - 新增 `HistoryAccumulator`
  - `ClusterState.__init__()` 持有 `HistoryAccumulator`
  - `ingest_sample()` 解析 sample 后调用 accumulator 更新 history
  - 将 accumulator 输出写回 `NodeSnapshot.history`
  - `node_snapshot_from_agent_sample()` 不再解析 agent payload 中的 `history`

- `tests/test_agent.py`
  - 断言 agent sample payload 不包含 `snapshot.history`

- `tests/test_cluster.py`
  - 断言 manager 能从连续 sample 中生成 `NodeSnapshot.history`
  - 断言 agent sample 不带 history 时 manager 仍生成 history
  - 断言 local/remote 只是不同 `node_id`，history 路径一致

## 验收标准

- 新 agent WebSocket sample payload 中不再包含 `snapshot.history`。
- 本地 agent 和远程 agent sample payload 都不包含 `snapshot.history`。
- `/api/cluster/snapshot` 和 `/ws/cluster` 仍包含每个节点的 `history`。
- 前端 sparkline 正常显示。
- 单节点 payload 明显下降，尤其 8 GPU 节点。

## 风险

- manager 重启后短历史会清空；这是可接受的，因为短历史本来就是实时 UI 缓存。
- 多 agent 断线重连时，同一 `gpu_id` 的 history 会延续；如果 GPU UUID 不变，这是期望行为。
- 如果节点 GPU 集合变化，需要按当前 GPU 列表输出 history；不在当前节点里的 GPU history 可以暂时保留，后续再做清理。

## 后续可选优化

- 当节点数达到几百/上千，或跨机房网络成为瓶颈时，再把 `HistoryAccumulator + ingest` 抽成独立 gateway。
- 静态字段只在 `hello` 发一次：GPU name、uuid、driver、NVML version。
- process list 改成低频或 delta。
- manager -> frontend 拆 overview/detail subscription，避免大集群全量广播。
