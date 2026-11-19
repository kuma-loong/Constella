INSERT INTO process_sessions (
  session_id, node_id, pid, ppid, process_start_time, parent_start_time,
  user, user_uid, task_name, process_name, exe, cmdline_hash, cmdline_text,
  first_seen_at, last_seen_at, duration_seconds, status, sample_count
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, 'running', 1)
ON CONFLICT(session_id) DO UPDATE SET
  ppid=COALESCE(excluded.ppid, process_sessions.ppid),
  parent_start_time=COALESCE(
    excluded.parent_start_time,
    process_sessions.parent_start_time
  ),
  user=COALESCE(excluded.user, process_sessions.user),
  user_uid=COALESCE(excluded.user_uid, process_sessions.user_uid),
  task_name=excluded.task_name,
  process_name=excluded.process_name,
  exe=COALESCE(excluded.exe, process_sessions.exe),
  cmdline_hash=COALESCE(excluded.cmdline_hash, process_sessions.cmdline_hash),
  cmdline_text=COALESCE(excluded.cmdline_text, process_sessions.cmdline_text),
  last_seen_at=excluded.last_seen_at,
  duration_seconds=excluded.last_seen_at - process_sessions.first_seen_at,
  status='running',
  sample_count=process_sessions.sample_count + 1
