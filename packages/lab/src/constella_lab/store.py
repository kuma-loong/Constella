from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

VALID_ROLES = frozenset({"viewer", "member", "admin"})
VALID_USER_STATUSES = frozenset({"active", "disabled", "pending_identity_review"})


class LabStoreError(RuntimeError):
    pass


class LastAdminError(LabStoreError):
    pass


class BindingConflictError(LabStoreError):
    pass


class LabStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def open(self) -> None:
        if self.connection is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        with connection:
            connection.executescript(
                """
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
                """
            )
            user_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(lab_users)")
            }
            if "onboarding_completed_at" not in user_columns:
                connection.execute(
                    "ALTER TABLE lab_users ADD COLUMN onboarding_completed_at REAL"
                )
                connection.execute(
                    """
                    UPDATE lab_users
                    SET onboarding_completed_at = (
                      SELECT MIN(valid_from)
                      FROM lab_account_bindings
                      WHERE user_id = lab_users.id
                    )
                    WHERE EXISTS (
                      SELECT 1
                      FROM lab_account_bindings
                      WHERE user_id = lab_users.id
                    )
                    """
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO lab_schema_migrations(version, applied_at)
                VALUES (2, CAST(strftime('%s', 'now') AS REAL))
                """
            )
        self.connection = connection
        os.chmod(self.path, 0o600)

    def close(self) -> None:
        with self._lock:
            if self.connection is not None:
                self.connection.close()
                self.connection = None

    def _con(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("Lab store is not open")
        return self.connection

    def resolve_identity(
        self,
        *,
        issuer: str,
        subject: str,
        email: str,
        bootstrap_admin_email: str,
        request_id: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = now if now is not None else time.time()
        normalized = normalize_email(email)
        with self._lock, self._con():
            con = self._con()
            row = con.execute(
                """
                SELECT * FROM lab_users
                WHERE access_issuer = ? AND access_subject = ?
                """,
                (issuer, subject),
            ).fetchone()
            if row is not None:
                if timestamp - float(row["last_login_at"]) >= 300 or row["email"] != email:
                    con.execute(
                        """
                        UPDATE lab_users
                        SET email = ?, email_normalized = ?, updated_at = ?, last_login_at = ?
                        WHERE id = ?
                        """,
                        (email, normalized, timestamp, timestamp, row["id"]),
                    )
                    row = con.execute(
                        "SELECT * FROM lab_users WHERE id = ?", (row["id"],)
                    ).fetchone()
                return dict(row)

            conflict = con.execute(
                "SELECT id FROM lab_users WHERE email_normalized = ? LIMIT 1",
                (normalized,),
            ).fetchone()
            admin_exists = con.execute(
                "SELECT 1 FROM lab_users WHERE role = 'admin' AND status = 'active' LIMIT 1"
            ).fetchone()
            is_bootstrap = (
                normalized == normalize_email(bootstrap_admin_email) and admin_exists is None
            )
            user_id = uuid.uuid4().hex
            role = "admin" if is_bootstrap else "member"
            status = "pending_identity_review" if conflict is not None else "active"
            con.execute(
                """
                INSERT INTO lab_users (
                  id, access_issuer, access_subject, email, email_normalized,
                  role, status, created_at, updated_at, last_login_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    issuer,
                    subject,
                    email,
                    normalized,
                    role,
                    status,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            self._audit_sql(
                con,
                actor_user_id=user_id,
                action="user.created",
                target_type="user",
                target_id=user_id,
                request_id=request_id,
                details={"role": role, "status": status},
                now=timestamp,
            )
            row = con.execute("SELECT * FROM lab_users WHERE id = ?", (user_id,)).fetchone()
            return dict(row)

    def user_with_bindings(self, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            user = self._con().execute(
                "SELECT * FROM lab_users WHERE id = ?", (user_id,)
            ).fetchone()
            if user is None:
                return None
            result = dict(user)
            result["bindings"] = self.list_bindings(user_id=user_id, active_only=False)
            return result

    def update_profile(self, user_id: str, display_name: str | None) -> dict[str, Any]:
        name = display_name.strip() if display_name else None
        if name and len(name) > 80:
            raise ValueError("display name is too long")
        with self._lock, self._con():
            self._con().execute(
                "UPDATE lab_users SET display_name = ?, updated_at = ? WHERE id = ?",
                (name, time.time(), user_id),
            )
        user = self.user_with_bindings(user_id)
        if user is None:
            raise LabStoreError("user not found")
        return user

    def list_bindings(
        self,
        *,
        user_id: str | None = None,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            clauses.append("b.user_id = ?")
            params.append(user_id)
        if active_only:
            clauses.append("b.valid_to IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._con().execute(
                f"""
                SELECT b.*, u.email AS user_email, u.display_name AS user_display_name
                FROM lab_account_bindings b
                JOIN lab_users u ON u.id = b.user_id
                {where}
                ORDER BY b.valid_from DESC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def can_bind(self, *, user_id: str, node_id: str, unix_uid: int) -> bool:
        with self._lock:
            conflict = self._con().execute(
                """
                SELECT 1 FROM lab_account_bindings
                WHERE valid_to IS NULL
                  AND ((user_id = ? AND node_id = ?) OR (node_id = ? AND unix_uid = ?))
                LIMIT 1
                """,
                (user_id, node_id, node_id, unix_uid),
            ).fetchone()
        return conflict is None

    def create_bindings(
        self,
        *,
        user_id: str,
        accounts: Iterable[dict[str, Any]],
        actor_user_id: str,
        request_id: str,
        assurance: str = "self_claimed",
        reason: str | None = None,
    ) -> list[dict[str, Any]]:
        now = time.time()
        created_ids: list[str] = []
        try:
            with self._lock, self._con():
                con = self._con()
                for account in accounts:
                    binding_id = uuid.uuid4().hex
                    con.execute(
                        """
                        INSERT INTO lab_account_bindings (
                          id, user_id, node_id, unix_uid, unix_gid, unix_username,
                          assurance, status, valid_from, created_by, reason
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                        """,
                        (
                            binding_id,
                            user_id,
                            account["node_id"],
                            account["uid"],
                            account.get("gid"),
                            account["canonical_username"],
                            assurance,
                            now,
                            actor_user_id,
                            reason,
                        ),
                    )
                    self._audit_sql(
                        con,
                        actor_user_id=actor_user_id,
                        action="binding.created",
                        target_type="binding",
                        target_id=binding_id,
                        request_id=request_id,
                        details={
                            "user_id": user_id,
                            "node_id": account["node_id"],
                            "unix_uid": account["uid"],
                            "assurance": assurance,
                            "reason": reason,
                        },
                        now=now,
                    )
                    created_ids.append(binding_id)
                completed = con.execute(
                    """
                    UPDATE lab_users
                    SET onboarding_completed_at = ?, updated_at = ?
                    WHERE id = ? AND onboarding_completed_at IS NULL
                    """,
                    (now, now, user_id),
                )
                if completed.rowcount:
                    self._audit_sql(
                        con,
                        actor_user_id=actor_user_id,
                        action="user.onboarding_completed",
                        target_type="user",
                        target_id=user_id,
                        request_id=request_id,
                        details={"binding_count": len(created_ids)},
                        now=now,
                    )
        except sqlite3.IntegrityError as exc:
            raise BindingConflictError("account is already bound") from exc
        return [
            binding
            for binding in self.list_bindings(user_id=user_id)
            if binding["id"] in created_ids
        ]

    def end_binding(
        self,
        binding_id: str,
        *,
        actor_user_id: str,
        owner_user_id: str | None,
        request_id: str,
        status: str = "revoked",
        reason: str | None = None,
    ) -> bool:
        now = time.time()
        clauses = ["id = ?", "valid_to IS NULL"]
        params: list[Any] = [binding_id]
        if owner_user_id is not None:
            clauses.append("user_id = ?")
            params.append(owner_user_id)
        with self._lock, self._con():
            con = self._con()
            row = con.execute(
                f"SELECT * FROM lab_account_bindings WHERE {' AND '.join(clauses)}",
                params,
            ).fetchone()
            if row is None:
                return False
            con.execute(
                """
                UPDATE lab_account_bindings
                SET valid_to = ?, ended_by = ?, status = ?, reason = COALESCE(?, reason)
                WHERE id = ?
                """,
                (now, actor_user_id, status, reason, binding_id),
            )
            self._audit_sql(
                con,
                actor_user_id=actor_user_id,
                action="binding.ended",
                target_type="binding",
                target_id=binding_id,
                request_id=request_id,
                details={"status": status, "reason": reason},
                now=now,
            )
        return True

    def list_users(self, query: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if query:
            where = "WHERE u.email_normalized LIKE ? OR u.display_name LIKE ?"
            needle = f"%{query.strip().lower()}%"
            params.extend((needle, needle))
        with self._lock:
            rows = self._con().execute(
                f"""
                SELECT u.*, COUNT(b.id) AS active_binding_count
                FROM lab_users u
                LEFT JOIN lab_account_bindings b
                  ON b.user_id = u.id AND b.valid_to IS NULL
                {where}
                GROUP BY u.id
                ORDER BY u.created_at ASC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def admin_update_user(
        self,
        user_id: str,
        *,
        actor_user_id: str,
        request_id: str,
        role: str | None = None,
        status: str | None = None,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        if role is not None and role not in VALID_ROLES:
            raise ValueError("invalid role")
        if status is not None and status not in VALID_USER_STATUSES:
            raise ValueError("invalid status")
        with self._lock, self._con():
            con = self._con()
            current = con.execute("SELECT * FROM lab_users WHERE id = ?", (user_id,)).fetchone()
            if current is None:
                raise LabStoreError("user not found")
            removes_active_admin = current["role"] == "admin" and current["status"] == "active" and (
                (role is not None and role != "admin")
                or (status is not None and status != "active")
            )
            if removes_active_admin:
                admin_count = con.execute(
                    "SELECT COUNT(*) FROM lab_users WHERE role = 'admin' AND status = 'active'"
                ).fetchone()[0]
                if admin_count <= 1:
                    raise LastAdminError("cannot remove the last active admin")
            next_role = role or current["role"]
            next_status = status or current["status"]
            next_name = display_name.strip() if display_name is not None else current["display_name"]
            con.execute(
                """
                UPDATE lab_users
                SET role = ?, status = ?, display_name = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_role, next_status, next_name or None, time.time(), user_id),
            )
            self._audit_sql(
                con,
                actor_user_id=actor_user_id,
                action="user.updated",
                target_type="user",
                target_id=user_id,
                request_id=request_id,
                details={"role": next_role, "status": next_status},
            )
            if next_status != "active":
                active_bindings = con.execute(
                    """
                    SELECT id FROM lab_account_bindings
                    WHERE user_id = ? AND valid_to IS NULL
                    """,
                    (user_id,),
                ).fetchall()
                con.execute(
                    """
                    UPDATE lab_account_bindings
                    SET valid_to = ?, ended_by = ?, status = 'revoked', reason = ?
                    WHERE user_id = ? AND valid_to IS NULL
                    """,
                    (time.time(), actor_user_id, "user disabled", user_id),
                )
                for binding in active_bindings:
                    self._audit_sql(
                        con,
                        actor_user_id=actor_user_id,
                        action="binding.ended",
                        target_type="binding",
                        target_id=binding["id"],
                        request_id=request_id,
                        details={"status": "revoked", "reason": "user disabled"},
                    )
        user = self.user_with_bindings(user_id)
        if user is None:
            raise LabStoreError("user not found")
        return user

    def migrate_identity(
        self,
        user_id: str,
        *,
        pending_user_id: str,
        actor_user_id: str,
        request_id: str,
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("reason is required")
        with self._lock, self._con():
            con = self._con()
            target = con.execute("SELECT * FROM lab_users WHERE id = ?", (user_id,)).fetchone()
            pending = con.execute(
                "SELECT * FROM lab_users WHERE id = ?", (pending_user_id,)
            ).fetchone()
            if target is None or pending is None:
                raise LabStoreError("user not found")
            if (
                pending["status"] != "pending_identity_review"
                or target["email_normalized"] != pending["email_normalized"]
            ):
                raise ValueError("identity migration candidates do not match")
            con.execute(
                """
                UPDATE lab_users
                SET access_issuer = ?, status = 'disabled', updated_at = ?
                WHERE id = ?
                """,
                (f"migrated:{pending_user_id}", time.time(), pending_user_id),
            )
            con.execute(
                """
                UPDATE lab_users
                SET access_issuer = ?, access_subject = ?, email = ?,
                    email_normalized = ?, updated_at = ?, last_login_at = ?
                WHERE id = ?
                """,
                (
                    pending["access_issuer"],
                    pending["access_subject"],
                    pending["email"],
                    pending["email_normalized"],
                    time.time(),
                    pending["last_login_at"],
                    user_id,
                ),
            )
            self._audit_sql(
                con,
                actor_user_id=actor_user_id,
                action="identity.migrated",
                target_type="user",
                target_id=user_id,
                request_id=request_id,
                details={"pending_user_id": pending_user_id, "reason": reason.strip()},
            )
        user = self.user_with_bindings(user_id)
        if user is None:
            raise LabStoreError("user not found")
        return user

    def verify_binding(
        self, binding_id: str, *, actor_user_id: str, request_id: str
    ) -> dict[str, Any] | None:
        with self._lock, self._con():
            con = self._con()
            row = con.execute(
                "SELECT * FROM lab_account_bindings WHERE id = ? AND valid_to IS NULL",
                (binding_id,),
            ).fetchone()
            if row is None:
                return None
            con.execute(
                "UPDATE lab_account_bindings SET assurance = 'admin_verified' WHERE id = ?",
                (binding_id,),
            )
            self._audit_sql(
                con,
                actor_user_id=actor_user_id,
                action="binding.verified",
                target_type="binding",
                target_id=binding_id,
                request_id=request_id,
                details={"assurance": "admin_verified"},
            )
            updated = con.execute(
                "SELECT * FROM lab_account_bindings WHERE id = ?", (binding_id,)
            ).fetchone()
        return dict(updated)

    def reassign_binding(
        self,
        *,
        user_id: str,
        account: dict[str, Any],
        actor_user_id: str,
        request_id: str,
        reason: str,
    ) -> dict[str, Any]:
        reason = reason.strip()
        if not reason:
            raise ValueError("reason is required")
        now = time.time()
        binding_id = uuid.uuid4().hex
        try:
            with self._lock, self._con():
                con = self._con()
                user = con.execute(
                    "SELECT status FROM lab_users WHERE id = ?", (user_id,)
                ).fetchone()
                if user is None or user["status"] != "active":
                    raise LabStoreError("target user is not active")
                replaced = con.execute(
                    """
                    SELECT id FROM lab_account_bindings
                    WHERE valid_to IS NULL AND (
                      (user_id = ? AND node_id = ?)
                      OR (node_id = ? AND unix_uid = ?)
                    )
                    """,
                    (user_id, account["node_id"], account["node_id"], account["uid"]),
                ).fetchall()
                for row in replaced:
                    con.execute(
                        """
                        UPDATE lab_account_bindings
                        SET valid_to = ?, ended_by = ?, status = 'reassigned', reason = ?
                        WHERE id = ?
                        """,
                        (now, actor_user_id, reason, row["id"]),
                    )
                    self._audit_sql(
                        con,
                        actor_user_id=actor_user_id,
                        action="binding.reassigned",
                        target_type="binding",
                        target_id=row["id"],
                        request_id=request_id,
                        details={"replacement_binding_id": binding_id, "reason": reason},
                        now=now,
                    )
                con.execute(
                    """
                    INSERT INTO lab_account_bindings (
                      id, user_id, node_id, unix_uid, unix_gid, unix_username,
                      assurance, status, valid_from, created_by, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, 'admin_verified', 'active', ?, ?, ?)
                    """,
                    (
                        binding_id,
                        user_id,
                        account["node_id"],
                        account["uid"],
                        account.get("gid"),
                        account["canonical_username"],
                        now,
                        actor_user_id,
                        reason,
                    ),
                )
                self._audit_sql(
                    con,
                    actor_user_id=actor_user_id,
                    action="binding.created",
                    target_type="binding",
                    target_id=binding_id,
                    request_id=request_id,
                    details={
                        "user_id": user_id,
                        "node_id": account["node_id"],
                        "unix_uid": account["uid"],
                        "assurance": "admin_verified",
                        "reason": reason,
                    },
                    now=now,
                )
                created = con.execute(
                    "SELECT * FROM lab_account_bindings WHERE id = ?", (binding_id,)
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise BindingConflictError("account is already bound") from exc
        return dict(created)

    def audit(
        self,
        *,
        actor_user_id: str | None,
        action: str,
        target_type: str,
        target_id: str | None,
        request_id: str,
        details: dict[str, Any],
    ) -> None:
        with self._lock, self._con():
            self._audit_sql(
                self._con(),
                actor_user_id=actor_user_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                request_id=request_id,
                details=details,
            )

    def list_audit_events(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._con().execute(
                """
                SELECT a.*, u.email AS actor_email
                FROM lab_audit_events a
                LEFT JOIN lab_users u ON u.id = a.actor_user_id
                ORDER BY a.id DESC LIMIT ? OFFSET ?
                """,
                (max(1, min(limit, 500)), max(0, offset)),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result

    def backup(self, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(target.parent, 0o700)
        with self._lock:
            destination = sqlite3.connect(target)
            try:
                self._con().backup(destination)
            finally:
                destination.close()
        os.chmod(target, 0o600)

    @staticmethod
    def _audit_sql(
        con: sqlite3.Connection,
        *,
        actor_user_id: str | None,
        action: str,
        target_type: str,
        target_id: str | None,
        request_id: str,
        details: dict[str, Any],
        now: float | None = None,
    ) -> None:
        con.execute(
            """
            INSERT INTO lab_audit_events (
              occurred_at, actor_user_id, action, target_type, target_id,
              request_id, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now if now is not None else time.time(),
                actor_user_id,
                action,
                target_type,
                target_id,
                request_id,
                json.dumps(details, ensure_ascii=False, separators=(",", ":")),
            ),
        )


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized or len(normalized) > 320:
        raise ValueError("invalid email claim")
    return normalized
