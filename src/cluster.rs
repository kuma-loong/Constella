use std::collections::{BTreeMap, VecDeque};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use parking_lot::RwLock;
use serde_json::Value;
use thiserror::Error;
use tokio::sync::Notify;

use crate::schema::{
    cluster_snapshot_from_nodes, gpu_global_id, node_totals_from_gpus, ClusterSnapshot,
    GpuHardwareInfo, GpuInfo, GpuProcess, HistoryPayload, NodeHardware, NodeSnapshot,
    OtherUserMemory,
};

pub const SCHEMA_VERSION: i64 = 1;
pub const HISTORY_SIZE: usize = 120;
const HISTORY_METRICS: [&str; 4] = ["gpu", "memory", "power", "temperature"];

#[derive(Debug, Error, PartialEq)]
pub enum ClusterError {
    #[error("first agent message must be hello")]
    ExpectedHello,
    #[error("agent hello is missing node_id")]
    MissingHelloNodeId,
    #[error("agent message is not a sample")]
    ExpectedSample,
    #[error("agent sample is missing node_id")]
    MissingSampleNodeId,
    #[error("agent sample is missing snapshot")]
    MissingSnapshot,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AgentHello {
    pub node_id: String,
    pub hostname: String,
    pub agent_version: Option<String>,
    pub capabilities: Option<Value>,
    pub hardware: Option<NodeHardware>,
}

#[derive(Debug, Clone)]
pub struct NodeRuntime {
    pub node_id: String,
    pub hostname: String,
    pub snapshot: NodeSnapshot,
    pub last_seq: i64,
    pub connected: bool,
    pub last_seen_at: f64,
    pub agent_version: Option<String>,
    pub connection_id: Option<u64>,
    pub hardware: Option<NodeHardware>,
}

#[derive(Debug, Clone)]
pub struct ClusterState {
    inner: Arc<RwLock<ClusterInner>>,
    notify: Arc<Notify>,
}

#[derive(Debug)]
struct ClusterInner {
    local_node_id: String,
    stale_after: Option<f64>,
    offline_after: Option<f64>,
    latest_by_node: BTreeMap<String, NodeRuntime>,
    history: HistoryAccumulator,
    seq: u64,
}

impl ClusterState {
    pub fn new(local_node_id: String) -> Self {
        Self::with_options(local_node_id, None, None, HISTORY_SIZE)
    }

    pub fn with_options(
        local_node_id: String,
        stale_after: Option<f64>,
        offline_after: Option<f64>,
        history_size: usize,
    ) -> Self {
        Self {
            inner: Arc::new(RwLock::new(ClusterInner {
                local_node_id,
                stale_after,
                offline_after,
                latest_by_node: BTreeMap::new(),
                history: HistoryAccumulator::new(history_size),
                seq: 0,
            })),
            notify: Arc::new(Notify::new()),
        }
    }

    pub fn seq(&self) -> u64 {
        self.inner.read().seq
    }

    pub async fn wait_for_update(&self, last_seq: u64, timeout: std::time::Duration) -> u64 {
        if self.seq() > last_seq {
            return self.seq();
        }
        tokio::select! {
            _ = self.notify.notified() => self.seq(),
            _ = tokio::time::sleep(timeout) => self.seq(),
        }
    }

    pub fn register_hello(&self, hello: AgentHello, now: Option<f64>, connection_id: Option<u64>) {
        let mut inner = self.inner.write();
        let seen_at = now.unwrap_or_else(unix_now);
        if let Some(runtime) = inner.latest_by_node.get_mut(&hello.node_id) {
            runtime.hostname = hello.hostname.clone();
            runtime.connected = true;
            runtime.last_seen_at = seen_at;
            runtime.last_seq = 0;
            runtime.connection_id = connection_id;
            runtime.agent_version = hello
                .agent_version
                .clone()
                .or(runtime.agent_version.clone());
            runtime.hardware = hello.hardware.clone().or(runtime.hardware.clone());
            runtime.snapshot.hostname = hello.hostname;
            runtime.snapshot.agent_version = runtime.agent_version.clone();
            runtime.snapshot.hardware = runtime.hardware.clone();
        } else {
            let snapshot = NodeSnapshot {
                node_id: hello.node_id.clone(),
                hostname: hello.hostname.clone(),
                seq: 0,
                sampled_at: seen_at,
                received_at: Some(seen_at),
                refresh_interval: 1.0,
                process_interval: 5.0,
                status: "online".to_string(),
                source: "none".to_string(),
                gpus: vec![],
                totals: Default::default(),
                error: None,
                agent_version: hello.agent_version.clone(),
                driver_version: None,
                cuda_driver_version: None,
                nvml_version: None,
                elapsed_ms: 0.0,
                history: Default::default(),
                hardware: hello.hardware.clone(),
            };
            inner.latest_by_node.insert(
                hello.node_id.clone(),
                NodeRuntime {
                    node_id: hello.node_id,
                    hostname: hello.hostname,
                    snapshot,
                    last_seq: 0,
                    connected: true,
                    last_seen_at: seen_at,
                    agent_version: hello.agent_version,
                    connection_id,
                    hardware: hello.hardware,
                },
            );
        }
        Self::bump_locked(&mut inner);
        self.notify.notify_waiters();
    }

