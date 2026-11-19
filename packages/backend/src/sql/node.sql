INSERT INTO nodes (
  node_id, hostname, display_name, first_seen_at, last_seen_at, agent_version, status
)
VALUES (?, ?, NULL, ?, ?, ?, ?)
ON CONFLICT(node_id) DO UPDATE SET
  hostname=excluded.hostname,
  last_seen_at=excluded.last_seen_at,
  agent_version=excluded.agent_version,
  status=excluded.status
WHERE excluded.last_seen_at > nodes.last_seen_at
