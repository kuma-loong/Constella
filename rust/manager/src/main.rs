mod agent_control;
mod analytics;
mod archive;
mod backup;
mod cluster;
mod db;
mod gpm_rollup;
mod highres;
mod highres_stream;
mod history;
mod job_curves;
mod jobs;
mod lab;
mod overview;
mod result_cache;
mod usage;

use axum::extract::State;
use axum::extract::ws::{CloseFrame, Message, WebSocket, WebSocketUpgrade};
use axum::http::{HeaderMap, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Extension, Json, Router};
use cluster::Cluster;
use constella_protocol::{
    AgentMessage, MAX_SAMPLE_BYTES, ManagerMessage, REFRESH_INTERVALS, Settings, VERSION,
    token_from_env,
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::env;
use std::future::Future;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
use subtle::ConstantTimeEq;
use tokio::sync::watch;
use tower_http::services::{ServeDir, ServeFile};

type Error = Box<dyn std::error::Error + Send + Sync>;

struct App {
    agent_control: agent_control::Broker,
    lab: Option<lab::Lab>,
    cluster: Cluster,
    settings: watch::Sender<Settings>,
    token: Option<String>,
    highres_token: Option<String>,
    highres_stream: highres_stream::Stream,
    connection_ids: AtomicU64,
    shutdown: watch::Sender<bool>,
    database: Option<db::Database>,
    analytics_cache: Arc<result_cache::ResultCache>,
    highres: highres::Highres,
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Error> {
    let command = env::args().nth(1);
    match command.as_deref() {
        Some("--version") => {
            println!("constella-manager {}", env!("CARGO_PKG_VERSION"));
            return Ok(());
        }
        Some("--help") => {
            println!(
                "Usage: constella-manager [lab|migrate|migrate-lab|backup DEST|backup-lab DEST]\nSet CONSTELLA_BIND, CONSTELLA_FRONTEND_DIST, and CONSTELLA_AGENT_TOKEN_FILE."
            );
            return Ok(());
        }
        Some("migrate") => {
            let path = env::var_os("CONSTELLA_DB_PATH").ok_or("CONSTELLA_DB_PATH is required")?;
            db::migrate(std::path::Path::new(&path))?;
            println!("SQLite schema ready");
            return Ok(());
        }
        Some("migrate-lab") => {
            let path =
                env::var_os("CONSTELLA_LAB_DB_PATH").ok_or("CONSTELLA_LAB_DB_PATH is required")?;
            lab::migrate(std::path::Path::new(&path))?;
            println!("Lab identity SQLite schema ready");
            return Ok(());
        }
        Some("backup" | "backup-lab") => {
            let setting = if command.as_deref() == Some("backup") {
                "CONSTELLA_DB_PATH"
            } else {
                "CONSTELLA_LAB_DB_PATH"
            };
            let source = env::var_os(setting).ok_or_else(|| format!("{setting} is required"))?;
            let mut args = env::args_os().skip(2);
            let destination = args.next().ok_or("backup destination is required")?;
            if args.next().is_some() {
                return Err("expected exactly one backup destination".into());
            }
            backup::run(
                std::path::Path::new(&source),
                std::path::Path::new(&destination),
            )?;
            println!("SQLite backup ready");
            return Ok(());
        }
        Some("lab") | None => {}
        Some(_) => return Err("unexpected argument".into()),
    }
    let lab = if command.as_deref() == Some("lab") {
        Some(lab::Lab::from_env()?)
    } else {
        None
    };
    let bind = env::var("CONSTELLA_BIND").unwrap_or_else(|_| "127.0.0.1:8875".into());
    let listener = tokio::net::TcpListener::bind(&bind).await?;
    let capacity: usize = env::var("CONSTELLA_DB_QUEUE_SIZE")
        .unwrap_or_else(|_| "128".into())
        .parse()?;
    if !(1..=16_384).contains(&capacity) {
        return Err("CONSTELLA_DB_QUEUE_SIZE must be between 1 and 16384".into());
    }
    let database = env::var_os("CONSTELLA_DB_PATH")
        .map(|path| db::Database::open(path.into(), capacity))
        .transpose()?;
    let app = Arc::new(App {
        lab,
        agent_control: agent_control::Broker::default(),
        cluster: Cluster::default(),
        settings: watch::channel(Settings::from_env()?).0,
        token: token_from_env("CONSTELLA_AGENT_TOKEN")?,
        highres_token: token_from_env("CONSTELLA_HIGHRES_TOKEN")?,
        highres_stream: highres_stream::Stream::default(),
        connection_ids: AtomicU64::new(0),
        shutdown: watch::channel(false).0,
        database,
        analytics_cache: Arc::default(),
        highres: highres::Highres::default(),
    });
    let mut router = Router::new()
        .route("/api/health", get(health))
        .route("/api/cluster/snapshot", get(snapshot))
        .route("/api/analytics/node/{node_id}", get(analytics::node))
        .route("/api/analytics/overview", get(overview::endpoint))
        .route("/api/history/gpu", get(history::gpu))
        .route("/api/history/tasks", get(history::tasks))
        .route("/api/users", get(history::users))
        .route("/api/highres/status", get(highres::status))
        .route("/api/highres/stream", get(highres_stream::upgrade))
        .route("/api/highres/jobs", get(jobs::search))
        .route("/api/highres/jobs/{job_key}", get(jobs::detail))
        .route("/api/highres/jobs/{job_key}/gpu", get(job_curves::gpu))
        .route(
            "/api/highres/jobs/{job_key}/performance",
            get(job_curves::performance),
        )
        .route("/api/highres/performance", get(highres::performance))
        .route("/api/settings", get(get_settings).patch(update_settings))
        .route("/ws/cluster", get(cluster_ws))
        .route("/api/agents/ws", get(agent_ws))
        .with_state(app.clone());
    if let Some(frontend) = env::var_os("CONSTELLA_FRONTEND_DIST") {
        router = router.fallback_service(ServeDir::new(&frontend).fallback(ServeFile::new(
            std::path::Path::new(&frontend).join("index.html"),
        )));
    }
    if app.lab.is_some() {
        router = lab::protect(router, app.clone());
    }
    eprintln!("Constella manager listening on {}", listener.local_addr()?);
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    let shutdown = app.shutdown.clone();
    axum::serve(listener, router)
        .with_graceful_shutdown(async move {
            tokio::select! {
                _ = tokio::signal::ctrl_c() => {},
                _ = term.recv() => {},
            }
            shutdown.send_replace(true);
        })
        .await?;
    Ok(())
}

async fn health(State(app): State<Arc<App>>) -> Json<Value> {
    let mut health = app
        .cluster
        .health(app.settings.borrow().effective(), app.token.is_some());
    if let Some(database) = &app.database {
        health["ok"] = json!(database.healthy());
        health["database"] = database.status();
    }
    Json(health)
}

async fn snapshot(State(app): State<Arc<App>>) -> impl IntoResponse {
    (
        [(header::CONTENT_TYPE, "application/json")],
        app.cluster.snapshot(app.settings.borrow().effective()).json,
    )
}

fn settings_json(settings: Settings) -> Json<Value> {
    let settings = settings.effective();
    Json(json!({
        "refresh_interval": settings.refresh_interval,
        "process_interval": settings.process_interval,
        "allowed_refresh_intervals": REFRESH_INTERVALS,
    }))
}

async fn get_settings(State(app): State<Arc<App>>) -> Json<Value> {
    settings_json(*app.settings.borrow())
}

#[derive(Deserialize)]
struct SettingsUpdate {
    refresh_interval: Option<f64>,
    process_interval: Option<f64>,
}

async fn update_settings(
    State(app): State<Arc<App>>,
    Json(update): Json<SettingsUpdate>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let old = *app.settings.borrow();
    let settings = Settings::new(
        update.refresh_interval.unwrap_or(old.refresh_interval),
        update.process_interval.unwrap_or(old.process_interval),
    )
    .ok_or_else(|| {
        (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "invalid sampling intervals"})),
        )
    })?;
    app.settings.send_replace(settings);
    Ok(settings_json(settings))
}

