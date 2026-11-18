use super::api::{Reply, database_error, failure, lab};
use super::{Session, store};
use crate::{App, agent_control};
use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::{Extension, Json};
use constella_protocol::{ControlRequest, ControlResponse};
use serde_json::{Value, json};
use std::sync::Arc;
use std::time::Duration;

pub(super) fn snapshot(app: &App) -> Vec<Value> {
    serde_json::from_slice::<Value>(&app.cluster.snapshot(app.settings.borrow().effective()).json)
        .expect("cluster snapshot is valid JSON")["nodes"]
        .as_array()
        .cloned()
        .unwrap_or_default()
}

pub async fn binding_nodes(
    State(app): State<Arc<App>>,
    Extension(session): Extension<Session>,
) -> Reply {
    if session.user.role == "viewer" {
        return Err(failure(&session, StatusCode::FORBIDDEN, "member_required"));
    }
    let nodes: Vec<_> = snapshot(&app)
        .iter()
        .map(|node| {
            let connected = node["status"] != "offline";
            let capable = node["node_id"]
                .as_str()
                .and_then(|id| app.agent_control.instance(id))
                .is_some();
            json!({"node_id":node["node_id"],"hostname":node["hostname"],"status":node["status"],
            "connected":connected,"binding_supported":capable})
        })
        .collect();
    Ok(Json(json!({"nodes":nodes,"request_id":session.request})))
}

pub async fn nodes(State(app): State<Arc<App>>, Extension(session): Extension<Session>) -> Reply {
    let hostname = std::fs::read_to_string("/proc/sys/kernel/hostname").unwrap_or_default();
    let nodes: Vec<_> = snapshot(&app)
        .iter()
        .map(|node| {
            let instance = node["node_id"]
                .as_str()
                .and_then(|id| app.agent_control.instance(id));
            let connected = node["status"] != "offline";
            json!({
                "node_id":node["node_id"],"hostname":node["hostname"],"status":node["status"],
                "connected":connected,"restart_supported":instance.is_some(),
                "manager_host":node["hostname"].as_str()==Some(hostname.trim()),
                "agent_version":node["agent_version"],"agent_instance_id":instance,
                "last_seen_at":node["received_at"],
            })
        })
        .collect();
    Ok(Json(json!({"nodes":nodes,"request_id":session.request})))
}

pub async fn restart(
    State(app): State<Arc<App>>,
    Extension(session): Extension<Session>,
    Path(target): Path<String>,
) -> Result<(StatusCode, Json<Value>), super::api::Failure> {
    let identity = lab(&app);
    let Some(instance) = app.agent_control.instance(&target) else {
        let code = if snapshot(&app)
            .iter()
            .any(|node| node["node_id"] == target && node["status"] != "offline")
        {
            "agent_restart_unsupported"
        } else {
            "node_offline"
        };
        return Err(failure(&session, StatusCode::CONFLICT, code));
    };
    if !identity.limits.consume(&target, 1, &[("restart", 1, 60)]) {
        return Err(failure(
            &session,
            StatusCode::TOO_MANY_REQUESTS,
            "agent_restart_cooldown",
        ));
    }
    let actor = session.user.id.clone();
    let request = session.request.clone();
    let node = target.clone();
    let accepted = identity
        .store
        .run(move |conn| {
            let tx = conn.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
            if !store::user_by_id(&tx, &actor)?
                .is_some_and(|user| user.role == "admin" && user.status == "active")
            {
                return Ok(false);
            }
            store::audit(
                &tx,
                &actor,
                &request,
                "agent.restart_requested",
                "node",
                &node,
                json!({"scope":"agent"}),
            )?;
            tx.commit()?;
            Ok(true)
        })
        .await
        .map_err(|error| database_error(&session, error))?;
    if !accepted {
        return Err(failure(&session, StatusCode::FORBIDDEN, "admin_required"));
    }
    let outcome = app
        .agent_control
        .request(
            &target,
            ControlRequest::Restart,
            Duration::from_secs(5),
            Some(&instance),
        )
        .await;
    let (action, error) = match outcome {
        Ok(ControlResponse::RestartAccepted) => ("agent.restart_accepted", None),
        Ok(_) => (
            "agent.restart_rejected",
            Some((StatusCode::BAD_GATEWAY, "agent_restart_rejected")),
        ),
        Err(agent_control::Error::Timeout) => (
            "agent.restart_unconfirmed",
            Some((StatusCode::GATEWAY_TIMEOUT, "agent_restart_unconfirmed")),
        ),
        Err(agent_control::Error::Unavailable) => (
            "agent.restart_unconfirmed",
            Some((StatusCode::CONFLICT, "agent_restart_unconfirmed")),
        ),
    };
    let actor = session.user.id.clone();
    let request = session.request.clone();
    let node = target.clone();
    identity
        .store
        .run(move |conn| {
            store::audit(
                conn,
                &actor,
                &request,
                action,
                "node",
                &node,
                json!({"scope":"agent"}),
            )
        })
        .await
        .map_err(|error| database_error(&session, error))?;
    if let Some((status, code)) = error {
        return Err(failure(&session, status, code));
    }
    Ok((
        StatusCode::ACCEPTED,
        Json(json!({"node_id":target,"status":"accepted","request_id":session.request})),
    ))
}
