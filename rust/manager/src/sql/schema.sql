CREATE TABLE IF NOT EXISTS nodes (
  node_id TEXT PRIMARY KEY,
  hostname TEXT NOT NULL,
  display_name TEXT,
  first_seen_at REAL NOT NULL,
  last_seen_at REAL NOT NULL,
  agent_version TEXT,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gpus (
  gpu_id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  uuid TEXT NOT NULL,
  gpu_index INTEGER NOT NULL,
  pci_bus_id TEXT,
  name TEXT NOT NULL,
  device_type TEXT NOT NULL DEFAULT 'nvidia',
  card_id TEXT,
  die_id INTEGER,
  memory_total_mb INTEGER NOT NULL,
  first_seen_at REAL NOT NULL,
  last_seen_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS gpu_metric_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sampled_at REAL NOT NULL,
  node_id TEXT NOT NULL,
  gpu_uuid TEXT NOT NULL,
  utilization_gpu REAL NOT NULL,
  utilization_mem REAL NOT NULL,
  memory_used_mb INTEGER NOT NULL,
  memory_total_mb INTEGER NOT NULL,
  power_watts REAL NOT NULL,
  power_limit_watts REAL NOT NULL,
  temperature_c INTEGER NOT NULL,
  sample_count INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_gpu_metric_samples_lookup
  ON gpu_metric_samples(node_id, gpu_uuid, sampled_at);

CREATE TABLE IF NOT EXISTS gpu_metric_rollups (
  bucket_start REAL NOT NULL,
  bucket_seconds INTEGER NOT NULL,
  node_id TEXT NOT NULL,
  gpu_uuid TEXT NOT NULL,
  avg_gpu_utilization REAL NOT NULL,
  max_gpu_utilization REAL NOT NULL,
  avg_memory_used_mb REAL NOT NULL,
  max_memory_used_mb INTEGER NOT NULL,
  avg_power_watts REAL NOT NULL,
  max_power_watts REAL NOT NULL,
  avg_temperature_c REAL NOT NULL,
  max_temperature_c INTEGER NOT NULL,
  sample_count INTEGER NOT NULL,
  PRIMARY KEY(bucket_start, bucket_seconds, node_id, gpu_uuid)
);

CREATE INDEX IF NOT EXISTS idx_gpu_metric_rollups_node_bucket_time
  ON gpu_metric_rollups(node_id, bucket_seconds, bucket_start);

CREATE TABLE IF NOT EXISTS process_sessions (
  session_id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  pid INTEGER NOT NULL,
  ppid INTEGER,
  process_start_time REAL,
  parent_start_time REAL,
  user TEXT,
  user_uid INTEGER,
  task_name TEXT NOT NULL,
  process_name TEXT NOT NULL,
  exe TEXT,
  cmdline_hash TEXT,
  cmdline_text TEXT,
  first_seen_at REAL NOT NULL,
  last_seen_at REAL NOT NULL,
  duration_seconds REAL NOT NULL,
  status TEXT NOT NULL,
  sample_count INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_process_sessions_user_last_seen
  ON process_sessions(user, last_seen_at);

CREATE TABLE IF NOT EXISTS process_gpu_usages (
  session_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  gpu_uuid TEXT NOT NULL,
  first_seen_at REAL NOT NULL,
  last_seen_at REAL NOT NULL,
  max_memory_mb INTEGER NOT NULL,
  avg_memory_mb REAL NOT NULL,
  last_memory_mb INTEGER NOT NULL,
  sample_count INTEGER NOT NULL,
  PRIMARY KEY(session_id, gpu_uuid)
);

CREATE INDEX IF NOT EXISTS idx_process_gpu_usages_node_window
  ON process_gpu_usages(node_id, first_seen_at, last_seen_at);

CREATE TABLE IF NOT EXISTS raw_snapshots (
  sampled_at REAL NOT NULL,
  node_id TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