async fn cluster_ws(
    ws: WebSocketUpgrade,
    State(app): State<Arc<App>>,
    session: Option<Extension<lab::Session>>,
) -> Response {
    ws.max_message_size(1024)
        .max_frame_size(1024)
        .on_upgrade(move |socket| {
            until_shutdown(
                app.clone(),
                publish_cluster(socket, app, session.map(|Extension(user)| user)),
            )
        })
}

async fn session_close(session: Option<lab::Session>, app: Arc<App>) -> u16 {
    let Some(session) = session else {
        return std::future::pending().await;
    };
    let Some(lab) = &app.lab else {
        return 4403;
    };
    let lifetime = (session.expires as f64 - constella_protocol::unix_time()).max(0.0);
    let deadline = tokio::time::sleep(Duration::from_secs_f64(lifetime.min(900.0)));
    tokio::pin!(deadline);
    loop {
        tokio::select! {
            _ = &mut deadline => return if lifetime <= 900.0 { 4401 } else { 4000 },
            _ = tokio::time::sleep(Duration::from_secs(60)) => {
                let id = session.user.id.clone();
                if !matches!(lab.store.run(move |conn| lab::user_by_id(conn, &id)).await,
                    Ok(Some(user)) if user.status == "active") {
                    return 4403;
                }
            }
        }
    }
}

