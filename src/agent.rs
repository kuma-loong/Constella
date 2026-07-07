use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use futures_util::{SinkExt, StreamExt};
use serde::Serialize;
use serde_json::{json, Value};
use thiserror::Error;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::header::AUTHORIZATION;
use tokio_tungstenite::tungstenite::Message;

use crate::cluster::SCHEMA_VERSION;
use crate::collector::SnapshotCollector;
use crate::schema::{local_hostname, local_node_id, GpuHardwareInfo, NodeHardware, Snapshot};
use crate::settings::{validate_refresh_interval, SettingsError};
use crate::{nvidia_smi, nvml};

const RECONNECT_DELAYS: [f64; 5] = [1.0, 2.0, 5.0, 15.0, 30.0];

#[derive(Debug, Error)]
pub enum AgentError {
    #[error("agent token is required via CONSTELLA_AGENT_TOKEN or token file")]
    MissingToken,
    #[error("manager url is required via CONSTELLA_MANAGER_URL")]
    MissingManagerUrl,
    #[error(transparent)]
    Settings(#[from] SettingsError),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    WebSocket(#[from] tokio_tungstenite::tungstenite::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
    #[error("invalid authorization header")]
    InvalidAuthorizationHeader,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AgentConfig {
    pub node_id: String,
    pub manager_url: String,
    pub token: String,
    pub refresh_interval: f64,
    pub process_interval: f64,
    pub state_file: PathBuf,
    pub heartbeat_seconds: f64,
}

impl AgentConfig {
    pub fn from_env(
        node_id: Option<String>,
        manager_url: Option<String>,
        token: Option<String>,
        token_file: Option<PathBuf>,
        refresh_interval: Option<f64>,
        process_interval: Option<f64>,
        state_file: Option<PathBuf>,
    ) -> Result<Self, AgentError> {
        let token_file = token_file.or_else(|| {
            std::env::var("CONSTELLA_AGENT_TOKEN_FILE")
                .ok()
                .map(PathBuf::from)
        });
        let token = if let Some(token) = token
            .filter(|value| !value.is_empty())
            .or_else(|| std::env::var("CONSTELLA_AGENT_TOKEN").ok())
            .filter(|value| !value.is_empty())
        {
            token
        } else if let Some(path) = token_file.as_ref() {
            let token = std::fs::read_to_string(path)?.trim().to_string();
            if token.is_empty() {
                return Err(AgentError::MissingToken);
            }
            token
        } else {
            return Err(AgentError::MissingToken);
        };

        let manager_url = manager_url
            .filter(|value| !value.is_empty())
            .or_else(|| std::env::var("CONSTELLA_MANAGER_URL").ok())
            .filter(|value| !value.is_empty())
            .ok_or(AgentError::MissingManagerUrl)?;

        let refresh = refresh_interval
            .or_else(|| {
                std::env::var("CONSTELLA_REFRESH_SECONDS")
                    .ok()?
                    .parse()
                    .ok()
            })
            .unwrap_or(1.0);
        let process = process_interval
            .or_else(|| {
                std::env::var("CONSTELLA_PROCESS_SECONDS")
                    .ok()?
                    .parse()
                    .ok()
            })
            .unwrap_or(5.0);
        let state_file = state_file
            .or_else(|| {
                std::env::var("CONSTELLA_AGENT_STATE_FILE")
                    .ok()
                    .map(PathBuf::from)
            })
            .unwrap_or_else(default_state_file);

        Ok(Self {
            node_id: node_id
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| local_node_id(None)),
            manager_url,
            token,
            refresh_interval: validate_refresh_interval(refresh)?,
            process_interval: process.max(1.0),
            state_file,
            heartbeat_seconds: 10.0,
        })
    }
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct AgentStatus {
    pub node_id: String,
    pub pid: u32,
    pub status: String,
    pub last_sample_at: Option<f64>,
    pub last_sent_at: Option<f64>,
    pub last_error: Option<String>,
}

impl AgentStatus {
    pub fn starting(node_id: String) -> Self {
        Self {
            node_id,
            pid: std::process::id(),
            status: "starting".to_string(),
            last_sample_at: None,
            last_sent_at: None,
            last_error: None,
        }
    }
}

pub async fn run_agent(config: AgentConfig) -> Result<(), AgentError> {
    let mut collector =
        SnapshotCollector::new(config.refresh_interval, config.process_interval, 120)?;
    let mut status = AgentStatus::starting(config.node_id.clone());
    let hardware = sample_hardware_inventory();
    let mut attempt = 0usize;
    loop {
        match run_connection(&config, &mut collector, &mut status, hardware.clone()).await {
            Ok(()) => attempt = 0,
            Err(error) => {
                status.status = "offline".to_string();
                status.last_error = Some(error.to_string());
                write_state_file(&config.state_file, &status)?;
                let delay = reconnect_delay(attempt);
                attempt += 1;
                tracing::warn!(%delay, error = %error, "agent connection failed; retrying");
                tokio::time::sleep(Duration::from_secs_f64(delay)).await;
            }
        }
    }
}

pub async fn run_connection(
    config: &AgentConfig,
    collector: &mut SnapshotCollector,
    status: &mut AgentStatus,
    hardware: Option<NodeHardware>,
) -> Result<(), AgentError> {
    let mut request = config.manager_url.clone().into_client_request()?;
    let auth_value = format!("Bearer {}", config.token)
        .parse()
        .map_err(|_| AgentError::InvalidAuthorizationHeader)?;
    request.headers_mut().insert(AUTHORIZATION, auth_value);

    let (socket, _) = connect_async(request).await?;
    let (mut writer, mut reader) = socket.split();

    writer
        .send(Message::Text(agent_hello(config, hardware).to_string()))
        .await?;
    status.status = "online".to_string();
    status.last_error = None;
    write_state_file(&config.state_file, status)?;

    let mut sample_seq = 0i64;
    let mut next_process_sample = Instant::now();
    let mut sample_tick = tokio::time::interval(Duration::from_secs_f64(config.refresh_interval));
    sample_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut heartbeat_tick =
        tokio::time::interval(Duration::from_secs_f64(config.heartbeat_seconds));
    heartbeat_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);

    loop {
        tokio::select! {
            _ = sample_tick.tick() => {
                let now = Instant::now();
                let collect_processes = now >= next_process_sample;
                if collect_processes {
                    next_process_sample = now + Duration::from_secs_f64(collector.process_interval());
                }
                let snapshot = collector.sample_once(collect_processes);
                sample_seq += 1;
                status.last_sample_at = Some(snapshot.timestamp);
                writer.send(Message::Text(agent_sample(config, sample_seq, &snapshot, collector.process_interval()).to_string())).await?;
                status.last_sent_at = Some(now_seconds());
                write_state_file(&config.state_file, status)?;
            }
            _ = heartbeat_tick.tick() => {
                sample_seq += 1;
                writer.send(Message::Text(agent_heartbeat(config, sample_seq).to_string())).await?;
                status.last_sent_at = Some(now_seconds());
                write_state_file(&config.state_file, status)?;
            }
            maybe_message = reader.next() => {
                let Some(message) = maybe_message else {
                    return Ok(());
                };
                let message = message?;
                if let Message::Text(raw) = message {
                    let previous_refresh = collector.refresh_interval;
                    apply_manager_message(collector, &raw);
                    if (collector.refresh_interval - previous_refresh).abs() > f64::EPSILON {
                        sample_tick = tokio::time::interval(Duration::from_secs_f64(collector.refresh_interval));
                        sample_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
                    }
                }
            }
        }
    }
}

pub fn apply_manager_message(collector: &mut SnapshotCollector, raw: &str) {
    let Ok(message) = serde_json::from_str::<Value>(raw) else {
        return;
    };
    if message.get("type").and_then(Value::as_str) != Some("config") {
        return;
    }
    if let Some(refresh) = message.get("refresh_interval").and_then(Value::as_f64) {
        let _ = collector.set_refresh_interval(refresh);
    }
    if let Some(process) = message.get("process_interval").and_then(Value::as_f64) {
        collector.set_process_interval(process);
    }
}

pub fn agent_hello(config: &AgentConfig, hardware: Option<NodeHardware>) -> Value {
    let mut message = json!({
        "type": "hello",
        "schema_version": SCHEMA_VERSION,
        "node_id": config.node_id,
        "hostname": local_hostname(None),
        "agent_version": env!("CARGO_PKG_VERSION"),
        "capabilities": {
            "nvml": true,
            "nvidia_smi_fallback": true,
            "process_cmdline": true
        }
    });
    if let Some(hardware) = hardware {
        message["hardware"] = serde_json::to_value(hardware).expect("hardware serializes");
    }
    message
}

pub fn sample_hardware_inventory() -> Option<NodeHardware> {
    nvml::sample_hardware_inventory().or_else(|| {
        nvidia_smi::sample(false)
            .ok()
            .map(|snapshot| hardware_from_snapshot(&snapshot))
    })
}

pub fn hardware_from_snapshot(snapshot: &Snapshot) -> NodeHardware {
    NodeHardware {
        gpus: snapshot
            .gpus
            .iter()
            .map(|gpu| GpuHardwareInfo {
                index: gpu.index,
                uuid: gpu.uuid.clone(),
                name: gpu.name.clone(),
                architecture: architecture_from_name(&gpu.name),
            })
            .collect(),
    }
}

fn architecture_from_name(name: &str) -> Option<String> {
    let upper = name.to_ascii_uppercase();
    if upper.contains("BLACKWELL") || upper.contains("B200") || upper.contains("GB200") {
        Some("Blackwell".to_string())
    } else if upper.contains("H100") || upper.contains("H200") || upper.contains("GH200") {
        Some("Hopper".to_string())
    } else if upper.contains("ADA") || upper.contains("L40") || upper.contains("RTX 4090") {
        Some("Ada".to_string())
    } else if upper.contains("A100")
        || upper.contains("A800")
        || upper.contains("A40")
        || upper.contains("A30")
    {
        Some("Ampere".to_string())
    } else {
        None
    }
}

pub fn agent_sample(
    config: &AgentConfig,
    seq: i64,
    snapshot: &Snapshot,
    process_interval: f64,
) -> Value {
    json!({
        "type": "sample",
        "schema_version": SCHEMA_VERSION,
        "node_id": config.node_id,
        "seq": seq,
        "sampled_at": snapshot.timestamp,
        "refresh_interval": snapshot.refresh_interval,
        "process_interval": process_interval,
        "snapshot": snapshot_to_agent_payload(snapshot),
    })
}

pub fn snapshot_to_agent_payload(snapshot: &Snapshot) -> Value {
    let mut payload = serde_json::to_value(snapshot).expect("snapshot serializes");
    if let Value::Object(ref mut object) = payload {
        object.remove("history");
    }
    payload
}

pub fn agent_heartbeat(config: &AgentConfig, seq: i64) -> Value {
    json!({
        "type": "heartbeat",
        "schema_version": SCHEMA_VERSION,
        "node_id": config.node_id,
        "seq": seq,
        "timestamp": now_seconds(),
    })
}

pub fn reconnect_delay(attempt: usize) -> f64 {
    RECONNECT_DELAYS[attempt.min(RECONNECT_DELAYS.len() - 1)]
}

pub fn write_state_file(path: &Path, status: &AgentStatus) -> Result<(), AgentError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let payload = serde_json::to_string_pretty(status)?;
    let temp_path = path.with_extension(format!(
        "{}tmp",
        path.extension()
            .and_then(|value| value.to_str())
            .map(|value| format!("{value}."))
            .unwrap_or_default()
    ));
    std::fs::write(&temp_path, format!("{payload}\n"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&temp_path, std::fs::Permissions::from_mode(0o600))?;
    }
    std::fs::rename(temp_path, path)?;
    Ok(())
}

fn now_seconds() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

fn default_state_file() -> PathBuf {
    std::env::var("HOME")
        .ok()
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".constella")
        .join("run")
        .join("agent-state.json")
}
