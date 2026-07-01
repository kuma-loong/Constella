# Constella 阶段 2.2 落地计划

本文覆盖阶段 2.2 的数据库写入收敛：原始 GPU 指标不再写入 SQLite，数据库继续作为可选旁路，只保存有长期价值的 rollup 和任务 session。

## 目标原则

数据库必须是可选组件，不能耦合进实时主链路。

```text
agent sample
  -> manager ClusterState / realtime memory state
  -> UI / WebSocket

  -> optional DB sink
       -> in-memory rollup accumulator
       -> gpu_metric_rollups
       -> process_sessions / process_gpu_usages
```

硬约束：

- DB 关闭时，agent ingest、ClusterState、UI、WebSocket 都必须正常工作。
- DB 慢、写失败、队列满时，不能反压实时监控链路。
- 原始 1s GPU samples 不写硬盘、不进 SQLite。
- 原始 sample 只服务实时内存状态和 DB sink 内部 rollup 聚合，完成后直接释放。
- 任务生命周期仍由 `process_sessions` 和 `process_gpu_usages` 长期记录。

## 数据保留策略

桶尺寸固定为：

```text
20s    bucket_seconds = 20
2m     bucket_seconds = 120
1h     bucket_seconds = 3600
```

保留周期：

```text
20s rollup: 保留 7 天
2m rollup:  保留 60 天
1h rollup:  保留 1 年
```

任务相关表：

```text
process_sessions: 长期保留
process_gpu_usages: 长期保留
nodes / gpus: 长期保留并 upsert 最新元信息
raw_snapshots: 默认关闭；如果开启，仍按短周期保留
```

不再长期保留：

```text
gpu_metric_samples
```

阶段 2.3 后，`gpu_metric_samples` 应进入废弃路径：

- 新部署不再写入。
- 历史库中已有数据可由迁移/维护命令 rollup 后清理。
- API 查询历史曲线应优先使用 `gpu_metric_rollups`，不再依赖 `gpu_metric_samples`。

## 写入模型

### DB disabled

```text
agent sample
  -> cluster_state.ingest_sample(...)
  -> /api/cluster/snapshot
  -> /ws/cluster
```

此模式下不创建 SQLite 连接，不创建 DB worker，不创建 rollup accumulator。

### DB enabled

```text
agent sample
  -> cluster_state.ingest_sample(...)
  -> accepted == true
  -> db_sink.submit_node_snapshot(runtime.snapshot)
  -> bounded queue
  -> DB worker
```

`submit_node_snapshot` 必须保持 non-blocking：

```text
queue.put_nowait(...)
queue full -> dropped_samples += 1 -> return false
```

即使 DB sample 被丢弃，也不能影响：

- agent ack
- ClusterState 最新状态
- 前端实时展示
- WebSocket 推送

## DB worker 职责

DB worker 收到 `NodeSnapshot` 后执行：

```text
1. upsert nodes
2. upsert gpus
3. upsert process_sessions
4. upsert process_gpu_usages
5. update in-memory 20s rollup accumulator
6. flush closed 20s buckets to gpu_metric_rollups
7. periodic rollup 20s -> 2m
8. periodic rollup 2m -> 1h
9. periodic prune expired rollups
10. optional prune raw_snapshots
```

不再执行：

```text
INSERT INTO gpu_metric_samples ...
```

## 内存 rollup accumulator

1s 原始 GPU sample 不需要保存完整列表。DB sink 只维护正在聚合的桶状态。

key：

```text
(bucket_seconds=20, bucket_start, node_id, gpu_uuid)
```

value：

```text
sum_gpu_utilization
max_gpu_utilization
sum_memory_used_mb
max_memory_used_mb
sum_power_watts
max_power_watts
sum_temperature_c
max_temperature_c
sample_count
```

每来一个 GPU sample：

```text
bucket_start = floor(sampled_at / 20) * 20
update accumulator[key]
```

flush 时写入：

```text
avg_gpu_utilization = sum_gpu_utilization / sample_count
max_gpu_utilization
avg_memory_used_mb = sum_memory_used_mb / sample_count
max_memory_used_mb
avg_power_watts = sum_power_watts / sample_count
max_power_watts
avg_temperature_c = sum_temperature_c / sample_count
max_temperature_c
sample_count
```