async fn publish_cluster(mut socket: WebSocket, app: Arc<App>, session: Option<lab::Session>) {
    let session_end = session_close(session, app.clone());
    tokio::pin!(session_end);
    let mut updates = app.cluster.updates.subscribe();
    loop {
        updates.borrow_and_update();
        let settings = app.settings.borrow().effective();
        let payload = app.cluster.snapshot(settings);
        if !send_text(&mut socket, payload.text).await {
            return;
        }
        let heartbeat = tokio::time::sleep(Duration::from_secs(5));
        tokio::pin!(heartbeat);
        loop {
            tokio::select! {
                code = &mut session_end => {
                    let _ = socket.send(Message::Close(Some(CloseFrame { code, reason: "".into() }))).await;
                    return;
                }
                _ = &mut heartbeat => break,
                result = updates.changed() => {
                    if result.is_err() {
                        return;
                    }
                    // Let near-simultaneous samples from several nodes share a frame.
                    tokio::time::sleep(Duration::from_millis(25)).await;
                    break;
                }
                message = socket.recv() => match message {
                    None | Some(Err(_)) | Some(Ok(Message::Close(_))) => return,
                    _ => {}
                }
            }
        }
    }
}

async fn agent_ws(
    ws: WebSocketUpgrade,
    State(app): State<Arc<App>>,
    headers: HeaderMap,
) -> Response {
    if !authorized(&headers, app.token.as_deref()) {
        return StatusCode::UNAUTHORIZED.into_response();
    }
    ws.max_message_size(MAX_SAMPLE_BYTES)
        .max_frame_size(MAX_SAMPLE_BYTES)
        .on_upgrade(move |socket| until_shutdown(app.clone(), receive_agent(socket, app)))
}

fn authorized(headers: &HeaderMap, expected: Option<&str>) -> bool {
    expected
        .zip(
            headers
                .get(header::AUTHORIZATION)
                .and_then(|value| value.to_str().ok())
                .and_then(|value| value.strip_prefix("Bearer ")),
        )
        .is_some_and(|(expected, actual)| bool::from(expected.as_bytes().ct_eq(actual.as_bytes())))
}

