use crate::gpm::Gpm;
use crate::procfs::{Procfs, excluded};
use constella_protocol::{Gpu, Process, Sample, unix_time};
use nvml_wrapper::enum_wrappers::device::{Clock, ComputeMode, TemperatureSensor};
use nvml_wrapper::enums::device::UsedGpuMemory;
use nvml_wrapper::error::NvmlError;
use nvml_wrapper::{Device, Nvml, cuda_driver_version_major, cuda_driver_version_minor};
use nvml_wrapper_sys::bindings::{nvmlMemory_v2_t, nvmlReturn_enum_NVML_SUCCESS as NVML_SUCCESS};
use std::collections::{BTreeMap, HashMap};
use std::time::{Duration, Instant};

const MIB: u64 = 1024 * 1024;

pub struct Nvidia {
    // GPM handles must be freed before the driver library is unloaded.
    gpm: Gpm,
    nvml: Nvml,
    procfs: Procfs,
    versions: Sample,
    inventory: HashMap<u32, Gpu>,
    inventory_at: Option<Instant>,
    processes: HashMap<u32, Vec<Process>>,
    processes_at: Option<Instant>,
}

impl Nvidia {
    pub fn new() -> Result<Self, NvmlError> {
        let nvml = Nvml::init()?;
        let versions = Sample {
            source: "nvml".into(),
            driver_version: nvml.sys_driver_version().ok(),
            nvml_version: nvml.sys_nvml_version().ok(),
            cuda_driver_version: nvml.sys_cuda_driver_version().ok().map(|version| {
                format!(
                    "{}.{}",
                    cuda_driver_version_major(version),
                    cuda_driver_version_minor(version)
                )
            }),
            ..Sample::default()
        };
        Ok(Self {
            gpm: Gpm::new(&nvml),
            nvml,
            procfs: Procfs::new(),
            versions,
            inventory: HashMap::new(),
            inventory_at: None,
            processes: HashMap::new(),
            processes_at: None,
        })
    }

    pub fn sample(&mut self, process_interval: f64) -> Result<Sample, NvmlError> {
        let start = Instant::now();
        let sampled_at = unix_time();
        let count = self.nvml.device_count()?;
        let refresh_inventory = self
            .inventory_at
            .is_none_or(|at| at.elapsed() >= Duration::from_secs(60));
        let refresh_processes = self
            .processes_at
            .is_none_or(|at| at.elapsed().as_secs_f64() >= process_interval);
        let mut users = HashMap::new();
        let mut details = HashMap::new();
        let mut gpus = Vec::with_capacity(count as usize);
        for index in 0..count {
            let device = match self.nvml.device_by_index(index) {
                Ok(device) => device,
                Err(error) => {
                    self.inventory.remove(&index);
                    self.gpm.remove(index);
                    self.processes.remove(&index);
                    gpus.push(Gpu {
                        index,
                        name: "unknown".into(),
                        uuid: "unknown".into(),
                        device_type: "nvidia".into(),
                        error: Some(error.to_string()),
                        ..Gpu::default()
                    });
                    continue;
                }
            };
            if refresh_inventory || !self.inventory.contains_key(&index) {
                let new = inventory(index, &device);
                if self
                    .inventory
                    .get(&index)
                    .is_some_and(|old| old.uuid != new.uuid)
                {
                    self.processes.remove(&index);
                    self.gpm.remove(index);
                }
                self.inventory.insert(index, new);
            }
            let mut gpu = self.inventory[&index].clone();
            fill_metrics(&self.nvml, &device, &mut gpu);
            gpu.performance = Some(self.gpm.read(&self.nvml, &device, index, sampled_at));
            if refresh_processes || !self.processes.contains_key(&index) {
                let processes =
                    collect_processes(&device, &self.procfs, sampled_at, &mut users, &mut details);
                match processes {
                    Ok(processes) => {
                        self.processes.insert(index, processes);
                    }
                    Err(error) => {
                        gpu.telemetry_errors
                            .insert("processes".into(), error.to_string());
                        self.processes.remove(&index);
                    }
                }
            }
            gpu.processes = self.processes.get(&index).cloned().unwrap_or_default();
            gpus.push(gpu);
        }
        self.gpm.retain(count);
        self.inventory.retain(|index, _| *index < count);
        self.processes.retain(|index, _| *index < count);
        if refresh_inventory {
            self.inventory_at = Some(start);
        }
        if refresh_processes {
            self.processes_at = Some(start);
        }
        Ok(Sample {
            sampled_at,
            elapsed_ms: start.elapsed().as_secs_f64() * 1000.0,
            gpus,
            ..self.versions.clone()
        })
    }
}

fn mode(enabled: bool) -> String {
    if enabled { "Enabled" } else { "Disabled" }.into()
}