    pub fn ingest_sample(
        &self,
        message: &Value,
        received_at: Option<f64>,
        connection_id: Option<u64>,
    ) -> Result<bool, ClusterError> {
        let node_id = text(message.get("node_id")).trim().to_string();
        if node_id.is_empty() {
            return Err(ClusterError::MissingSampleNodeId);
        }
        let seq = int_value(message.get("seq")).unwrap_or(0);
        let mut inner = self.inner.write();
        if let Some(runtime) = inner.latest_by_node.get(&node_id) {
            if !connection_matches(runtime, connection_id) || seq <= runtime.last_seq {
                return Ok(false);
            }
        }

        let now = received_at.unwrap_or_else(unix_now);
        let runtime = inner.latest_by_node.get(&node_id).cloned();
        let mut snapshot = node_snapshot_from_agent_sample(
            message,
            now,
            runtime.as_ref().map(|runtime| runtime.hostname.as_str()),
            runtime
                .as_ref()
                .and_then(|runtime| runtime.agent_version.clone()),
            runtime
                .as_ref()
                .and_then(|runtime| runtime.hardware.clone()),
        )?;
        inner.history.update(&mut snapshot);
        inner.latest_by_node.insert(
            node_id.clone(),
            NodeRuntime {
                node_id,
                hostname: snapshot.hostname.clone(),
                last_seq: seq,
                connected: true,
                last_seen_at: now,
                agent_version: snapshot.agent_version.clone(),
                connection_id,
                hardware: snapshot.hardware.clone(),
                snapshot,
            },
        );
        Self::bump_locked(&mut inner);
        drop(inner);
        self.notify.notify_waiters();
        Ok(true)
    }

    pub fn ingest_heartbeat(
        &self,
        node_id: &str,
        seq: Option<i64>,
        now: Option<f64>,
        connection_id: Option<u64>,
    ) {
        if !self.inner.read().latest_by_node.contains_key(node_id) {
            self.register_hello(
                AgentHello {
                    node_id: node_id.to_string(),
                    hostname: node_id.to_string(),
                    agent_version: None,
                    capabilities: None,
                    hardware: None,
                },
                now,
                connection_id,
            );
        }
        let mut inner = self.inner.write();
        let Some(runtime) = inner.latest_by_node.get_mut(node_id) else {
            return;
        };
        if !connection_matches(runtime, connection_id) {
            return;
        }
        runtime.connected = true;
        runtime.last_seen_at = now.unwrap_or_else(unix_now);
        if let Some(seq) = seq {
            runtime.last_seq = runtime.last_seq.max(seq);
        }
        Self::bump_locked(&mut inner);
        drop(inner);
        self.notify.notify_waiters();
    }

    pub fn disconnect(&self, node_id: &str, now: Option<f64>, connection_id: Option<u64>) {
        let mut inner = self.inner.write();
        let Some(runtime) = inner.latest_by_node.get_mut(node_id) else {
            return;
        };
        if !connection_matches(runtime, connection_id) {
            return;
        }
        runtime.connected = false;
        if let Some(now) = now {
            runtime.last_seen_at = now;
        }
        Self::bump_locked(&mut inner);
        drop(inner);
        self.notify.notify_waiters();
    }

    pub fn snapshot(&self, now: Option<f64>) -> ClusterSnapshot {
        let inner = self.inner.read();
        let timestamp = now.unwrap_or_else(unix_now);
        let nodes = inner
            .latest_by_node
            .values()
            .cloned()
            .map(|runtime| runtime_snapshot(&inner, runtime, timestamp))
            .collect();
        cluster_snapshot_from_nodes(nodes, inner.seq, timestamp)
    }

