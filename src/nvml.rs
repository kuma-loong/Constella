use std::time::{Instant, SystemTime, UNIX_EPOCH};

use nvml_wrapper::enum_wrappers::device::{
    Clock, ComputeMode, PerformanceState, TemperatureSensor,
};
use nvml_wrapper::enums::device::DeviceArchitecture;
use nvml_wrapper::enums::device::UsedGpuMemory;
use nvml_wrapper::error::NvmlError;
use nvml_wrapper::Nvml;

use crate::procfs;
use crate::schema::{
    cmdline_fingerprint, infer_task_name, GpuHardwareInfo, GpuInfo, GpuProcess, NodeHardware,
    Snapshot,
};

#[derive(Debug)]
pub struct NvmlSampler {
    nvml: Nvml,
}

impl NvmlSampler {
    pub fn new() -> Result<Self, NvmlError> {
        Ok(Self {
            nvml: Nvml::init()?,
        })
    }

    pub fn sample(&self, collect_processes: bool) -> Result<Snapshot, NvmlError> {
        let started = Instant::now();
        let count = self.nvml.device_count()?;
        let driver_version = self.nvml.sys_driver_version().ok();
        let nvml_version = self.nvml.sys_nvml_version().ok();
        let cuda_driver_version = self
            .nvml
            .sys_cuda_driver_version()
            .ok()
            .map(cuda_version_string);
        let mut gpus = Vec::with_capacity(count as usize);
        for index in 0..count {
            let device = self.nvml.device_by_index(index)?;
            let memory = device.memory_info()?;
            let utilization = device.utilization_rates()?;
            let uuid = device.uuid().unwrap_or_else(|_| "unknown".to_string());
            let processes = if collect_processes {
                nvml_processes(&self.nvml, &device).unwrap_or_default()
            } else {
                Vec::new()
            };
            gpus.push(GpuInfo {
                index: index as i64,
                uuid,
                name: device.name().unwrap_or_else(|_| "unknown".to_string()),
                pci_bus_id: device.pci_info().ok().map(|pci| pci.bus_id),
                utilization_gpu: utilization.gpu as i64,
                utilization_mem: utilization.memory as i64,
                memory_total_mb: bytes_to_mib(memory.total),
                memory_used_mb: bytes_to_mib(memory.used),
                memory_free_mb: bytes_to_mib(memory.free),
                temperature_c: device
                    .temperature(TemperatureSensor::Gpu)
                    .ok()
                    .map(i64::from)
                    .unwrap_or(0),
                power_watts: milliwatts_to_watts(device.power_usage().ok()),
                power_limit_watts: milliwatts_to_watts(device.enforced_power_limit().ok()),
                clock_sm_mhz: device.clock_info(Clock::SM).ok().map(i64::from),
                clock_mem_mhz: device.clock_info(Clock::Memory).ok().map(i64::from),
                max_clock_sm_mhz: device.max_clock_info(Clock::SM).ok().map(i64::from),
                max_clock_mem_mhz: device.max_clock_info(Clock::Memory).ok().map(i64::from),
                pstate: device.performance_state().ok().map(performance_state_label),
                compute_mode: device.compute_mode().ok().map(compute_mode_label),
                mig_mode: device
                    .mig_mode()
                    .ok()
                    .map(|mode| enabled_label(mode.current != 0)),
                ecc_mode: device
                    .is_ecc_enabled()
                    .ok()
                    .map(|mode| enabled_label(mode.currently_enabled)),
                processes,
                ..Default::default()
            });
        }
        Ok(Snapshot {
            ok: true,
            source: "nvml".to_string(),
            hostname: std::env::var("HOSTNAME").unwrap_or_else(|_| "local".to_string()),
            timestamp: unix_now(),
            elapsed_ms: round1(started.elapsed().as_secs_f64() * 1000.0),
            gpus,
            driver_version,
            cuda_driver_version,
            nvml_version,
            error: None,
            seq: 0,
            refresh_interval: 1.0,
            history: Default::default(),
        })
    }

