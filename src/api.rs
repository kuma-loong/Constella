use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::body::Body;
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Query, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use parking_lot::RwLock;
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::sync::broadcast;

use crate::cluster::{parse_agent_hello, ClusterState};
use crate::settings::{ManagerSettings, SettingsUpdate};

#[derive(Debug, Clone)]
pub struct AppState {
    pub cluster_state: ClusterState,
    pub settings: Arc<RwLock<ManagerSettings>>,
    pub agent_token: Option<String>,
    pub config_tx: broadcast::Sender<Value>,
    pub connection_ids: Arc<AtomicU64>,
}

impl AppState {
    pub fn new(
        cluster_state: ClusterState,
        settings: ManagerSettings,
        agent_token: Option<String>,
    ) -> Self {
        let (config_tx, _) = broadcast::channel(128);
        Self {
            cluster_state,
            settings: Arc::new(RwLock::new(settings)),
            agent_token,
            config_tx,
            connection_ids: Arc::new(AtomicU64::new(1)),
        }
    }
}

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/api/health", get(health))
        .route("/api/snapshot", get(deprecated_snapshot))
        .route("/api/cluster/snapshot", get(cluster_snapshot))
        .route("/api/settings", get(settings).patch(update_settings))
        .route("/api/history/gpu", get(disabled_items))
        .route("/api/history/tasks", get(disabled_items))
        .route("/api/users", get(disabled_items))
        .route("/api/analytics/overview", get(disabled_object))
        .route("/api/analytics/node/:node_id", get(disabled_object))
        .route("/api/highres/status", get(highres_status))
        .route("/api/highres/jobs", get(disabled_items))
        .route("/api/highres/jobs/*job_key", get(disabled_highres_job))
        .route("/ws/gpu", get(deprecated_gpu_ws))
        .route("/ws/cluster", get(cluster_ws))
        .route("/api/agents/ws", get(agent_ws))
        .route("/api/highres/stream", get(highres_stream))
        .with_state(state)
}

async fn health(State(state): State<AppState>) -> Json<Value> {
    let snapshot = state.cluster_state.snapshot(None);
    Json(json!({
        "ok": true,
        "seq": snapshot.seq,
        "source": "manager",
        "agent_ingest_enabled": state.agent_token.is_some(),
        "node_count": snapshot.totals.node_count,
        "online_node_count": snapshot.totals.online_node_count,
        "gpu_count": snapshot.totals.node.gpu_count,
    }))
}

async fn deprecated_snapshot() -> impl IntoResponse {
    (
        StatusCode::GONE,
        Json(json!({"detail": "GET /api/snapshot is retired; use GET /api/cluster/snapshot"})),
    )
}

async fn cluster_snapshot(State(state): State<AppState>) -> Json<Value> {
    Json(
        serde_json::to_value(state.cluster_state.snapshot(None))
            .expect("cluster snapshot serializes"),
    )
}

async fn settings(State(state): State<AppState>) -> Json<Value> {
    Json(serde_json::to_value(state.settings.read().to_payload()).expect("settings serializes"))
}

async fn update_settings(
    State(state): State<AppState>,
    Json(update): Json<SettingsUpdate>,
) -> Result<Json<Value>, Response> {
    let (payload, config) = {
        let mut settings = state.settings.write();
        let payload = settings.update(update).map_err(|error| {
            (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": error.to_string()})),
            )
                .into_response()
        })?;
        let config = serde_json::to_value(settings.config_message()).expect("config serializes");
        (payload, config)
    };
    let _ = state.config_tx.send(config);
    Ok(Json(
        serde_json::to_value(payload).expect("settings payload serializes"),
    ))
}

async fn disabled_items() -> Json<Value> {
    Json(json!({"enabled": false, "items": []}))
}

async fn disabled_object() -> Json<Value> {
    Json(json!({"enabled": false}))
}

async fn highres_status() -> Json<Value> {
    Json(json!({
        "retention_seconds": 0.0,
        "gpu_count": 0,
        "oldest_at": null,
        "newest_at": null,
    }))
}

#[derive(Debug, Deserialize)]
struct JobCurveQuery {
    #[allow(dead_code)]
    padding_seconds: Option<f64>,
    #[allow(dead_code)]
    resolution: Option<String>,
}

async fn disabled_highres_job(Query(_query): Query<JobCurveQuery>) -> Json<Value> {
    Json(json!({"enabled": false, "series": []}))
}

async fn deprecated_gpu_ws(ws: WebSocketUpgrade) -> impl IntoResponse {
    ws.on_upgrade(|socket| async move {
        let _ = socket.close().await;
    })
}

async fn cluster_ws(State(state): State<AppState>, ws: WebSocketUpgrade) -> impl IntoResponse {
    ws.on_upgrade(move |socket| cluster_socket(state, socket))
}