    pub fn latest_node_snapshot(&self, node_id: &str) -> Option<NodeSnapshot> {
        self.inner
            .read()
            .latest_by_node
            .get(node_id)
            .map(|runtime| runtime.snapshot.clone())
    }

    pub fn local_node_id(&self) -> String {
        self.inner.read().local_node_id.clone()
    }

    fn bump_locked(inner: &mut ClusterInner) {
        inner.seq += 1;
    }
}

fn runtime_snapshot(inner: &ClusterInner, mut runtime: NodeRuntime, now: f64) -> NodeSnapshot {
    runtime.snapshot.status = status(inner, &runtime, now);
    runtime.snapshot.received_at = Some(runtime.last_seen_at);
    runtime.snapshot
}

fn status(inner: &ClusterInner, runtime: &NodeRuntime, now: f64) -> String {
    if !runtime.connected {
        return "offline".to_string();
    }
    let elapsed = now - runtime.last_seen_at;
    let refresh = runtime.snapshot.refresh_interval.max(0.1);
    let stale_after = inner
        .stale_after
        .unwrap_or_else(|| (3.0 * refresh).max(5.0));
    let offline_after = inner
        .offline_after
        .unwrap_or_else(|| (10.0 * refresh).max(30.0));
    if elapsed > offline_after {
        "offline".to_string()
    } else if elapsed > stale_after {
        "stale".to_string()
    } else if runtime.snapshot.error.is_some() {
        "error".to_string()
    } else {
        "online".to_string()
    }
}

fn connection_matches(runtime: &NodeRuntime, connection_id: Option<u64>) -> bool {
    connection_id.is_none()
        || runtime.connection_id.is_none()
        || connection_id == runtime.connection_id
}

#[derive(Debug, Clone)]
struct HistoryAccumulator {
    history_size: usize,
    history: BTreeMap<String, BTreeMap<String, VecDeque<f64>>>,
}

impl HistoryAccumulator {
    fn new(history_size: usize) -> Self {
        Self {
            history_size,
            history: BTreeMap::new(),
        }
    }

    fn update(&mut self, snapshot: &mut NodeSnapshot) {
        let history_size = self.history_size;
        for gpu in &mut snapshot.gpus {
            let gpu_id = gpu
                .gpu_id
                .clone()
                .unwrap_or_else(|| gpu_global_id(&snapshot.node_id, gpu));
            gpu.gpu_id = Some(gpu_id.clone());
            let series = self.series_for(&gpu_id);
            push_bounded(
                series.get_mut("gpu").unwrap(),
                gpu.utilization_gpu as f64,
                history_size,
            );
            push_bounded(
                series.get_mut("memory").unwrap(),
                gpu.memory_percent(),
                history_size,
            );
            push_bounded(
                series.get_mut("power").unwrap(),
                gpu.power_percent(),
                history_size,
            );
            push_bounded(
                series.get_mut("temperature").unwrap(),
                gpu.temperature_c as f64,
                history_size,
            );
        }
        snapshot.history = self.payload_for_node(snapshot);
    }

    fn payload_for_node(&self, snapshot: &NodeSnapshot) -> HistoryPayload {
        let mut payload = HistoryPayload::new();
        for gpu in &snapshot.gpus {
            let gpu_id = gpu
                .gpu_id
                .clone()
                .unwrap_or_else(|| gpu_global_id(&snapshot.node_id, gpu));
            if let Some(series) = self.history.get(&gpu_id) {
                payload.insert(
                    gpu_id,
                    series
                        .iter()
                        .map(|(name, values)| (name.clone(), values.iter().copied().collect()))
                        .collect(),
                );
            }
        }
        payload
    }

