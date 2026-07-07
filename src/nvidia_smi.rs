use std::collections::BTreeMap;
use std::process::Command;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use thiserror::Error;

use crate::procfs;
use crate::schema::{cmdline_fingerprint, infer_task_name, GpuInfo, GpuProcess, Snapshot};

pub const GPU_QUERY_FIELDS: [&str; 18] = [
    "index",
    "uuid",
    "name",
    "pci.bus_id",
    "driver_version",
    "temperature.gpu",
    "utilization.gpu",
    "utilization.memory",
    "memory.total",
    "memory.used",
    "memory.free",
    "power.draw",
    "power.limit",
    "clocks.sm",
    "clocks.mem",
    "pstate",
    "compute_mode",
    "mig.mode.current",
];

#[derive(Debug, Error)]
pub enum NvidiaSmiError {
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error("nvidia-smi failed: {0}")]
    CommandFailed(String),
}

pub fn parse_gpu_query_csv(output: &str) -> (Vec<GpuInfo>, Option<String>) {
    let mut gpus = Vec::new();
    let mut driver_version = None;
    for row in csv_rows(output) {
        if row.is_empty() || row.iter().all(|cell| cell.trim().is_empty()) {
            continue;
        }
        let value = |index: usize| row.get(index).map(String::as_str).unwrap_or("");
        driver_version = driver_version.or_else(|| clean(value(4)));
        gpus.push(GpuInfo {
            index: to_int(value(0), 0),
            uuid: clean(value(1)).unwrap_or_else(|| "unknown".to_string()),
            name: clean(value(2)).unwrap_or_else(|| "unknown".to_string()),
            pci_bus_id: clean(value(3)),
            utilization_gpu: to_int(value(6), 0),
            utilization_mem: to_int(value(7), 0),
            memory_total_mb: to_int(value(8), 0),
            memory_used_mb: to_int(value(9), 0),
            memory_free_mb: to_int(value(10), 0),
            temperature_c: to_int(value(5), 0),
            power_watts: round1(to_float(value(11), 0.0)),
            power_limit_watts: round1(to_float(value(12), 0.0)),
            clock_sm_mhz: nonzero_int(value(13)),
            clock_mem_mhz: nonzero_int(value(14)),
            pstate: clean(value(15)),
            compute_mode: clean(value(16)),
            mig_mode: clean(value(17)),
            ..Default::default()
        });
    }
    (gpus, driver_version)
}

pub fn parse_process_query_csv(output: &str) -> BTreeMap<String, Vec<GpuProcess>> {
    parse_process_query_csv_with_details(output, |pid| {
        let (cmdline, detail_status) = procfs::process_cmdline(pid);
        let ppid = procfs::process_parent_pid(pid);
        ProcessDetails {
            ppid,
            user: procfs::process_user(pid),
            cmdline,
            exe: procfs::process_exe(pid),
            runtime_seconds: procfs::process_runtime_seconds(pid),
            process_start_time: procfs::process_start_time_seconds(pid),
            parent_start_time: ppid.and_then(procfs::process_start_time_seconds),
            detail_status,
        }
    })
}

pub fn parse_process_query_csv_with_details<F>(
    output: &str,
    mut details: F,
) -> BTreeMap<String, Vec<GpuProcess>>
where
    F: FnMut(i64) -> ProcessDetails,
{
    let mut result: BTreeMap<String, Vec<GpuProcess>> = BTreeMap::new();
    for row in csv_rows(output) {
        if row.len() < 4 {
            continue;
        }
        let uuid = clean(&row[0]).unwrap_or_else(|| "unknown".to_string());
        let pid = to_int(&row[1], 0);
        let name = clean(&row[2]).unwrap_or_else(|| "?".to_string());
        let mut process = GpuProcess {
            pid,
            name: name.clone(),
            gpu_memory_mb: to_int(&row[3], 0),
            kind: "compute".to_string(),
            ..Default::default()
        };
        if pid != 0 {
            let detail = details(pid);
            process.ppid = detail.ppid;
            process.user = detail.user;
            process.cmdline = detail.cmdline;
            process.cmdline_hash = cmdline_fingerprint(process.cmdline.as_deref());
            process.exe = detail.exe;
            process.runtime_seconds = detail.runtime_seconds;
            process.process_start_time = detail.process_start_time;
            process.parent_start_time = detail.parent_start_time;
            process.detail_status = detail.detail_status;
            process.task_name = Some(infer_task_name(
                process.cmdline.as_deref(),
                process.exe.as_deref(),
                None,
                Some(&name),
                Some(pid),
            ));
        }
        result.entry(uuid).or_default().push(process);
    }
    result
}

