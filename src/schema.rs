use serde::ser::SerializeStruct;
use serde::{Deserialize, Serialize, Serializer};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct GpuProcess {
    pub pid: i64,
    pub name: String,
    pub gpu_memory_mb: i64,
    pub ppid: Option<i64>,
    pub user: Option<String>,
    pub task_name: Option<String>,
    pub exe: Option<String>,
    pub cmdline: Option<String>,
    pub cmdline_hash: Option<String>,
    #[serde(default = "default_process_kind")]
    pub kind: String,
    pub runtime_seconds: Option<i64>,
    pub process_start_time: Option<f64>,
    pub parent_start_time: Option<f64>,
    #[serde(default = "default_detail_status")]
    pub detail_status: String,
    pub detail_error: Option<String>,
}

impl Default for GpuProcess {
    fn default() -> Self {
        Self {
            pid: 0,
            name: String::new(),
            gpu_memory_mb: 0,
            ppid: None,
            user: None,
            task_name: None,
            exe: None,
            cmdline: None,
            cmdline_hash: None,
            kind: default_process_kind(),
            runtime_seconds: None,
            process_start_time: None,
            parent_start_time: None,
            detail_status: default_detail_status(),
            detail_error: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq)]
pub struct OtherUserMemory {
    pub user: String,
    pub process_count: i64,
    pub total_memory_mb: i64,
    pub runtime_seconds: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct GpuHardwareInfo {
    pub index: i64,
    #[serde(default = "unknown")]
    pub uuid: String,
    #[serde(default = "unknown")]
    pub name: String,
    pub architecture: Option<String>,
}

impl Default for GpuHardwareInfo {
    fn default() -> Self {
        Self {
            index: 0,
            uuid: unknown(),
            name: unknown(),
            architecture: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq)]
pub struct NodeHardware {
    #[serde(default)]
    pub gpus: Vec<GpuHardwareInfo>,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct GpuInfo {
    pub index: i64,
    pub node_id: Option<String>,
    pub gpu_id: Option<String>,
    #[serde(default = "unknown")]
    pub uuid: String,
    #[serde(default = "unknown")]
    pub name: String,
    pub pci_bus_id: Option<String>,
    #[serde(default)]
    pub utilization_gpu: i64,
    #[serde(default)]
    pub utilization_mem: i64,
    #[serde(default)]
    pub memory_total_mb: i64,
    #[serde(default)]
    pub memory_used_mb: i64,
    #[serde(default)]
    pub memory_free_mb: i64,
    #[serde(default)]
    pub temperature_c: i64,
    #[serde(default)]
    pub power_watts: f64,
    #[serde(default)]
    pub power_limit_watts: f64,
    pub clock_sm_mhz: Option<i64>,
    pub clock_mem_mhz: Option<i64>,
    pub max_clock_sm_mhz: Option<i64>,
    pub max_clock_mem_mhz: Option<i64>,
    pub pstate: Option<String>,
    pub compute_mode: Option<String>,
    pub mig_mode: Option<String>,
    pub ecc_mode: Option<String>,
    #[serde(default)]
    pub processes: Vec<GpuProcess>,
    #[serde(default)]
    pub other_users: Vec<OtherUserMemory>,
    pub error: Option<String>,
}

impl Default for GpuInfo {
    fn default() -> Self {
        Self {
            index: 0,
            node_id: None,
            gpu_id: None,
            uuid: unknown(),
            name: unknown(),
            pci_bus_id: None,
            utilization_gpu: 0,
            utilization_mem: 0,
            memory_total_mb: 0,
            memory_used_mb: 0,
            memory_free_mb: 0,
            temperature_c: 0,
            power_watts: 0.0,
            power_limit_watts: 0.0,
            clock_sm_mhz: None,
            clock_mem_mhz: None,
            max_clock_sm_mhz: None,
            max_clock_mem_mhz: None,
            pstate: None,
            compute_mode: None,
            mig_mode: None,
            ecc_mode: None,
            processes: vec![],
            other_users: vec![],
            error: None,
        }
    }
}

impl GpuInfo {
    pub fn memory_percent(&self) -> f64 {
        if self.memory_total_mb <= 0 {
            0.0
        } else {
            round1((self.memory_used_mb as f64 / self.memory_total_mb as f64) * 100.0)
        }
    }

