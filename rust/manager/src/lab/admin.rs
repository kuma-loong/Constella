use super::api::{Reply, database_error, failure, lab};
use super::{Session, store};
use crate::App;
use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::{Extension, Json};
use constella_protocol::unix_time;
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::Arc;

#[derive(Deserialize)]
pub struct Update {
    role: Option<String>,
    status: Option<String>,
    display_name: Option<String>,
}

enum Outcome {
    Updated(Value),
    Invalid,
    Forbidden,
    LastAdmin,
}

fn response(session: &Session, output: Outcome, invalid_code: &str) -> Reply {
    match output {
        Outcome::Updated(user) => Ok(Json(json!({"user": user, "request_id": session.request}))),
        Outcome::Invalid => Err(failure(
            session,
            StatusCode::UNPROCESSABLE_ENTITY,
            invalid_code,
        )),
        Outcome::Forbidden => Err(failure(session, StatusCode::FORBIDDEN, "admin_required")),
        Outcome::LastAdmin => Err(failure(session, StatusCode::CONFLICT, "last_active_admin")),
    }
}

fn is_admin(conn: &Connection, id: &str) -> rusqlite::Result<bool> {
    Ok(store::user_by_id(conn, id)?
        .is_some_and(|user| user.role == "admin" && user.status == "active"))
}

pub async fn update_user(
    State(app): State<Arc<App>>,
    Extension(session): Extension<Session>,
    Path(target): Path<String>,
    Json(input): Json<Update>,
) -> Reply {
    if input
        .role
        .as_deref()
        .is_some_and(|role| !matches!(role, "viewer" | "member" | "admin"))
        || input.status.as_deref().is_some_and(|status| {
            !matches!(status, "active" | "disabled" | "pending_identity_review")
        })
        || input
            .display_name
            .as_ref()
            .is_some_and(|name| name.chars().count() > 80)
    {
        return response(&session, Outcome::Invalid, "invalid_user_update");
    }
    let actor = session.user.id.clone();
    let request = session.request.clone();
    let outcome = lab(&app)
        .store
        .run(move |conn| update(conn, &actor, &request, &target, input))
        .await
        .map_err(|error| database_error(&session, error))?;
    response(&session, outcome, "invalid_user_update")
}

fn update(
    conn: &mut Connection,
    actor: &str,
    request: &str,
    target: &str,
    input: Update,
) -> rusqlite::Result<Outcome> {
    let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
    if !is_admin(&tx, actor)? {
        return Ok(Outcome::Forbidden);
    }
    let Some(current) = store::user_by_id(&tx, target)? else {
        return Ok(Outcome::Invalid);
    };
    let role = input.role.as_deref().unwrap_or(&current.role);
    let status = input.status.as_deref().unwrap_or(&current.status);
    if current.role == "admin"
        && current.status == "active"
        && (role != "admin" || status != "active")
        && tx.query_row(
            "SELECT COUNT(*) FROM lab_users WHERE role='admin' AND status='active'",
            [],
            |row| row.get::<_, i64>(0),
        )? <= 1
    {
        return Ok(Outcome::LastAdmin);
    }
    let now = unix_time();
    tx.execute(
        "UPDATE lab_users SET role=?,status=?,display_name=CASE WHEN ?3 IS NULL
        THEN display_name ELSE NULLIF(?3,'') END,updated_at=?4 WHERE id=?5",
        params![
            role,
            status,
            input.display_name.as_deref().map(str::trim),
            now,
            target
        ],
    )?;
    store::audit(
        &tx,
        actor,
        request,
        "user.updated",
        "user",
        target,
        json!({"role":role,"status":status}),
    )?;
    if status != "active" {
        let mut statement = tx.prepare("UPDATE lab_account_bindings SET valid_to=?,ended_by=?,
            status='revoked',reason='user disabled' WHERE user_id=? AND valid_to IS NULL RETURNING id")?;
        let ids = statement
            .query_map(params![now, actor, target], |row| row.get::<_, String>(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        for id in ids {
            store::audit(
                &tx,
                actor,
                request,
                "binding.ended",
                "binding",
                &id,
                json!({"status":"revoked","reason":"user disabled"}),
            )?;
        }
    }
    let user = store::user_payload(&tx, target)?.expect("updated user exists");
    tx.commit()?;
    Ok(Outcome::Updated(user))
}

#[derive(Deserialize)]
pub struct Migration {
    pending_user_id: String,
    reason: String,
}

pub async fn migrate_identity(
    State(app): State<Arc<App>>,
    Extension(session): Extension<Session>,
    Path(target): Path<String>,
    Json(input): Json<Migration>,
) -> Reply {
    let actor = session.user.id.clone();
    let request = session.request.clone();
    let outcome = lab(&app)
        .store
        .run(move |conn| migrate(conn, &actor, &request, &target, input))
        .await
        .map_err(|error| database_error(&session, error))?;
    response(&session, outcome, "invalid_identity_migration")
}

fn migrate(
    conn: &mut Connection,
    actor: &str,
    request: &str,
    target: &str,
    input: Migration,
) -> rusqlite::Result<Outcome> {
    let reason = input.reason.trim();
    let source = input.pending_user_id.as_str();
    if reason.is_empty() || reason.chars().count() > 500 || source == target {
        return Ok(Outcome::Invalid);
    }
    let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
    if !is_admin(&tx, actor)? {
        return Ok(Outcome::Forbidden);
    }
    let candidate = tx
        .query_row(
            "SELECT p.access_issuer,p.access_subject,p.email,p.email_normalized,p.last_login_at
        FROM lab_users p JOIN lab_users t ON t.email_normalized=p.email_normalized
        WHERE p.id=? AND t.id=? AND p.status='pending_identity_review'",
            params![source, target],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, f64>(4)?,
                ))
            },
        )
        .optional()?;
    let Some((issuer, subject, email, normalized, last_login)) = candidate else {
        return Ok(Outcome::Invalid);
    };
    let now = unix_time();
    tx.execute(
        "UPDATE lab_users SET access_issuer=?,status='disabled',updated_at=? WHERE id=?",
        params![format!("migrated:{source}"), now, source],
    )?;
    tx.execute(
        "UPDATE lab_users SET access_issuer=?,access_subject=?,email=?,email_normalized=?,
        updated_at=?,last_login_at=? WHERE id=?",
        params![issuer, subject, email, normalized, now, last_login, target],
    )?;
    store::audit(
        &tx,
        actor,
        request,
        "identity.migrated",
        "user",
        target,
        json!({"pending_user_id":source,"reason":reason}),
    )?;
    let user = store::user_payload(&tx, target)?.expect("migrated user exists");
    tx.commit()?;
    Ok(Outcome::Updated(user))
}