    fn series_for(&mut self, gpu_id: &str) -> &mut BTreeMap<String, VecDeque<f64>> {
        self.history.entry(gpu_id.to_string()).or_insert_with(|| {
            HISTORY_METRICS
                .into_iter()
                .map(|name| (name.to_string(), VecDeque::new()))
                .collect()
        })
    }
}

fn push_bounded(values: &mut VecDeque<f64>, value: f64, max_len: usize) {
    values.push_back(value);
    while values.len() > max_len {
        values.pop_front();
    }
}

pub fn parse_agent_hello(message: &Value) -> Result<AgentHello, ClusterError> {
    if message.get("type").and_then(Value::as_str) != Some("hello") {
        return Err(ClusterError::ExpectedHello);
    }
    let node_id = text(message.get("node_id")).trim().to_string();
    if node_id.is_empty() {
        return Err(ClusterError::MissingHelloNodeId);
    }
    Ok(AgentHello {
        hostname: text(message.get("hostname"))
            .trim()
            .to_string()
            .if_empty_then(node_id.clone()),
        node_id,
        agent_version: opt_text(message.get("agent_version")),
        capabilities: message
            .get("capabilities")
            .filter(|value| value.is_object())
            .cloned(),
        hardware: hardware_from_value(message.get("hardware")),
    })
}

pub fn node_snapshot_from_agent_sample(
    message: &Value,
    received_at: f64,
    hostname: Option<&str>,
    agent_version: Option<String>,
    hardware: Option<NodeHardware>,
) -> Result<NodeSnapshot, ClusterError> {
    if message.get("type").and_then(Value::as_str) != Some("sample") {
        return Err(ClusterError::ExpectedSample);
    }
    let node_id = text(message.get("node_id")).trim().to_string();
    if node_id.is_empty() {
        return Err(ClusterError::MissingSampleNodeId);
    }
    let Some(payload) = message.get("snapshot").filter(|value| value.is_object()) else {
        return Err(ClusterError::MissingSnapshot);
    };
    let mut gpus: Vec<GpuInfo> = payload
        .get("gpus")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| gpu_from_value(&node_id, item))
        .collect();
    for gpu in &mut gpus {
        gpu.node_id = Some(node_id.clone());
        gpu.gpu_id = Some(gpu_global_id(&node_id, gpu));
    }
    let totals = node_totals_from_gpus(&gpus);
    Ok(NodeSnapshot {
        node_id,
        hostname: text(payload.get("hostname"))
            .trim()
            .to_string()
            .if_empty_then(hostname.unwrap_or("").to_string())
            .if_empty_then(text(message.get("node_id")).to_string()),
        seq: int_value(message.get("seq"))
            .or_else(|| int_value(payload.get("seq")))
            .unwrap_or(0),
        sampled_at: float_value(message.get("sampled_at"))
            .or_else(|| float_value(payload.get("timestamp")))
            .unwrap_or(received_at),
        received_at: Some(received_at),
        refresh_interval: float_value(message.get("refresh_interval"))
            .or_else(|| float_value(payload.get("refresh_interval")))
            .unwrap_or(1.0),
        process_interval: float_value(message.get("process_interval"))
            .or_else(|| float_value(payload.get("process_interval")))
            .unwrap_or(5.0),
        status: if payload.get("ok").and_then(Value::as_bool).unwrap_or(true) {
            "online".to_string()
        } else {
            "error".to_string()
        },
        source: text(payload.get("source")).if_empty_then("none".to_string()),
        gpus,
        totals,
        error: opt_text(payload.get("error")),
        agent_version,
        driver_version: opt_text(payload.get("driver_version")),
        cuda_driver_version: opt_text(payload.get("cuda_driver_version")),
        nvml_version: opt_text(payload.get("nvml_version")),
        elapsed_ms: float_value(payload.get("elapsed_ms")).unwrap_or(0.0),
        history: Default::default(),
        hardware,
    })
}

fn hardware_from_value(payload: Option<&Value>) -> Option<NodeHardware> {
    let gpus: Vec<GpuHardwareInfo> = payload?
        .get("gpus")?
        .as_array()?
        .iter()
        .filter_map(|item| {
            Some(GpuHardwareInfo {
                index: int_value(item.get("index")).unwrap_or(0),
                uuid: text(item.get("uuid")).if_empty_then("unknown".to_string()),
                name: text(item.get("name")).if_empty_then("unknown".to_string()),
                architecture: opt_text(item.get("architecture")).filter(|value| !value.is_empty()),
            })
        })
        .collect();
    (!gpus.is_empty()).then_some(NodeHardware { gpus })
}

