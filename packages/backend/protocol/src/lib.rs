use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

pub const VERSION: u32 = 1;
pub const REFRESH_INTERVALS: [f64; 4] = [0.5, 1.0, 2.0, 5.0];
pub const MAX_SAMPLE_BYTES: usize = 4 * 1024 * 1024;

pub const NVIDIA_GPM_PROFILE: &str = "nvidia.gpm.v1";
pub const NVIDIA_GPM_METRICS: [(u32, &str); 11] = [
    (2, "nvidia.gpm.sm_active"),
    (3, "nvidia.gpm.sm_occupancy"),
    (5, "nvidia.gpm.tensor_active"),
    (10, "nvidia.gpm.dram_bw_active"),
    (13, "nvidia.gpm.fp16_non_tensor_active"),
    (12, "nvidia.gpm.fp32_non_tensor_active"),
    (11, "nvidia.gpm.fp64_non_tensor_active"),
    (20, "nvidia.gpm.pcie_tx_per_second"),
    (21, "nvidia.gpm.pcie_rx_per_second"),
    (61, "nvidia.gpm.nvlink_tx_per_second"),
    (60, "nvidia.gpm.nvlink_rx_per_second"),
];

pub fn unix_time() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

pub fn token_from_env(name: &str) -> std::io::Result<Option<String>> {
    if let Ok(token) = std::env::var(name)
        && !token.is_empty()
    {
        return Ok(Some(token));
    }
    std::env::var_os(format!("{name}_FILE"))
        .map(|path| std::fs::read_to_string(path).map(|token| token.trim().to_owned()))
        .transpose()
        .map(|token| token.filter(|token| !token.is_empty()))
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
pub struct Settings {
    pub refresh_interval: f64,
    pub process_interval: f64,
}

impl Settings {
    pub fn new(refresh_interval: f64, process_interval: f64) -> Option<Self> {
        if !REFRESH_INTERVALS.contains(&refresh_interval) || !process_interval.is_finite() {
            return None;
        }
        Some(Self {
            refresh_interval,
            process_interval: process_interval.max(1.0),
        })
    }

    pub fn from_env() -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let refresh = std::env::var("CONSTELLA_REFRESH_SECONDS").unwrap_or_else(|_| "1".into());
        let process = std::env::var("CONSTELLA_PROCESS_SECONDS").unwrap_or_else(|_| "5".into());
        Self::new(refresh.parse()?, process.parse()?)
            .ok_or_else(|| "invalid sampling intervals".into())
    }

    pub fn effective(self) -> Self {
        Self {
            process_interval: self.process_interval.max(self.refresh_interval),
            ..self
        }
    }
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum AgentMessage {
    Hello {
        version: u32,
        node_id: String,
        hostname: String,
        agent_version: String,
        #[serde(default)]
        instance_id: Option<String>,
    },
    Sample {
        seq: u64,
        #[serde(flatten)]
        sample: Box<Sample>,
    },
    ControlResponse {
        id: u64,
        response: ControlResponse,
    },
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ManagerMessage {
    Config {
        #[serde(flatten)]
        settings: Settings,
    },
    Control {
        id: u64,
        request: ControlRequest,
    },
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(tag = "action", rename_all = "snake_case")]
pub enum ControlRequest {
    AccountLookup { username: String },
    Restart,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(tag = "result", rename_all = "snake_case")]
pub enum ControlResponse {
    Account { account: Option<Account> },
    RestartAccepted,
    Rejected,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Account {
    pub canonical_username: String,
    pub uid: u32,
    pub gid: u32,
    pub shell: String,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct Sample {
    pub sampled_at: f64,
    pub elapsed_ms: f64,
    pub source: String,
    pub error: Option<String>,
    pub driver_version: Option<String>,
    pub cuda_driver_version: Option<String>,
    pub nvml_version: Option<String>,
    pub gpus: Vec<Gpu>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct Gpu {
    pub index: u32,
    pub device_type: String,
    pub card_id: Option<String>,
    pub die_id: Option<u32>,
    pub uuid: String,
    pub name: String,
    pub architecture: Option<String>,
    pub pci_bus_id: Option<String>,
    pub utilization_gpu: u32,
    pub utilization_mem: u32,
    pub memory_total_mb: u64,
    pub memory_used_mb: u64,
    pub memory_free_mb: u64,
    pub temperature_c: u32,
    pub power_watts: f64,
    pub power_limit_watts: f64,
    pub clock_sm_mhz: Option<u32>,
    pub clock_mem_mhz: Option<u32>,
    pub max_clock_sm_mhz: Option<u32>,
    pub max_clock_mem_mhz: Option<u32>,
    pub pstate: Option<String>,
    pub compute_mode: Option<String>,
    pub mig_mode: Option<String>,
    pub ecc_mode: Option<String>,
    pub error: Option<String>,
    pub telemetry_errors: BTreeMap<String, String>,
    pub processes: Vec<Process>,
    #[serde(default)]
    pub other_users: Vec<OtherUser>,
    pub performance: Option<Performance>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct OtherUser {
    pub user: String,
    pub process_count: usize,
    pub total_memory_mb: u64,
    pub runtime_seconds: Option<u64>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct Process {
    pub pid: u32,
    pub name: String,
    pub gpu_memory_mb: u64,
    pub kind: String,
    pub ppid: Option<u32>,
    pub user_uid: Option<u32>,
    pub user: Option<String>,
    pub task_name: Option<String>,
    pub exe: Option<String>,
    pub cmdline: Option<String>,
    pub cmdline_hash: Option<String>,
    pub process_start_time: Option<f64>,
    pub parent_start_time: Option<f64>,
    pub runtime_seconds: Option<u64>,
    pub detail_status: String,
    pub detail_error: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Performance {
    pub profile: String,
    pub status: String,
    pub sampled_at: f64,
    pub interval_ms: Option<f64>,
    pub metrics: BTreeMap<String, f64>,
    pub supported_metrics: Vec<String>,
    pub invalid_metrics: Vec<String>,
    pub error: Option<String>,
}