fn inventory(index: u32, device: &Device<'_>) -> Gpu {
    use ComputeMode::*;
    Gpu {
        index,
        device_type: "nvidia".into(),
        uuid: device.uuid().unwrap_or_else(|_| "unknown".into()),
        name: device.name().unwrap_or_else(|_| "unknown".into()),
        architecture: device.architecture().ok().map(|value| format!("{value:?}")),
        pci_bus_id: device.pci_info().ok().map(|value| value.bus_id),
        max_clock_sm_mhz: device.max_clock_info(Clock::SM).ok(),
        max_clock_mem_mhz: device.max_clock_info(Clock::Memory).ok(),
        mig_mode: device.mig_mode().ok().map(|value| mode(value.current != 0)),
        ecc_mode: device
            .is_ecc_enabled()
            .ok()
            .map(|value| mode(value.currently_enabled)),
        compute_mode: device.compute_mode().ok().map(|value| {
            match value {
                Default => "Default",
                ExclusiveThread => "Exclusive Thread",
                Prohibited => "Prohibited",
                ExclusiveProcess => "Exclusive Process",
            }
            .into()
        }),
        ..Gpu::default()
    }
}

fn metric<T: Default>(
    result: Result<T, NvmlError>,
    fields: &[&str],
    errors: &mut BTreeMap<String, String>,
) -> T {
    result.unwrap_or_else(|error| {
        for field in fields {
            errors.insert((*field).into(), error.to_string());
        }
        T::default()
    })
}

fn memory(nvml: &Nvml, device: &Device<'_>) -> Result<(u64, u64), NvmlError> {
    let mut value = nvmlMemory_v2_t {
        version: size_of::<nvmlMemory_v2_t>() as u32 | (2 << 24),
        total: 0,
        reserved: 0,
        free: 0,
        used: 0,
    };
    if let Ok(get_memory) = nvml.lib().nvmlDeviceGetMemoryInfo_v2.as_ref() {
        // SAFETY: the handle belongs to the live NVML session and value is a versioned NVML output buffer.
        let result = unsafe { get_memory(device.handle(), &mut value) };
        if result == NVML_SUCCESS {
            return Ok((
                value.total,
                value
                    .total
                    .saturating_sub(value.free)
                    .saturating_sub(value.reserved),
            ));
        }
    }
    device.memory_info().map(|value| (value.total, value.used))
}

fn fill_metrics(nvml: &Nvml, device: &Device<'_>, gpu: &mut Gpu) {
    let errors = &mut gpu.telemetry_errors;
    let (total, used) = metric(
        memory(nvml, device),
        &["memory_total_mb", "memory_used_mb", "memory_free_mb"],
        errors,
    );
    gpu.memory_total_mb = total / MIB;
    gpu.memory_used_mb = used / MIB;
    gpu.memory_free_mb = gpu.memory_total_mb.saturating_sub(gpu.memory_used_mb);
    let (gpu_util, mem_util) = metric(
        device
            .utilization_rates()
            .map(|value| (value.gpu, value.memory)),
        &["utilization_gpu", "utilization_mem"],
        errors,
    );
    gpu.utilization_gpu = gpu_util;
    gpu.utilization_mem = mem_util;
    gpu.temperature_c = metric(
        device.temperature(TemperatureSensor::Gpu),
        &["temperature_c"],
        errors,
    );
    gpu.power_watts = f64::from(metric(device.power_usage(), &["power_watts"], errors)) / 1000.0;
    gpu.power_limit_watts = f64::from(metric(
        device.enforced_power_limit(),
        &["power_limit_watts"],
        errors,
    )) / 1000.0;
    gpu.clock_sm_mhz = device.clock_info(Clock::SM).ok();
    gpu.clock_mem_mhz = device.clock_info(Clock::Memory).ok();
    gpu.pstate = device
        .performance_state()
        .ok()
        .map(|state| state.as_c())
        .filter(|state| *state <= 15)
        .map(|state| format!("P{state}"));
}

fn collect_processes(
    device: &Device<'_>,
    procfs: &Procfs,
    now: f64,
    users: &mut HashMap<u32, Option<String>>,
    details: &mut HashMap<u32, Process>,
) -> Result<Vec<Process>, NvmlError> {
    let compute = device
        .running_compute_processes()
        .or_else(|_| device.running_compute_processes_v2())?;
    let graphics = device
        .running_graphics_processes()
        .or_else(|_| device.running_graphics_processes_v2())
        .unwrap_or_default();
    let mut result = HashMap::<u32, Process>::new();
    for (kind, readings) in [("compute", compute), ("graphics", graphics)] {
        for reading in readings {
            let process = result.entry(reading.pid).or_insert_with(|| {
                let mut process = details
                    .entry(reading.pid)
                    .or_insert_with(|| procfs.read(reading.pid, now, users))
                    .clone();
                process.kind = kind.into();
                process
            });
            if let UsedGpuMemory::Used(bytes) = reading.used_gpu_memory {
                process.gpu_memory_mb = process.gpu_memory_mb.max(bytes / MIB);
            }
        }
    }
    let mut processes: Vec<_> = result
        .into_values()
        .filter(|process| !excluded(process))
        .collect();
    processes
        .sort_unstable_by_key(|process| (std::cmp::Reverse(process.gpu_memory_mb), process.pid));
    Ok(processes)
}
