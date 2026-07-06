use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::body::Body;
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path as AxumPath, Query, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use parking_lot::RwLock;
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::sync::broadcast;

use crate::analytics;
use crate::cluster::{parse_agent_hello, ClusterState};
use crate::db::{DbError, SQLiteStore};
use crate::highres::{self, HighresGpuCache, JobFilter};
use crate::settings::{ManagerSettings, SettingsUpdate};

#[derive(Debug, Clone)]
pub struct AppState {
    pub cluster_state: ClusterState,
    pub settings: Arc<RwLock<ManagerSettings>>,
    pub agent_token: Option<String>,
    pub db_path: Option<Arc<PathBuf>>,
    pub highres_cache: Arc<RwLock<HighresGpuCache>>,
    pub config_tx: broadcast::Sender<Value>,
    pub highres_tx: broadcast::Sender<Value>,
    pub highres_published: Arc<AtomicU64>,
    pub connection_ids: Arc<AtomicU64>,
}

impl AppState {
    pub fn new(
        cluster_state: ClusterState,
        settings: ManagerSettings,
        agent_token: Option<String>,
    ) -> Self {
        let (config_tx, _) = broadcast::channel(128);
        let (highres_tx, _) = broadcast::channel(256);
        Self {
            cluster_state,
            settings: Arc::new(RwLock::new(settings)),
            agent_token,
            db_path: None,
            highres_cache: Arc::new(RwLock::new(HighresGpuCache::default())),
            config_tx,
            highres_tx,
            highres_published: Arc::new(AtomicU64::new(0)),
            connection_ids: Arc::new(AtomicU64::new(1)),
        }
    }

    pub fn with_db_store(mut self, mut store: SQLiteStore) -> Self {
        self.db_path = Some(Arc::new(store.path().to_path_buf()));
        store.close();
        self
    }

    pub fn with_db_path(mut self, path: PathBuf) -> Self {
        self.db_path = Some(Arc::new(path));
        self
    }
}

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/api/health", get(health))
        .route("/api/snapshot", get(deprecated_snapshot))
        .route("/api/cluster/snapshot", get(cluster_snapshot))
        .route("/api/settings", get(settings).patch(update_settings))
        .route("/api/history/gpu", get(gpu_history))
        .route("/api/history/tasks", get(task_history))
        .route("/api/users", get(users))
        .route("/api/analytics/overview", get(analytics_overview))
        .route("/api/analytics/node/:node_id", get(analytics_node))
        .route("/api/highres/status", get(highres_status))
        .route("/api/highres/jobs", get(highres_jobs))
        .route("/api/highres/jobs/:job_key/gpu", get(highres_job_gpu))
        .route("/api/highres/jobs/:job_key", get(highres_job))
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

#[derive(Debug, Deserialize)]
struct AnalyticsRangeQuery {
    range: Option<String>,
}

async fn analytics_overview(
    State(state): State<AppState>,
    Query(query): Query<AnalyticsRangeQuery>,
) -> Result<Json<Value>, Response> {
    let Some(db_path) = state.db_path else {
        return Ok(disabled_object().await);
    };
    let store = open_store(&db_path).map_err(internal_error)?;
    Ok(Json(
        analytics::overview_analytics(&store, query.range.as_deref().unwrap_or("7d"), None)
            .map_err(internal_error)?,
    ))
}

async fn analytics_node(
    State(state): State<AppState>,
    AxumPath(node_id): AxumPath<String>,
    Query(query): Query<AnalyticsRangeQuery>,
) -> Result<Json<Value>, Response> {
    let Some(db_path) = state.db_path else {
        return Ok(disabled_object().await);
    };
    let store = open_store(&db_path).map_err(internal_error)?;
    Ok(Json(
        analytics::node_analytics(
            &store,
            &node_id,
            query.range.as_deref().unwrap_or("24h"),
            None,
        )
        .map_err(internal_error)?,
    ))
}

async fn highres_status(State(state): State<AppState>) -> Json<Value> {
    Json(state.highres_cache.read().status())
}

#[derive(Debug, Deserialize)]
struct GpuHistoryQuery {
    node_id: Option<String>,
    gpu_uuid: Option<String>,
    since: Option<f64>,
    until: Option<f64>,
    limit: Option<i64>,
}

async fn gpu_history(
    State(state): State<AppState>,
    Query(query): Query<GpuHistoryQuery>,
) -> Result<Json<Value>, Response> {
    let Some(db_path) = state.db_path else {
        return Ok(disabled_items().await);
    };
    let store = open_store(&db_path).map_err(internal_error)?;
    let items = store
        .query_gpu_history(
            query.node_id.as_deref(),
            query.gpu_uuid.as_deref(),
            query.since,
            query.until,
            None,
            query.limit.unwrap_or(1000).clamp(1, 5000),
        )
        .map_err(internal_error)?;
    Ok(Json(json!({"enabled": true, "items": items})))
}

#[derive(Debug, Deserialize)]
struct TaskHistoryQuery {
    user: Option<String>,
    status: Option<String>,
    limit: Option<i64>,
}

async fn task_history(
    State(state): State<AppState>,
    Query(query): Query<TaskHistoryQuery>,
) -> Result<Json<Value>, Response> {
    let Some(db_path) = state.db_path else {
        return Ok(disabled_items().await);
    };
    let store = open_store(&db_path).map_err(internal_error)?;
    let items = store
        .query_tasks(
            query.user.as_deref(),
            query.status.as_deref(),
            query.limit.unwrap_or(200).clamp(1, 1000),
        )
        .map_err(internal_error)?;
    Ok(Json(json!({"enabled": true, "items": items})))
}

