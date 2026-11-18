use super::access::Identity;
use crate::{Error, history};
use constella_protocol::unix_time;
use rusqlite::types::Value as SqlValue;
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::Serialize;
use serde_json::{Value, json};
use std::path::Path;
use std::sync::{Arc, Mutex};
use tokio::sync::Semaphore;

pub struct Store {
    conn: Arc<Mutex<Connection>>,
    slots: Arc<Semaphore>,
}

#[derive(Clone)]
pub struct User {
    pub id: String,
    pub role: String,
    pub status: String,
}

impl Store {
    pub fn open(path: &Path) -> Result<Self, Error> {
        Ok(Self {
            conn: Arc::new(Mutex::new(super::schema::open(path)?)),
            slots: Arc::new(Semaphore::new(16)),
        })
    }

    pub async fn run<T: Send + 'static>(
        &self,
        task: impl FnOnce(&mut Connection) -> rusqlite::Result<T> + Send + 'static,
    ) -> Result<T, Error> {
        let permit = self.slots.clone().try_acquire_owned()?;
        let conn = self.conn.clone();
        Ok(tokio::task::spawn_blocking(move || {
            let _permit = permit;
            task(&mut conn.lock().unwrap())
        })
        .await??)
    }
}

pub fn normalize_email(email: &str) -> Option<String> {
    let normalized = email.trim().to_lowercase();
    (normalized.contains('@') && normalized.chars().count() <= 320).then_some(normalized)
}

pub fn user_by_id(conn: &Connection, id: &str) -> rusqlite::Result<Option<User>> {
    conn.query_row(
        "SELECT id, role, status FROM lab_users WHERE id = ?",
        [id],
        read_user,
    )
    .optional()
}

fn read_user(row: &rusqlite::Row<'_>) -> rusqlite::Result<User> {
    Ok(User {
        id: row.get(0)?,
        role: row.get(1)?,
        status: row.get(2)?,
    })
}

pub fn resolve(
    conn: &mut Connection,
    identity: &Identity,
    bootstrap: &str,
    request: &str,
) -> rusqlite::Result<User> {
    let now = unix_time();
    let normalized = normalize_email(&identity.email).expect("validated email claim");
    let existing = |db: &Connection| {
        db.query_row(
            "SELECT id,role,status,email,last_login_at FROM lab_users WHERE access_issuer=? AND access_subject=?",
            params![identity.issuer, identity.subject],
            |row| Ok((read_user(row)?, row.get::<_, String>(3)?, row.get::<_, f64>(4)?)),
        ).optional()
    };
    if let Some((user, email, last_login)) = existing(conn)?
        && email == identity.email
        && now - last_login < 300.0
    {
        return Ok(user);
    }
    let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let user = if let Some((user, _, _)) = existing(&tx)? {
        tx.execute("UPDATE lab_users SET email=?,email_normalized=?,updated_at=?3,last_login_at=?3 WHERE id=?4",
            params![identity.email, normalized, now, user.id])?;
        user
    } else {
        let conflict = tx.query_row(
            "SELECT EXISTS(SELECT 1 FROM lab_users WHERE email_normalized=?)",
            [&normalized],
            |row| row.get::<_, bool>(0),
        )?;
        let bootstrap = !conflict
            && normalized == bootstrap
            && tx.query_row(
                "SELECT NOT EXISTS(SELECT 1 FROM lab_users WHERE role='admin' AND status='active')",
                [],
                |row| row.get::<_, bool>(0),
            )?;
        let role = if bootstrap { "admin" } else { "member" };
        let status = if conflict {
            "pending_identity_review"
        } else {
            "active"
        };
        let id = uuid::Uuid::new_v4().simple().to_string();
        tx.execute("INSERT INTO lab_users (id,access_issuer,access_subject,email,email_normalized,role,status,
            created_at,updated_at,last_login_at) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?8,?8)",
            params![id, identity.issuer, identity.subject, identity.email, normalized, role, status, now])?;
        audit(
            &tx,
            &id,
            request,
            "user.created",
            "user",
            &id,
            json!({"role": role, "status": status}),
        )?;
        User {
            id,
            role: role.into(),
            status: status.into(),
        }
    };
    tx.commit()?;
    Ok(user)
}

pub fn bindings(
    conn: &Connection,
    id: Option<&str>,
    active_only: bool,
) -> rusqlite::Result<Vec<Value>> {
    let filters = id.map_or_else(Vec::new, |id| {
        vec![("b.user_id=?", SqlValue::Text(id.to_owned()))]
    });
    if active_only {
        return history::fetch(
            conn,
            "SELECT b.*,u.email AS user_email,u.display_name AS user_display_name
             FROM lab_account_bindings b JOIN lab_users u ON u.id=b.user_id
             AND b.valid_to IS NULL",
            filters,
            "ORDER BY b.valid_from DESC",
            [],
        );
    }
    history::fetch(
        conn,
        "SELECT b.*,u.email AS user_email,u.display_name AS user_display_name
        FROM lab_account_bindings b JOIN lab_users u ON u.id=b.user_id",
        filters,
        "ORDER BY b.valid_from DESC",
        [],
    )
}

pub fn user_payload(conn: &Connection, id: &str) -> rusqlite::Result<Option<Value>> {
    let mut values = history::fetch(
        conn,
        "SELECT * FROM lab_users",
        vec![("id=?", SqlValue::Text(id.to_owned()))],
        "",
        [],
    )?;
    if let Some(mut user) = values.pop() {
        user["bindings"] = json!(bindings(conn, Some(id), false)?);
        Ok(Some(user))
    } else {
        Ok(None)
    }
}

pub fn audit(
    conn: &Connection,
    actor: &str,
    request: &str,
    action: &str,
    kind: &str,
    id: &str,
    detail: impl Serialize,
) -> rusqlite::Result<()> {
    let text = serde_json::to_string(&detail)
        .map_err(|error| rusqlite::Error::ToSqlConversionFailure(Box::new(error)))?;
    conn.execute("INSERT INTO lab_audit_events
        (occurred_at,actor_user_id,action,target_type,target_id,request_id,details_json) VALUES (?,?,?,?,?,?,?)",
        params![unix_time(), actor, action, kind, id, request, text])?;
    Ok(())
}
