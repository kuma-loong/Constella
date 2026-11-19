CREATE TABLE IF NOT EXISTS lab_schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS lab_users (
  id TEXT PRIMARY KEY,
  access_issuer TEXT NOT NULL,
  access_subject TEXT NOT NULL,
  email TEXT NOT NULL,
  email_normalized TEXT NOT NULL,
  display_name TEXT,
  role TEXT NOT NULL CHECK(role IN ('viewer', 'member', 'admin')),
  status TEXT NOT NULL CHECK(
    status IN ('active', 'disabled', 'pending_identity_review')
  ),
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  last_login_at REAL NOT NULL,
  onboarding_completed_at REAL,
  UNIQUE(access_issuer, access_subject)
);

CREATE INDEX IF NOT EXISTS idx_lab_users_email
  ON lab_users(email_normalized);

CREATE TABLE IF NOT EXISTS lab_account_bindings (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES lab_users(id),
  node_id TEXT NOT NULL,
  unix_uid INTEGER NOT NULL,
  unix_gid INTEGER,
  unix_username TEXT NOT NULL,
  assurance TEXT NOT NULL CHECK(
    assurance IN ('self_claimed', 'admin_verified', 'node_verified')
  ),
  status TEXT NOT NULL CHECK(status IN ('active', 'revoked', 'reassigned')),
  valid_from REAL NOT NULL,
  valid_to REAL,
  created_by TEXT NOT NULL REFERENCES lab_users(id),
  ended_by TEXT REFERENCES lab_users(id),
  reason TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_lab_binding_user_node_active
  ON lab_account_bindings(user_id, node_id)
  WHERE valid_to IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_lab_binding_node_uid_active
  ON lab_account_bindings(node_id, unix_uid)
  WHERE valid_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_lab_bindings_history
  ON lab_account_bindings(node_id, unix_uid, valid_from, valid_to);

CREATE TABLE IF NOT EXISTS lab_audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at REAL NOT NULL,
  actor_user_id TEXT REFERENCES lab_users(id),
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT,
  request_id TEXT NOT NULL,
  details_json TEXT NOT NULL
);

INSERT OR IGNORE INTO lab_schema_migrations(version, applied_at)
VALUES (1, CAST(strftime('%s', 'now') AS REAL));