flush 成功后删除对应内存 bucket。

## Rollup 关闭桶规则

只处理已经结束并留出安全延迟的桶，避免当前桶仍在写入时反复变化。

建议 safety lag：

```text
20s rollup: 处理 end_time <= now - 20s 的桶
2m rollup:  处理 end_time <= now - 120s 的桶
1h rollup:  处理 end_time <= now - 3600s 的桶
```

示例：当前时间 `15:23:17`。

```text
20s: 只 flush <= 15:22:40 的桶
2m:  只聚合 <= 15:20:00 的桶
1h:  只聚合 <= 14:00:00 的桶
```

## Rollup 频率

推荐默认调度：

```text
20s rollup flush:
  每 10 秒执行一次
  来源：DB sink 内存 accumulator
  目标：gpu_metric_rollups(bucket_seconds = 20)

2m rollup:
  每 2 分钟执行一次
  来源：gpu_metric_rollups(bucket_seconds = 20)
  目标：gpu_metric_rollups(bucket_seconds = 120)

1h rollup:
  每 1 小时执行一次
  来源：gpu_metric_rollups(bucket_seconds = 120)
  目标：gpu_metric_rollups(bucket_seconds = 3600)
```

prune 调度：

```text
20s rollup prune:
  每 10 分钟执行一次
  删除 bucket_seconds = 20 且 bucket_start < now - 7d

2m rollup prune:
  每 1 小时执行一次
  删除 bucket_seconds = 120 且 bucket_start < now - 60d

1h rollup prune:
  每 1 天执行一次
  删除 bucket_seconds = 3600 且 bucket_start < now - 365d
```

## 2m 和 1h 聚合规则

`20s -> 2m`：

```text
每 6 个 20s bucket 聚合成 1 个 120s bucket
```

`2m -> 1h`：

```text
每 30 个 120s bucket 聚合成 1 个 3600s bucket
```

聚合字段规则：

```text
avg_gpu_utilization = weighted average by sample_count
max_gpu_utilization = max(child.max_gpu_utilization)

avg_memory_used_mb = weighted average by sample_count
max_memory_used_mb = max(child.max_memory_used_mb)

avg_power_watts = weighted average by sample_count
max_power_watts = max(child.max_power_watts)

avg_temperature_c = weighted average by sample_count
max_temperature_c = max(child.max_temperature_c)

sample_count = sum(child.sample_count)
```

使用 `sample_count` 加权，而不是简单平均子桶，避免缺样时产生偏差。

## 查询策略

历史图表不再依赖 `gpu_metric_samples`。

推荐按查询跨度选择数据源：

```text
0 - 7 天:
  gpu_metric_rollups bucket_seconds = 20

7 - 60 天:
  gpu_metric_rollups bucket_seconds = 120

60 天 - 1 年:
  gpu_metric_rollups bucket_seconds = 3600
```

实时 UI 的秒级短曲线仍来自内存 latest/history，不来自 SQLite。

如果页面需要跨越多个范围，可以按时间段拆分查询，或者选择能覆盖整个范围的最粗粒度。

## 表结构策略

`gpu_metric_rollups` 现有设计已经支持多粒度：

```text
PRIMARY KEY(bucket_start, bucket_seconds, node_id, gpu_uuid)
```

因此 20s、2m、1h 可以共用同一张表。

需要新增或调整的能力：

- 从内存 accumulator upsert 20s rollup。
- 从 rollups 表聚合出更粗 bucket。
- 按 `bucket_seconds` 和 retention 删除过期数据。
- 历史 API 支持查询 rollup 数据。

可以保留 `gpu_metric_samples` 表用于旧库兼容，但新写入路径应停止写这张表。

## 维护命令策略

维护脚本不再负责 `gpu_metric_samples -> rollup` 作为主路径，因为原始 samples 不落库。

维护命令应负责：

```text
close stale sessions
rollup 20s -> 2m
rollup 2m -> 1h
prune expired rollups
prune raw_snapshots
checkpoint WAL，可选
vacuum，可选且低频
```