    pub fn hardware_inventory(&self) -> Result<NodeHardware, NvmlError> {
        let count = self.nvml.device_count()?;
        let mut gpus = Vec::with_capacity(count as usize);
        for index in 0..count {
            let device = self.nvml.device_by_index(index)?;
            gpus.push(GpuHardwareInfo {
                index: index as i64,
                uuid: device.uuid().unwrap_or_else(|_| "unknown".to_string()),
                name: device.name().unwrap_or_else(|_| "unknown".to_string()),
                architecture: device.architecture().ok().and_then(architecture_label),
            });
        }
        Ok(NodeHardware { gpus })
    }
}

pub fn sample_hardware_inventory() -> Option<NodeHardware> {
    NvmlSampler::new()
        .and_then(|sampler| sampler.hardware_inventory())
        .ok()
}

pub fn bytes_to_mib(value: u64) -> i64 {
    (value / 1024 / 1024) as i64
}

pub fn milliwatts_to_watts(value: Option<u32>) -> f64 {
    round1(value.unwrap_or(0) as f64 / 1000.0)
}

pub fn used_gpu_memory_mib(value: UsedGpuMemory) -> i64 {
    match value {
        UsedGpuMemory::Used(bytes) => bytes_to_mib(bytes),
        UsedGpuMemory::Unavailable => 0,
    }
}

pub fn performance_state_label(value: PerformanceState) -> String {
    let label = match value {
        PerformanceState::Zero => "P0",
        PerformanceState::One => "P1",
        PerformanceState::Two => "P2",
        PerformanceState::Three => "P3",
        PerformanceState::Four => "P4",
        PerformanceState::Five => "P5",
        PerformanceState::Six => "P6",
        PerformanceState::Seven => "P7",
        PerformanceState::Eight => "P8",
        PerformanceState::Nine => "P9",
        PerformanceState::Ten => "P10",
        PerformanceState::Eleven => "P11",
        PerformanceState::Twelve => "P12",
        PerformanceState::Thirteen => "P13",
        PerformanceState::Fourteen => "P14",
        PerformanceState::Fifteen => "P15",
        other => return format!("{other:?}"),
    };
    label.to_string()
}

pub fn compute_mode_label(value: ComputeMode) -> String {
    match value {
        ComputeMode::Default => "Default",
        ComputeMode::ExclusiveThread => "Exclusive Thread",
        ComputeMode::Prohibited => "Prohibited",
        ComputeMode::ExclusiveProcess => "Exclusive Process",
    }
    .to_string()
}

pub fn architecture_label(value: DeviceArchitecture) -> Option<String> {
    match value {
        DeviceArchitecture::Unknown => None,
        other => Some(other.to_string()),
    }
}

fn nvml_processes(
    nvml: &Nvml,
    device: &nvml_wrapper::Device<'_>,
) -> Result<Vec<GpuProcess>, NvmlError> {
    let mut processes = Vec::new();
    for process in device.running_compute_processes()? {
        let pid = process.pid as i64;
        let cmdline = procfs::process_cmdline(pid);
        let exe = procfs::process_exe(pid);
        let name = nvml
            .sys_process_name(process.pid, 256)
            .ok()
            .or_else(|| exe.clone())
            .unwrap_or_else(|| "?".to_string());
        processes.push(GpuProcess {
            pid,
            name: name.clone(),
            gpu_memory_mb: used_gpu_memory_mib(process.used_gpu_memory),
            ppid: procfs::process_parent_pid(pid),
            user: None,
            task_name: Some(infer_task_name(
                cmdline.0.as_deref(),
                exe.as_deref(),
                None,
                Some(&name),
                Some(pid),
            )),
            exe,
            cmdline_hash: cmdline_fingerprint(cmdline.0.as_deref()),
            cmdline: cmdline.0,
            kind: "compute".to_string(),
            detail_status: cmdline.1,
            ..Default::default()
        });
    }
    Ok(processes)
}

fn cuda_version_string(version: i32) -> String {
    format!(
        "{}.{}",
        nvml_wrapper::cuda_driver_version_major(version),
        nvml_wrapper::cuda_driver_version_minor(version)
    )
}

fn enabled_label(enabled: bool) -> String {
    if enabled { "Enabled" } else { "Disabled" }.to_string()
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
