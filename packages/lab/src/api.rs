use super::{Lab, Session, store};
use crate::{App, history};
use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::routing::get;
use axum::{Extension, Json, Router};
use constella_protocol::unix_time;
use rusqlite::TransactionBehavior;
use rusqlite::params;
use rusqlite::types::Value as SqlValue;
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::Arc;

pub(super) type Failure = (StatusCode, Json<Value>);
pub(super) type Reply = Result<Json<Value>, Failure>;

pub fn routes(app: Arc<App>) -> Router {
    Router::new()
        .route(
            "/api/lab/account-lookups/batch",
            axum::routing::post(super::accounts::preview),
        )
        .route(
            "/api/lab/account-bindings/batch",
            axum::routing::post(super::accounts::create),
        )
        .route(
            "/api/lab/account-bindings/{id}",
            axum::routing::delete(super::accounts::end),
        )
        .route(
            "/api/lab/admin/bindings/{id}/verify",
            axum::routing::post(super::accounts::verify),
        )
        .route(
            "/api/lab/admin/bindings/reassign",
            axum::routing::post(super::accounts::reassign),
        )
        .route("/api/lab/me/activity", get(super::activity::endpoint))
        .route("/api/lab/me", get(me).patch(profile))
        .route(
            "/api/lab/account-binding-nodes",
            get(super::nodes::binding_nodes),
        )
        .route("/api/lab/admin/nodes", get(super::nodes::nodes))
        .route(
            "/api/lab/admin/nodes/{node}/restart",
            axum::routing::post(super::nodes::restart),
        )
        .route("/api/lab/me/readonly", axum::routing::post(readonly))
        .route("/api/lab/admin/users", get(users))
        .route(
            "/api/lab/admin/users/{id}",
            axum::routing::patch(super::admin::update_user),
        )
        .route(
            "/api/lab/admin/users/{id}/migrate-identity",
            axum::routing::post(super::admin::migrate_identity),
        )
        .route("/api/lab/admin/bindings", get(bindings))
        .route("/api/lab/admin/audit-events", get(audit_events))
        .with_state(app)
}

pub(super) fn lab(app: &App) -> &Lab {
    app.lab.as_ref().expect("Lab route enabled")
}

pub(super) fn failure(session: &Session, status: StatusCode, code: &str) -> Failure {
    (
        status,
        Json(json!({"detail": {"code": code, "request_id": session.request}})),
    )
}

pub(super) fn database_error(session: &Session, error: crate::Error) -> Failure {
    eprintln!("Lab API store: {error}");
    failure(
        session,
        StatusCode::SERVICE_UNAVAILABLE,
        "identity_store_unavailable",
    )
}

async fn me(State(app): State<Arc<App>>, Extension(session): Extension<Session>) -> Reply {
    let id = session.user.id.clone();
    let payload = lab(&app)
        .store
        .run(move |conn| store::user_payload(conn, &id))
        .await
        .map_err(|error| database_error(&session, error))?;
    Ok(Json(
        json!({"user": payload, "request_id": session.request}),
    ))
}

#[derive(Deserialize)]
struct Profile {
    display_name: Option<String>,
}

async fn profile(
    State(app): State<Arc<App>>,
    Extension(session): Extension<Session>,
    Json(input): Json<Profile>,
) -> Reply {
    if session.user.role == "viewer" {
        return Err(failure(&session, StatusCode::FORBIDDEN, "member_required"));
    }
    if input
        .display_name
        .as_ref()
        .is_some_and(|name| name.chars().count() > 80)
    {
        return Err(failure(
            &session,
            StatusCode::UNPROCESSABLE_ENTITY,
            "invalid_display_name",
        ));
    }
    let display_name = input
        .display_name
        .as_deref()
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .map(str::to_owned);
    let id = session.user.id.clone();
    let request = session.request.clone();
    let payload = lab(&app)
        .store
        .run(move |conn| {
            let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let changed = tx.execute(
                "UPDATE lab_users SET display_name=?,updated_at=?
            WHERE id=? AND status='active' AND role IN ('member','admin')",
                params![display_name, unix_time(), id],
            )?;
            if changed == 0 {
                return Ok(None);
            }
            store::audit(
                &tx,
                &id,
                &request,
                "user.profile_updated",
                "user",
                &id,
                json!({}),
            )?;
            let result = store::user_payload(&tx, &id)?;
            tx.commit()?;
            Ok(result)
        })
        .await
        .map_err(|error| database_error(&session, error))?
        .ok_or_else(|| failure(&session, StatusCode::FORBIDDEN, "member_required"))?;
    Ok(Json(
        json!({"user": payload, "request_id": session.request}),
    ))
}