async fn cluster_socket(state: AppState, mut socket: WebSocket) {
    let mut last_seq = u64::MAX;
    let mut last_sent_at = Instant::now() - Duration::from_secs(10);
    loop {
        let current = state.cluster_state.snapshot(None);
        if current.seq != last_seq {
            last_seq = current.seq;
            if send_json(&mut socket, &current).await.is_err() {
                return;
            }
            last_sent_at = Instant::now();
        }
        let interval = Duration::from_secs_f64(state.settings.read().refresh_interval.max(0.5));
        let _ = state
            .cluster_state
            .wait_for_update(last_seq, interval)
            .await;
        let elapsed = last_sent_at.elapsed();
        if elapsed < interval {
            tokio::time::sleep(interval - elapsed).await;
        }
    }
}

async fn agent_ws(
    State(state): State<AppState>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> impl IntoResponse {
    if !authorized(&headers, state.agent_token.as_deref()) {
        return StatusCode::UNAUTHORIZED.into_response();
    }
    ws.on_upgrade(move |socket| agent_socket(state, socket))
        .into_response()
}

async fn agent_socket(state: AppState, mut socket: WebSocket) {
    let connection_id = state.connection_ids.fetch_add(1, Ordering::Relaxed);
    let Some(Ok(Message::Text(raw_hello))) = socket.recv().await else {
        return;
    };
    let Ok(hello_value) = serde_json::from_str::<Value>(&raw_hello) else {
        let _ = send_json(
            &mut socket,
            &json!({"type": "error", "error": "invalid json"}),
        )
        .await;
        return;
    };
    let hello = match parse_agent_hello(&hello_value) {
        Ok(hello) => hello,
        Err(error) => {
            let _ = send_json(
                &mut socket,
                &json!({"type": "error", "error": error.to_string()}),
            )
            .await;
            return;
        }
    };
    let node_id = hello.node_id.clone();
    state
        .cluster_state
        .register_hello(hello, None, Some(connection_id));
    let config =
        serde_json::to_value(state.settings.read().config_message()).expect("config serializes");
    if send_json(&mut socket, &config).await.is_err() {
        state
            .cluster_state
            .disconnect(&node_id, None, Some(connection_id));
        return;
    }
    let mut config_rx = state.config_tx.subscribe();
    loop {
        tokio::select! {
            maybe_message = socket.recv() => {
                let Some(Ok(message)) = maybe_message else {
                    state.cluster_state.disconnect(&node_id, None, Some(connection_id));
                    return;
                };
                let Message::Text(raw) = message else {
                    continue;
                };
                let Ok(value) = serde_json::from_str::<Value>(&raw) else {
                    let _ = send_json(&mut socket, &json!({"type": "error", "error": "invalid json"})).await;
                    continue;
                };
                match value.get("type").and_then(Value::as_str) {
                    Some("sample") => {
                        let seq = value.get("seq").cloned().unwrap_or(Value::Null);
                        match state.cluster_state.ingest_sample(&value, None, Some(connection_id)) {
                            Ok(accepted) => {
                                if send_json(&mut socket, &json!({"type": "ack", "seq": seq, "accepted": accepted})).await.is_err() {
                                    return;
                                }
                            }
                            Err(error) => {
                                let _ = send_json(&mut socket, &json!({"type": "error", "error": error.to_string()})).await;
                            }
                        }
                    }
                    Some("heartbeat") => {
                        let heartbeat_node_id = value.get("node_id").and_then(Value::as_str).unwrap_or(&node_id).to_string();
                        let seq = value.get("seq").and_then(Value::as_i64);
                        if !heartbeat_node_id.is_empty() {
                            state.cluster_state.ingest_heartbeat(&heartbeat_node_id, seq, None, Some(connection_id));
                        }
                        let seq_value = value.get("seq").cloned().unwrap_or(Value::Null);
                        if send_json(&mut socket, &json!({"type": "ack", "seq": seq_value})).await.is_err() {
                            return;
                        }
                    }
                    other => {
                        let _ = send_json(&mut socket, &json!({"type": "error", "error": format!("unsupported agent message: {}", other.unwrap_or(""))})).await;
                    }
                }
            }
            Ok(config) = config_rx.recv() => {
                if send_json(&mut socket, &config).await.is_err() {
                    return;
                }
            }
        }
    }
}

async fn highres_stream(ws: WebSocketUpgrade) -> impl IntoResponse {
    ws.on_upgrade(|mut socket| async move {
        let _ = send_json(
            &mut socket,
            &json!({"type": "hello", "subscriber_count": 1, "published_count": 0}),
        )
        .await;
        while socket.recv().await.is_some() {}
    })
}

fn authorized(headers: &HeaderMap, expected_token: Option<&str>) -> bool {
    let Some(expected_token) = expected_token.filter(|value| !value.is_empty()) else {
        return false;
    };
    headers
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        == Some(expected_token)
}

async fn send_json<T: serde::Serialize>(
    socket: &mut WebSocket,
    value: &T,
) -> Result<(), axum::Error> {
    socket
        .send(Message::Text(
            serde_json::to_string(value).expect("websocket payload serializes"),
        ))
        .await
}

#[allow(dead_code)]
fn empty_body() -> Body {
    Body::empty()
}