fn gpu_from_value(node_id: &str, data: &Value) -> Option<GpuInfo> {
    if !data.is_object() {
        return None;
    }
    let mut gpu = GpuInfo {
        index: int_value(data.get("index")).unwrap_or(0),
        node_id: Some(node_id.to_string()),
        uuid: text(data.get("uuid")).if_empty_then("unknown".to_string()),
        name: text(data.get("name")).if_empty_then("unknown".to_string()),
        pci_bus_id: opt_text(data.get("pci_bus_id")),
        utilization_gpu: int_value(data.get("utilization_gpu")).unwrap_or(0),
        utilization_mem: int_value(data.get("utilization_mem")).unwrap_or(0),
        memory_total_mb: int_value(data.get("memory_total_mb")).unwrap_or(0),
        memory_used_mb: int_value(data.get("memory_used_mb")).unwrap_or(0),
        memory_free_mb: int_value(data.get("memory_free_mb")).unwrap_or(0),
        temperature_c: int_value(data.get("temperature_c")).unwrap_or(0),
        power_watts: float_value(data.get("power_watts")).unwrap_or(0.0),
        power_limit_watts: float_value(data.get("power_limit_watts")).unwrap_or(0.0),
        clock_sm_mhz: int_value(data.get("clock_sm_mhz")),
        clock_mem_mhz: int_value(data.get("clock_mem_mhz")),
        max_clock_sm_mhz: int_value(data.get("max_clock_sm_mhz")),
        max_clock_mem_mhz: int_value(data.get("max_clock_mem_mhz")),
        pstate: opt_text(data.get("pstate")),
        compute_mode: opt_text(data.get("compute_mode")),
        mig_mode: opt_text(data.get("mig_mode")),
        ecc_mode: opt_text(data.get("ecc_mode")),
        processes: vec![],
        other_users: vec![],
        error: opt_text(data.get("error")),
        gpu_id: None,
    };
    gpu.gpu_id = Some(gpu_global_id(node_id, &gpu));
    gpu.processes = data
        .get("processes")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(process_from_value)
        .collect();
    gpu.other_users = data
        .get("other_users")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(other_user_from_value)
        .collect();
    Some(gpu)
}

fn process_from_value(data: &Value) -> Option<GpuProcess> {
    if !data.is_object() {
        return None;
    }
    Some(GpuProcess {
        pid: int_value(data.get("pid")).unwrap_or(0),
        name: text(data.get("name")).to_string(),
        gpu_memory_mb: int_value(data.get("gpu_memory_mb")).unwrap_or(0),
        ppid: int_value(data.get("ppid")),
        user: opt_text(data.get("user")),
        task_name: opt_text(data.get("task_name")),
        exe: opt_text(data.get("exe")),
        cmdline: opt_text(data.get("cmdline")),
        cmdline_hash: opt_text(data.get("cmdline_hash")),
        kind: text(data.get("kind")).if_empty_then("compute".to_string()),
        runtime_seconds: int_value(data.get("runtime_seconds")),
        process_start_time: float_value(data.get("process_start_time")),
        parent_start_time: float_value(data.get("parent_start_time")),
        detail_status: text(data.get("detail_status")).if_empty_then("unknown".to_string()),
        detail_error: opt_text(data.get("detail_error")),
    })
}

fn other_user_from_value(data: &Value) -> Option<OtherUserMemory> {
    if !data.is_object() {
        return None;
    }
    Some(OtherUserMemory {
        user: text(data.get("user")).to_string(),
        process_count: int_value(data.get("process_count")).unwrap_or(0),
        total_memory_mb: int_value(data.get("total_memory_mb")).unwrap_or(0),
        runtime_seconds: int_value(data.get("runtime_seconds")),
    })
}

fn text(value: Option<&Value>) -> &str {
    value.and_then(Value::as_str).unwrap_or("")
}

fn opt_text(value: Option<&Value>) -> Option<String> {
    value.and_then(Value::as_str).map(str::to_string)
}

fn int_value(value: Option<&Value>) -> Option<i64> {
    value.and_then(|value| value.as_i64().or_else(|| value.as_u64().map(|v| v as i64)))
}

fn float_value(value: Option<&Value>) -> Option<f64> {
    value
        .and_then(Value::as_f64)
        .or_else(|| int_value(value).map(|value| value as f64))
}

fn unix_now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .unwrap_or(0.0)
}

trait IfEmpty {
    fn if_empty_then(self, fallback: String) -> String;
}

impl IfEmpty for String {
    fn if_empty_then(self, fallback: String) -> String {
        if self.is_empty() {
            fallback
        } else {
            self
        }
    }
}

impl IfEmpty for &str {
    fn if_empty_then(self, fallback: String) -> String {
        if self.is_empty() {
            fallback
        } else {
            self.to_string()
        }
    }
}