    pub fn power_percent(&self) -> f64 {
        if self.power_limit_watts <= 0.0 {
            0.0
        } else {
            round1((self.power_watts / self.power_limit_watts) * 100.0)
        }
    }
}

impl Serialize for GpuInfo {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        let mut state = serializer.serialize_struct("GpuInfo", 27)?;
        state.serialize_field("index", &self.index)?;
        state.serialize_field("node_id", &self.node_id)?;
        state.serialize_field("gpu_id", &self.gpu_id)?;
        state.serialize_field("uuid", &self.uuid)?;
        state.serialize_field("name", &self.name)?;
        state.serialize_field("pci_bus_id", &self.pci_bus_id)?;
        state.serialize_field("utilization_gpu", &self.utilization_gpu)?;
        state.serialize_field("utilization_mem", &self.utilization_mem)?;
        state.serialize_field("memory_total_mb", &self.memory_total_mb)?;
        state.serialize_field("memory_used_mb", &self.memory_used_mb)?;
        state.serialize_field("memory_free_mb", &self.memory_free_mb)?;
        state.serialize_field("memory_percent", &self.memory_percent())?;
        state.serialize_field("temperature_c", &self.temperature_c)?;
        state.serialize_field("power_watts", &self.power_watts)?;
        state.serialize_field("power_limit_watts", &self.power_limit_watts)?;
        state.serialize_field("power_percent", &self.power_percent())?;
        state.serialize_field("clock_sm_mhz", &self.clock_sm_mhz)?;
        state.serialize_field("clock_mem_mhz", &self.clock_mem_mhz)?;
        state.serialize_field("max_clock_sm_mhz", &self.max_clock_sm_mhz)?;
        state.serialize_field("max_clock_mem_mhz", &self.max_clock_mem_mhz)?;
        state.serialize_field("pstate", &self.pstate)?;
        state.serialize_field("compute_mode", &self.compute_mode)?;
        state.serialize_field("mig_mode", &self.mig_mode)?;
        state.serialize_field("ecc_mode", &self.ecc_mode)?;
        state.serialize_field("processes", &self.processes)?;
        state.serialize_field("other_users", &self.other_users)?;
        state.serialize_field("error", &self.error)?;
        state.end()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Snapshot {
    pub ok: bool,
    pub source: String,
    pub hostname: String,
    pub timestamp: f64,
    pub elapsed_ms: f64,
    #[serde(default)]
    pub gpus: Vec<GpuInfo>,
    pub driver_version: Option<String>,
    pub cuda_driver_version: Option<String>,
    pub nvml_version: Option<String>,
    pub error: Option<String>,
    #[serde(default)]
    pub seq: i64,
    #[serde(default = "default_refresh")]
    pub refresh_interval: f64,
    #[serde(default)]
    pub history: HistoryPayload,
}

impl Snapshot {
    pub fn totals(&self) -> NodeTotals {
        node_totals_from_gpus(&self.gpus)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq)]
pub struct NodeTotals {
    #[serde(default)]
    pub gpu_count: i64,
    #[serde(default)]
    pub active_processes: i64,
    #[serde(default)]
    pub avg_gpu_utilization: f64,
    #[serde(default)]
    pub avg_memory_utilization: f64,
    #[serde(default)]
    pub memory_used_mb: i64,
    #[serde(default)]
    pub memory_total_mb: i64,
    #[serde(default)]
    pub power_watts: f64,
    #[serde(default)]
    pub power_limit_watts: f64,
    #[serde(default)]
    pub max_temperature_c: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct NodeSnapshot {
    pub node_id: String,
    pub hostname: String,
    pub seq: i64,
    pub sampled_at: f64,
    pub received_at: Option<f64>,
    pub refresh_interval: f64,
    pub process_interval: f64,
    pub status: String,
    pub source: String,
    #[serde(default)]
    pub gpus: Vec<GpuInfo>,
    pub totals: NodeTotals,
    pub error: Option<String>,
    pub agent_version: Option<String>,
    pub driver_version: Option<String>,
    pub cuda_driver_version: Option<String>,
    pub nvml_version: Option<String>,
    pub elapsed_ms: f64,
    #[serde(default)]
    pub history: HistoryPayload,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hardware: Option<NodeHardware>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq)]
pub struct ClusterTotals {
    #[serde(flatten)]
    pub node: NodeTotals,
    #[serde(default)]
    pub node_count: i64,
    #[serde(default)]
    pub online_node_count: i64,
    #[serde(default)]
    pub stale_node_count: i64,
    #[serde(default)]
    pub offline_node_count: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ClusterSnapshot {
    pub ok: bool,
    pub seq: u64,
    pub timestamp: f64,
    #[serde(default)]
    pub nodes: Vec<NodeSnapshot>,
    pub totals: ClusterTotals,
    #[serde(default)]
    pub history: HistoryPayload,
}

pub type HistoryPayload = BTreeMap<String, BTreeMap<String, Vec<f64>>>;

pub fn process_session_id(node_id: &str, process: &GpuProcess) -> String {
    let started = process
        .process_start_time
        .map(|value| format!("{value:.6}"))
        .unwrap_or_else(|| "unknown".to_string());
    format!("{}:{}:{}", node_id, process.pid, started)
}

pub fn cmdline_fingerprint(cmdline: Option<&str>) -> Option<String> {
    let cmdline = cmdline.filter(|value| !value.is_empty())?;
    let mut hasher = Sha256::new();
    hasher.update(cmdline.as_bytes());
    let digest = hasher.finalize();
    Some(format!("{digest:x}")[..16].to_string())
}

pub fn gpu_global_id(node_id: &str, gpu: &GpuInfo) -> String {
    if !gpu.uuid.is_empty() && gpu.uuid != "unknown" {
        format!("{}:{}", node_id, gpu.uuid)
    } else {
        format!("{}:index:{}", node_id, gpu.index)
    }
}

pub fn local_node_id(default: Option<&str>) -> String {
    std::env::var("CONSTELLA_NODE_ID")
        .ok()
        .filter(|value| !value.is_empty())
        .or_else(|| std::env::var("CONSTELLA_MANAGER_HOSTNAME").ok())
        .filter(|value| !value.is_empty())
        .or_else(|| default.map(str::to_string))
        .or_else(|| std::env::var("HOSTNAME").ok())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "local".to_string())
}

pub fn local_hostname(default: Option<&str>) -> String {
    std::env::var("CONSTELLA_MANAGER_HOSTNAME")
        .ok()
        .filter(|value| !value.is_empty())
        .or_else(|| default.map(str::to_string))
        .or_else(|| std::env::var("HOSTNAME").ok())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "local".to_string())
}

pub fn node_totals_from_gpus(gpus: &[GpuInfo]) -> NodeTotals {
    let gpu_count = gpus.len() as i64;
    let memory_total_mb = gpus.iter().map(|gpu| gpu.memory_total_mb).sum();
    let memory_used_mb = gpus.iter().map(|gpu| gpu.memory_used_mb).sum();
    let power_limit_watts = gpus.iter().map(|gpu| gpu.power_limit_watts).sum::<f64>();
    let power_watts = gpus.iter().map(|gpu| gpu.power_watts).sum::<f64>();
    let active_processes = gpus
        .iter()
        .map(|gpu| {
            gpu.processes.len() as i64
                + gpu
                    .other_users
                    .iter()
                    .map(|other| other.process_count)
                    .sum::<i64>()
        })
        .sum();
    NodeTotals {
        gpu_count,
        active_processes,
        avg_gpu_utilization: if gpu_count > 0 {
            round1(
                gpus.iter().map(|gpu| gpu.utilization_gpu).sum::<i64>() as f64 / gpu_count as f64,
            )
        } else {
            0.0
        },
        avg_memory_utilization: if memory_total_mb > 0 {
            round1(memory_used_mb as f64 / memory_total_mb as f64 * 100.0)
        } else {
            0.0
        },
        memory_used_mb,
        memory_total_mb,
        power_watts: round1(power_watts),
        power_limit_watts: round1(power_limit_watts),
        max_temperature_c: gpus.iter().map(|gpu| gpu.temperature_c).max().unwrap_or(0),
    }
}

pub fn snapshot_to_node_snapshot(
    snapshot: &Snapshot,
    node_id: Option<&str>,
    hostname: Option<&str>,
    received_at: Option<f64>,
    process_interval: f64,
    status: Option<&str>,
    agent_version: Option<String>,
) -> NodeSnapshot {
    let resolved_node_id = node_id
        .map(str::to_string)
        .unwrap_or_else(|| local_node_id(Some(&snapshot.hostname)));
    let resolved_hostname = hostname.map(str::to_string).unwrap_or_else(|| {
        if node_id.is_none() {
            local_hostname(Some(&snapshot.hostname))
        } else {
            snapshot.hostname.clone()
        }
    });
    let mut gpus = snapshot.gpus.clone();
    let mut history = HistoryPayload::new();
    for gpu in &mut gpus {
        gpu.node_id = Some(resolved_node_id.clone());
        let gpu_id = gpu_global_id(&resolved_node_id, gpu);
        gpu.gpu_id = Some(gpu_id.clone());
        if let Some(series) = snapshot.history.get(&gpu.index.to_string()) {
            history.insert(gpu_id, series.clone());
        }
    }
    let totals = node_totals_from_gpus(&gpus);
    NodeSnapshot {
        node_id: resolved_node_id,
        hostname: resolved_hostname,
        seq: snapshot.seq,
        sampled_at: snapshot.timestamp,
        received_at,
        refresh_interval: snapshot.refresh_interval,
        process_interval,
        status: status
            .map(str::to_string)
            .unwrap_or_else(|| if snapshot.ok { "online" } else { "error" }.to_string()),
        source: snapshot.source.clone(),
        gpus,
        totals,
        error: snapshot.error.clone(),
        agent_version,
        driver_version: snapshot.driver_version.clone(),
        cuda_driver_version: snapshot.cuda_driver_version.clone(),
        nvml_version: snapshot.nvml_version.clone(),
        elapsed_ms: snapshot.elapsed_ms,
        history,
        hardware: None,
    }
}

pub fn cluster_totals_from_nodes(nodes: &[NodeSnapshot]) -> ClusterTotals {
    let online_gpus: Vec<GpuInfo> = nodes
        .iter()
        .filter(|node| node.status != "offline")
        .flat_map(|node| node.gpus.clone())
        .collect();
    ClusterTotals {
        node: node_totals_from_gpus(&online_gpus),
        node_count: nodes.len() as i64,
        online_node_count: nodes.iter().filter(|node| node.status == "online").count() as i64,
        stale_node_count: nodes.iter().filter(|node| node.status == "stale").count() as i64,
        offline_node_count: nodes.iter().filter(|node| node.status == "offline").count() as i64,
    }
}

pub fn cluster_snapshot_from_nodes(
    mut nodes: Vec<NodeSnapshot>,
    seq: u64,
    timestamp: f64,
) -> ClusterSnapshot {
    nodes.sort_by(|left, right| left.node_id.cmp(&right.node_id));
    let mut history = HistoryPayload::new();
    for node in &nodes {
        history.extend(node.history.clone());
    }
    ClusterSnapshot {
        ok: nodes.iter().any(|node| node.status == "online"),
        seq,
        timestamp,
        totals: cluster_totals_from_nodes(&nodes),
        nodes,
        history,
    }
}

pub fn infer_task_name(
    cmdline: Option<&str>,
    exe: Option<&str>,
    comm: Option<&str>,
    process_name: Option<&str>,
    pid: Option<i64>,
) -> String {
    if let Some(cmdline) = cmdline.filter(|value| !value.trim().is_empty()) {
        let parts: Vec<&str> = cmdline.split_whitespace().collect();
        for part in &parts {
            let basename = Path::new(part)
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or(part);
            if [".py", ".sh", ".pl", ".R", ".ipynb"]
                .iter()
                .any(|suffix| basename.ends_with(suffix))
            {
                return basename.to_string();
            }
        }
        for part in &parts {
            let basename = Path::new(part)
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or(part);
            if matches!(
                basename,
                "torchrun" | "accelerate" | "python" | "python3" | "uvicorn"
            ) {
                return basename.to_string();
            }
        }
        if let Some(first) = parts.first() {
            return Path::new(first)
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or(first)
                .to_string();
        }
    }
    exe.and_then(|value| Path::new(value).file_name()?.to_str().map(str::to_string))
        .or_else(|| comm.map(str::to_string))
        .or_else(|| {
            process_name
                .and_then(|value| Path::new(value).file_name()?.to_str().map(str::to_string))
        })
        .unwrap_or_else(|| {
            pid.map(|pid| format!("unknown:{pid}"))
                .unwrap_or_else(|| "unknown".to_string())
        })
}

fn round1(value: f64) -> f64 {
    (value * 10.0).round() / 10.0
}

fn unknown() -> String {
    "unknown".to_string()
}

fn default_process_kind() -> String {
    "compute".to_string()
}

fn default_detail_status() -> String {
    "unknown".to_string()
}

fn default_refresh() -> f64 {
    1.0
}