async fn receive_agent(mut socket: WebSocket, app: Arc<App>) {
    let first = tokio::time::timeout(Duration::from_secs(5), socket.recv()).await;
    let Ok(Some(Ok(Message::Text(first)))) = first else {
        return;
    };
    let Ok(AgentMessage::Hello {
        version: VERSION,
        node_id,
        hostname,
        agent_version,
        instance_id,
    }) = serde_json::from_str(&first)
    else {
        return;
    };
    if node_id.is_empty()
        || node_id.len() > 128
        || node_id.chars().any(char::is_control)
        || hostname.len() > 255
        || agent_version.len() > 64
    {
        return;
    }
    let connection = app.connection_ids.fetch_add(1, Ordering::Relaxed);
    app.cluster.connect(
        node_id.clone(),
        hostname.clone(),
        agent_version.clone(),
        connection,
    );
    let (registration, mut controls) = instance_id
        .filter(|id| {
            !id.is_empty()
                && id.len() <= 64
                && id
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        })
        .map(|instance| {
            app.agent_control
                .connect(node_id.clone(), connection, instance)
        })
        .unzip();
    let _registration = registration;
    let mut pending = std::collections::HashMap::new();
    let mut settings = app.settings.subscribe();
    loop {
        let config = ManagerMessage::Config {
            settings: *settings.borrow_and_update(),
        };
        if !send(&mut socket, serde_json::to_string(&config).unwrap()).await {
            break;
        }
        let idle = tokio::time::sleep(Duration::from_secs(60));
        tokio::pin!(idle);
        loop {
            tokio::select! {
                _ = settings.changed() => break,
                call = async {
                    if let Some(controls) = controls.as_mut() {
                        controls.recv().await
                    } else {
                        std::future::pending().await
                    }
                } => {
                    let Some(agent_control::Call { id, request, reply }) = call else {
                        app.cluster.disconnect(&node_id, connection);
                        return;
                    };
                    pending.retain(|_, reply: &mut tokio::sync::oneshot::Sender<constella_protocol::ControlResponse>| !reply.is_closed());
                    if reply.is_closed() || pending.len() >= 8 {
                        continue;
                    }
                    let command = ManagerMessage::Control { id, request };
                    if !send(&mut socket, serde_json::to_string(&command).unwrap()).await {
                        app.cluster.disconnect(&node_id, connection);
                        return;
                    }
                    pending.insert(id, reply);
                }

                _ = &mut idle => {
                    app.cluster.disconnect(&node_id, connection);
                    return;
                }
                message = socket.recv() => match message {
                    Some(Ok(Message::Text(text))) => {
                        let (seq, sample) = match serde_json::from_str(&text) {
                            Ok(AgentMessage::Sample { seq, sample }) => (seq, sample),
                            Ok(AgentMessage::ControlResponse { id, response }) => {
                                if let Some(reply) = pending.remove(&id) {
                                    let _ = reply.send(response);
                                }
                                continue;
                            }
                            _ => {
                                app.cluster.disconnect(&node_id, connection);
                                return;
                            }
                        };
                        let Some(sample) = app.cluster.ingest(&node_id, connection, seq, *sample) else {
                            app.cluster.disconnect(&node_id, connection);
                            return;
                        };
                        let received_at = constella_protocol::unix_time();
                        let current_settings = *settings.borrow();
                        app.highres.add(&node_id, &sample);
                        app.highres_stream.publish(&node_id, &sample, current_settings.refresh_interval);
                        if let Some(database) = &app.database {
                            database.submit(db::Ingest {
                                node_id: node_id.clone(), hostname: hostname.clone(),
                                agent_version: agent_version.clone(), seq, received_at,
                                settings: current_settings, sample,
                            });
                        }
                        idle.as_mut().reset(tokio::time::Instant::now() + Duration::from_secs(60));
                    }
                    Some(Ok(Message::Ping(_) | Message::Pong(_))) => {}
                    _ => {
                        app.cluster.disconnect(&node_id, connection);
                        return;
                    }
                }
            }
        }
    }
    app.cluster.disconnect(&node_id, connection);
}

async fn until_shutdown(app: Arc<App>, connection: impl Future<Output = ()>) {
    let mut shutdown = app.shutdown.subscribe();
    tokio::select! {
        _ = connection => {},
        _ = shutdown.wait_for(|closed| *closed) => {},
    }
}

async fn send(socket: &mut WebSocket, text: String) -> bool {
    send_text(socket, text.into()).await
}

async fn send_text(socket: &mut WebSocket, text: axum::extract::ws::Utf8Bytes) -> bool {
    matches!(
        tokio::time::timeout(Duration::from_secs(5), socket.send(Message::Text(text))).await,
        Ok(Ok(()))
    )
}
