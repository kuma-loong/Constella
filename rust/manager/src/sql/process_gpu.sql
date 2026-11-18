INSERT INTO process_gpu_usages (
  session_id, node_id, gpu_uuid, first_seen_at, last_seen_at,
  max_memory_mb, avg_memory_mb, last_memory_mb, sample_count
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
ON CONFLICT(session_id, gpu_uuid) DO UPDATE SET
  last_seen_at=excluded.last_seen_at,
  max_memory_mb=MAX(process_gpu_usages.max_memory_mb, excluded.last_memory_mb),
  avg_memory_mb=(
    (process_gpu_usages.avg_memory_mb * process_gpu_usages.sample_count)
    + excluded.last_memory_mb
  ) / (process_gpu_usages.sample_count + 1),
  last_memory_mb=excluded.last_memory_mb,
  sample_count=process_gpu_usages.sample_count + 1