async fn users(State(state): State<AppState>) -> Result<Json<Value>, Response> {
    let Some(db_path) = state.db_path else {
        return Ok(disabled_items().await);
    };
    let store = open_store(&db_path).map_err(internal_error)?;
    let items = store.query_users().map_err(internal_error)?;
    Ok(Json(json!({"enabled": true, "items": items})))
}

#[derive(Debug, Deserialize)]
struct HighresJobsQuery {
    q: Option<String>,
    user: Option<String>,
    pid: Option<i64>,
    node_id: Option<String>,
    status: Option<String>,
    since: Option<f64>,
    until: Option<f64>,
    max_duration_seconds: Option<f64>,
    recent_seconds: Option<f64>,
    limit: Option<i64>,
}

async fn highres_jobs(
    State(state): State<AppState>,
    Query(query): Query<HighresJobsQuery>,
) -> Result<Json<Value>, Response> {
    let Some(db_path) = state.db_path else {
        return Ok(disabled_items().await);
    };
    let store = open_store(&db_path).map_err(internal_error)?;
    let items = highres::query_jobs(
        &store,
        JobFilter {
            q: query.q,
            user: query.user,
            pid: query.pid,
            node_id: query.node_id,
            status: query.status,
            since: query.since,
            until: query.until,
            max_duration_seconds: query
                .max_duration_seconds
                .map(|value| value.clamp(1.0, highres::HIGHRES_JOB_LOOKBACK_SECONDS)),
            recent_seconds: Some(
                query
                    .recent_seconds
                    .unwrap_or(highres::HIGHRES_JOB_LOOKBACK_SECONDS)
                    .clamp(60.0, highres::HIGHRES_JOB_LOOKBACK_SECONDS),
            ),
            limit: query.limit.unwrap_or(100).clamp(1, 500),
        },
        None,
    )
    .map_err(internal_error)?;
    Ok(Json(json!({"enabled": true, "items": items})))
}

#[derive(Debug, Deserialize)]
struct JobCurveQuery {
    padding_seconds: Option<f64>,
    resolution: Option<String>,
}

async fn highres_job_gpu(
    State(state): State<AppState>,
    AxumPath(job_key): AxumPath<String>,
    Query(query): Query<JobCurveQuery>,
) -> Result<Response, Response> {
    let Some(db_path) = state.db_path else {
        return Ok(Json(json!({"enabled": false, "series": []})).into_response());
    };
    let store = open_store(&db_path).map_err(internal_error)?;
    let cache = state.highres_cache.read().clone();
    match highres::job_curve(
        &store,
        &cache,
        &job_key,
        query
            .padding_seconds
            .unwrap_or(highres::HIGHRES_DEFAULT_PADDING_SECONDS),
        query.resolution.as_deref().unwrap_or("auto"),
        None,
    )
    .map_err(internal_error)?
    {
        Some(payload) => Ok(Json(payload).into_response()),
        None => Ok((
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "job not found"})),
        )
            .into_response()),
    }
}

async fn highres_job(
    State(state): State<AppState>,
    AxumPath(job_key): AxumPath<String>,
) -> Result<Response, Response> {
    let Some(db_path) = state.db_path else {
        return Ok(Json(json!({"enabled": false})).into_response());
    };
    let store = open_store(&db_path).map_err(internal_error)?;
    match highres::get_job(&store, &job_key, None).map_err(internal_error)? {
        Some(item) => Ok(Json(json!({"enabled": true, "item": item})).into_response()),
        None => Ok((
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "job not found"})),
        )
            .into_response()),
    }
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
                                if accepted {
                                    persist_latest_snapshot(&state, &node_id);
                                }
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

async fn highres_stream(State(state): State<AppState>, ws: WebSocketUpgrade) -> impl IntoResponse {
    ws.on_upgrade(|mut socket| async move {
        let mut rx = state.highres_tx.subscribe();
        let _ = send_json(
            &mut socket,
            &json!({
                "type": "hello",
                "subscriber_count": state.highres_tx.receiver_count(),
                "published_count": state.highres_published.load(Ordering::Relaxed),
            }),
        )
        .await;
        loop {
            tokio::select! {
                message = rx.recv() => {
                    match message {
                        Ok(message) => {
                            if send_json(&mut socket, &message).await.is_err() {
                                return;
                            }
                        }
                        Err(broadcast::error::RecvError::Lagged(_)) => continue,
                        Err(broadcast::error::RecvError::Closed) => return,
                    }
                }
                incoming = socket.recv() => {
                    if incoming.is_none() {
                        return;
                    }
                }
            }
        }
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

fn internal_error(error: impl std::fmt::Display) -> Response {
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(json!({"detail": error.to_string()})),
    )
        .into_response()
}

fn open_store(path: &PathBuf) -> Result<SQLiteStore, DbError> {
    let mut store = SQLiteStore::new(path.clone());
    store.open()?;
    Ok(store)
}

fn persist_latest_snapshot(state: &AppState, node_id: &str) {
    let Some(snapshot) = state.cluster_state.latest_node_snapshot(node_id) else {
        return;
    };
    state.highres_cache.write().add_snapshot(&snapshot);
    let message = highres::gpu_sample_message(&snapshot);
    if state.highres_tx.send(message).is_ok() {
        state.highres_published.fetch_add(1, Ordering::Relaxed);
    }
    let Some(db_path) = &state.db_path else {
        return;
    };
    match open_store(db_path) {
        Ok(store) => {
            if let Err(error) = store.write_node_snapshot(&snapshot, false) {
                tracing::warn!(error = %error, "failed to persist node snapshot");
            }
        }
        Err(error) => tracing::warn!(error = %error, "failed to open sqlite store"),
    }
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