建议命令：

```bash
uv run constella db maintain --path run/constella.db
```

或者保留细粒度命令：

```bash
uv run constella db rollup --path run/constella.db --from-bucket-seconds 20 --to-bucket-seconds 120
uv run constella db rollup --path run/constella.db --from-bucket-seconds 120 --to-bucket-seconds 3600
uv run constella db prune-rollups --path run/constella.db
uv run constella db close-sessions --path run/constella.db
```

`scripts/maintenance/db.sh` 应变成调用 `maintain` 或上述组合命令。

## 故障和重启语义

因为原始 samples 只在内存中，服务重启时可能丢失尚未 flush 的当前 20s bucket。

这是可接受 tradeoff：

- 最多损失一个安全窗口内的细粒度 rollup。
- 不影响实时服务恢复。
- 不影响已经写入的任务 session。
- 不影响已关闭 bucket 的历史曲线。

如需降低损失，可在 DB sink stop 时尝试 flush 已结束 bucket，但不要强行 flush 当前未关闭 bucket。

## 写入量预期

以 10 张 GPU 为例：

```text
旧模型 1s raw:
  10 GPU * 86400 秒 = 864000 行/天

新模型 20s:
  10 GPU * 4320 桶 = 43200 行/天

2m:
  10 GPU * 720 桶 = 7200 行/天

1h:
  10 GPU * 24 桶 = 240 行/天
```

长期数据库增长主要来自：

- 7 天 20s rollup
- 60 天 2m rollup
- 1 年 1h rollup
- 长期任务 session

相比保留 1s raw，写入量和存储量大幅下降。

## 迁移步骤

1. 保持 DB optional 边界不变。
2. 在 DB sink 内新增 20s in-memory accumulator。
3. 停止向 `gpu_metric_samples` 写入新数据。
4. 写入 20s rollup 到 `gpu_metric_rollups`。
5. 增加 `20s -> 2m`、`2m -> 1h` 聚合函数。
6. 增加 rollup prune 函数。
7. 调整历史 API，优先查询 `gpu_metric_rollups`。
8. 调整维护脚本和文档。
9. 对旧数据库执行一次性迁移：先从已有 `gpu_metric_samples` 生成 20s rollup，再清理旧 samples。

## 测试矩阵

DB disabled：

- agent sample 可以更新 ClusterState。
- `/api/cluster/snapshot` 正常返回。
- `/ws/cluster` 正常推送。
- 不创建 SQLite 文件或连接。

DB enabled：

- agent sample accepted 后进入 DB queue。
- `nodes` / `gpus` 正确 upsert。
- `process_sessions` / `process_gpu_usages` 正确 upsert。
- 不新增 `gpu_metric_samples` 行。
- 20s accumulator 在桶关闭后写入 `gpu_metric_rollups`。
- `20s -> 2m` 聚合使用 `sample_count` 加权。
- `2m -> 1h` 聚合使用 `sample_count` 加权。
- prune 删除过期 20s、2m、1h rollup。
- queue full 时 dropped_samples 增加，但实时状态不受影响。

历史查询：

- 7 天内使用 20s rollup。
- 7-60 天使用 2m rollup。
- 60 天-1 年使用 1h rollup。
- 无 DB 时返回 `enabled: false`。

故障场景：

- DB worker 抛错不影响 agent ingest。
- 服务重启最多丢失未 flush 的当前 20s bucket。
- 维护命令重复执行幂等。

## 验收标准

- 运行一段时间后，`gpu_metric_samples` 行数不再增长。
- `gpu_metric_rollups(bucket_seconds=20)` 按 GPU 约每 20 秒增长一行。
- `gpu_metric_rollups(bucket_seconds=120)` 按 GPU 约每 2 分钟增长一行。
- `gpu_metric_rollups(bucket_seconds=3600)` 按 GPU 约每小时增长一行。
- 任务运行超过 20s/2m/1h 保留层级时，`process_sessions` 仍能记录完整生命周期。
- 关闭 DB 后，实时 UI 和 agent 上报不受影响。
- 数据库写入量显著低于 1s raw 模型。