async fn readonly(State(app): State<Arc<App>>, Extension(session): Extension<Session>) -> Reply {
    let id = session.user.id.clone();
    let request = session.request.clone();
    let payload = lab(&app)
        .store
        .run(move |conn| {
            let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let changed = tx.execute(
                "UPDATE lab_users SET role='viewer',onboarding_completed_at=?1,updated_at=?1
            WHERE id=?2 AND role='member' AND status='active' AND onboarding_completed_at IS NULL
            AND NOT EXISTS(SELECT 1 FROM lab_account_bindings WHERE user_id=?2)",
                params![unix_time(), id],
            )?;
            if changed == 0 {
                return Ok(None);
            }
            store::audit(
                &tx,
                &id,
                &request,
                "user.readonly_selected",
                "user",
                &id,
                json!({}),
            )?;
            let user = store::user_payload(&tx, &id)?;
            tx.commit()?;
            Ok(user)
        })
        .await
        .map_err(|error| database_error(&session, error))?
        .ok_or_else(|| {
            failure(
                &session,
                StatusCode::CONFLICT,
                "readonly_onboarding_unavailable",
            )
        })?;
    Ok(Json(
        json!({"user": payload, "request_id": session.request}),
    ))
}

#[derive(Deserialize)]
struct Search {
    q: Option<String>,
}

async fn users(
    State(app): State<Arc<App>>,
    Extension(session): Extension<Session>,
    Query(query): Query<Search>,
) -> Reply {
    let rows = lab(&app).store.run(move |conn| {
        let mut filters = Vec::new();
        if let Some(query) = query.q.filter(|value| !value.trim().is_empty()) {
            let pattern = format!("%{}%", query.trim().to_lowercase());
            filters.push(("(u.email_normalized LIKE ?1 OR LOWER(COALESCE(u.display_name,'')) LIKE ?1)",
                SqlValue::Text(pattern)));
        }
        history::fetch(conn, "SELECT u.*,COUNT(CASE WHEN b.valid_to IS NULL THEN b.id END) AS active_binding_count
            FROM lab_users u LEFT JOIN lab_account_bindings b ON b.user_id=u.id",
            filters, "GROUP BY u.id ORDER BY u.created_at ASC", [])
    }).await.map_err(|error| database_error(&session, error))?;
    Ok(Json(json!({"users": rows, "request_id": session.request})))
}

async fn bindings(State(app): State<Arc<App>>, Extension(session): Extension<Session>) -> Reply {
    let bindings = lab(&app)
        .store
        .run(|conn| store::bindings(conn, None, true))
        .await
        .map_err(|error| database_error(&session, error))?;
    Ok(Json(
        json!({"bindings": bindings, "request_id": session.request}),
    ))
}

#[derive(Deserialize)]
struct Page {
    limit: Option<i64>,
    offset: Option<i64>,
}

async fn audit_events(
    State(app): State<Arc<App>>,
    Extension(session): Extension<Session>,
    Query(page): Query<Page>,
) -> Reply {
    let events = lab(&app)
        .store
        .run(move |conn| {
            let mut events = history::fetch(
                conn,
                "SELECT a.*,u.email AS actor_email
            FROM lab_audit_events a LEFT JOIN lab_users u ON u.id=a.actor_user_id",
                Vec::new(),
                "ORDER BY a.id DESC LIMIT ? OFFSET ?",
                [
                    SqlValue::Integer(page.limit.unwrap_or(100).clamp(1, 500)),
                    SqlValue::Integer(page.offset.unwrap_or_default().max(0)),
                ],
            )?;
            for event in &mut events {
                let detail = event
                    .as_object_mut()
                    .expect("SQL row object")
                    .remove("details_json")
                    .unwrap_or(Value::Null);
                event["details"] = serde_json::from_str(detail.as_str().unwrap_or("null"))
                    .map_err(|error| rusqlite::Error::ToSqlConversionFailure(Box::new(error)))?;
            }
            Ok(events)
        })
        .await
        .map_err(|error| database_error(&session, error))?;
    Ok(Json(
        json!({"events": events, "request_id": session.request}),
    ))
}