#[derive(Debug, Clone, Default)]
pub struct ProcessDetails {
    pub ppid: Option<i64>,
    pub user: Option<String>,
    pub cmdline: Option<String>,
    pub exe: Option<String>,
    pub runtime_seconds: Option<i64>,
    pub process_start_time: Option<f64>,
    pub parent_start_time: Option<f64>,
    pub detail_status: String,
}

pub fn sample(collect_processes: bool) -> Result<Snapshot, NvidiaSmiError> {
    let started = Instant::now();
    let gpu_output = run_nvidia_smi(&[
        format!("--query-gpu={}", GPU_QUERY_FIELDS.join(",")),
        "--format=csv,noheader,nounits".to_string(),
    ])?;
    let (mut gpus, driver_version) = parse_gpu_query_csv(&gpu_output);
    let processes_by_uuid = if collect_processes {
        run_nvidia_smi(&[
            "--query-compute-apps=gpu_uuid,pid,process_name,used_memory".to_string(),
            "--format=csv,noheader,nounits".to_string(),
        ])
        .map(|output| parse_process_query_csv(&output))
        .unwrap_or_default()
    } else {
        BTreeMap::new()
    };
    for gpu in &mut gpus {
        gpu.processes = processes_by_uuid
            .get(&gpu.uuid)
            .cloned()
            .unwrap_or_default();
    }
    Ok(Snapshot {
        ok: true,
        source: "nvidia-smi".to_string(),
        hostname: std::env::var("HOSTNAME").unwrap_or_else(|_| "local".to_string()),
        timestamp: unix_now(),
        elapsed_ms: round1(started.elapsed().as_secs_f64() * 1000.0),
        gpus,
        driver_version,
        cuda_driver_version: None,
        nvml_version: None,
        error: None,
        seq: 0,
        refresh_interval: 1.0,
        history: Default::default(),
    })
}

pub fn error_snapshot(error: impl Into<String>, source: impl Into<String>) -> Snapshot {
    Snapshot {
        ok: false,
        source: source.into(),
        hostname: std::env::var("HOSTNAME").unwrap_or_else(|_| "local".to_string()),
        timestamp: unix_now(),
        elapsed_ms: 0.0,
        gpus: vec![],
        driver_version: None,
        cuda_driver_version: None,
        nvml_version: None,
        error: Some(error.into()),
        seq: 0,
        refresh_interval: 1.0,
        history: Default::default(),
    }
}

fn run_nvidia_smi(args: &[String]) -> Result<String, NvidiaSmiError> {
    let output = Command::new("nvidia-smi").args(args).output()?;
    if !output.status.success() {
        return Err(NvidiaSmiError::CommandFailed(
            String::from_utf8_lossy(&output.stderr).to_string(),
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

fn csv_rows(output: &str) -> Vec<Vec<String>> {
    output
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| {
            line.split(',')
                .map(|cell| cell.trim().to_string())
                .collect()
        })
        .collect()
}

fn clean(value: &str) -> Option<String> {
    let value = value.trim();
    if value.is_empty()
        || matches!(
            value.to_uppercase().as_str(),
            "N/A" | "[N/A]" | "NOT SUPPORTED"
        )
    {
        None
    } else {
        Some(value.to_string())
    }
}

fn to_int(value: &str, default: i64) -> i64 {
    clean(value)
        .and_then(|value| value.parse::<f64>().ok())
        .map(|value| value as i64)
        .unwrap_or(default)
}

fn nonzero_int(value: &str) -> Option<i64> {
    match to_int(value, 0) {
        0 => None,
        value => Some(value),
    }
}

fn to_float(value: &str, default: f64) -> f64 {
    clean(value)
        .and_then(|value| value.parse::<f64>().ok())
        .unwrap_or(default)
}

fn round1(value: f64) -> f64 {
    (value * 10.0).round() / 10.0
}

fn unix_now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .unwrap_or(0.0)
}
