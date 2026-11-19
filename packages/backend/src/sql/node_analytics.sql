WITH aggregated AS (
  SELECT CAST(bucket_start / ?1 AS INTEGER) * ?1 AS target_start, gpu_uuid,
    SUM(avg_gpu_utilization * sample_count) / SUM(sample_count) AS avg_gpu,
    MAX(max_gpu_utilization) AS max_gpu,
    SUM(avg_memory_used_mb * sample_count) / SUM(sample_count) AS avg_memory,
    MAX(max_memory_used_mb) AS max_memory,
    SUM(avg_power_watts * sample_count) / SUM(sample_count) AS avg_power,
    MAX(max_power_watts) AS max_power,
    SUM(avg_temperature_c * sample_count) / SUM(sample_count) AS avg_temperature,
    MAX(max_temperature_c) AS max_temperature,
    SUM(sample_count) AS total
  FROM gpu_metric_rollups
  WHERE bucket_seconds = ?2 AND node_id = ?3 AND bucket_start BETWEEN ?4 AND ?5
  GROUP BY target_start, gpu_uuid
  HAVING total > 0
)
SELECT a.target_start, a.gpu_uuid, g.gpu_index, g.name, a.avg_gpu, a.max_gpu,
       a.avg_memory, a.max_memory, a.avg_power, a.max_power,
       a.avg_temperature, a.max_temperature, a.total
FROM aggregated a
LEFT JOIN gpus g ON g.gpu_id = ?3 || ':' || a.gpu_uuid
ORDER BY a.target_start
