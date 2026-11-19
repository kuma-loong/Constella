INSERT INTO gpu_metric_rollups (
  bucket_start, bucket_seconds, node_id, gpu_uuid,
  avg_gpu_utilization, max_gpu_utilization,
  avg_memory_used_mb, max_memory_used_mb,
  avg_power_watts, max_power_watts,
  avg_temperature_c, max_temperature_c, sample_count
)
VALUES (?1, 20, ?2, ?3, ?4, ?4, ?5, ?5, ?6, ?6, ?7, ?7, 1)
ON CONFLICT(bucket_start, bucket_seconds, node_id, gpu_uuid) DO UPDATE SET
  avg_gpu_utilization = avg_gpu_utilization + (?4 - avg_gpu_utilization) / (sample_count + 1),
  max_gpu_utilization = MAX(max_gpu_utilization, ?4),
  avg_memory_used_mb = avg_memory_used_mb + (?5 - avg_memory_used_mb) / (sample_count + 1),
  max_memory_used_mb = MAX(max_memory_used_mb, ?5),
  avg_power_watts = avg_power_watts + (?6 - avg_power_watts) / (sample_count + 1),
  max_power_watts = MAX(max_power_watts, ?6),
  avg_temperature_c = avg_temperature_c + (?7 - avg_temperature_c) / (sample_count + 1),
  max_temperature_c = MAX(max_temperature_c, ?7),
  sample_count = sample_count + 1
