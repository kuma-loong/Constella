use crate::procfs::{Procfs, excluded};
use constella_protocol::{Gpu, Process, Sample, unix_time};
use libloading::Library;
use std::collections::{HashMap, HashSet};
use std::ffi::{c_char, c_int, c_uint, c_ulong, c_void};
use std::time::{Duration, Instant};

const MAX_CARDS: usize = 64;
const MAX_PROCESSES: usize = 1024;

#[repr(C)]
#[derive(Default)]
struct Chip {
    chip_type: [u8; 32],
    chip_name: [u8; 32],
    chip_version: [u8; 32],
    ai_core_count: c_uint,
    npu_name: [u8; 32],
}

#[repr(C)]
#[derive(Default)]
struct Hbm {
    memory_size: u64,
    frequency: c_uint,
    memory_usage: u64,
    temperature: c_int,
    bandwidth_utilization: c_uint,
}

#[repr(C)]
struct Memory {
    memory_size: u64,
    memory_available: u64,
    frequency: c_uint,
    hugepage_size: c_ulong,
    hugepages_total: c_ulong,
    hugepages_free: c_ulong,
    utilization: c_uint,
    reserved: [u8; 60],
}

impl Default for Memory {
    fn default() -> Self {
        Self {
            memory_size: 0,
            memory_available: 0,
            frequency: 0,
            hugepage_size: 0,
            hugepages_total: 0,
            hugepages_free: 0,
            utilization: 0,
            reserved: [0; 60],
        }
    }
}

#[repr(C)]
#[derive(Default)]
struct Pcie {
    vendor_id: c_uint,
    subvendor_id: c_uint,
    device_id: c_uint,
    subdevice_id: c_uint,
    domain: c_int,
    bus: c_uint,
    device: c_uint,
    function: c_uint,
    reserved: [u8; 32],
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Resource {
    pid: c_int,
    memory_bytes: c_ulong,
}

#[repr(C)]
#[derive(Default)]
struct RatedPower {
    soc_rated_power_mw: c_uint,
    reserved: [u8; 32],
}

type DeviceRead<T> = unsafe extern "C" fn(c_int, c_int, *mut T) -> c_int;

struct Api {
    _library: Library,
    init: unsafe extern "C" fn() -> c_int,
    cards: unsafe extern "C" fn(*mut c_int, *mut c_int, c_int) -> c_int,
    dies: unsafe extern "C" fn(c_int, *mut c_int) -> c_int,
    chip: DeviceRead<Chip>,
    hbm: Option<DeviceRead<Hbm>>,
    memory: Option<DeviceRead<Memory>>,
    pcie: Option<DeviceRead<Pcie>>,
    temperature: DeviceRead<c_int>,
    power: Option<DeviceRead<c_int>>,
    utilization: unsafe extern "C" fn(c_int, c_int, c_int, *mut c_uint) -> c_int,
    resources: Option<unsafe extern "C" fn(c_int, c_int, *mut Resource, *mut c_int) -> c_int>,
    device_info: Option<
        unsafe extern "C" fn(c_int, c_int, c_int, c_uint, *mut c_void, *mut c_uint) -> c_int,
    >,
    driver_version: Option<unsafe extern "C" fn(*mut c_char, c_uint) -> c_int>,
    dcmi_version: Option<unsafe extern "C" fn(*mut c_char, c_uint) -> c_int>,
}

impl Api {
    fn load() -> Result<Self, crate::Error> {
        let driver_paths = [
            "/usr/local/dcmi/libdcmi.so",
            "/usr/local/Ascend/driver/lib64/driver/libdcmi.so",
            "libdcmi.so",
        ];
        let candidates = std::env::var_os("CONSTELLA_DCMI_LIBRARY")
            .into_iter()
            .chain(driver_paths.into_iter().map(Into::into));
        // SAFETY: these are administrator-supplied or system vendor driver libraries.
        let library = candidates
            .filter_map(|path| unsafe { Library::new(path).ok() })
            .next()
            .ok_or("cannot load libdcmi.so")?;
        // SAFETY: each pointer uses the vendor DCMI ABI and the Api owns the library.
        unsafe {
            let api = Self {
                init: *library.get(b"dcmi_init")?,
                cards: *library.get(b"dcmi_get_card_num_list")?,
                dies: *library.get(b"dcmi_get_device_num_in_card")?,
                chip: *library.get(b"dcmi_get_device_chip_info_v2")?,
                hbm: library
                    .get(b"dcmi_get_device_hbm_info")
                    .ok()
                    .map(|symbol| *symbol),
                memory: library
                    .get(b"dcmi_get_device_memory_info_v3")
                    .ok()
                    .map(|symbol| *symbol),
                pcie: library
                    .get(b"dcmi_get_device_pcie_info_v2")
                    .ok()
                    .map(|symbol| *symbol),
                temperature: *library.get(b"dcmi_get_device_temperature")?,
                power: library
                    .get(b"dcmi_get_device_power_info")
                    .ok()
                    .map(|symbol| *symbol),
                utilization: *library.get(b"dcmi_get_device_utilization_rate")?,
                resources: library
                    .get(b"dcmi_get_device_resource_info")
                    .ok()
                    .map(|symbol| *symbol),
                device_info: library
                    .get(b"dcmi_get_device_info")
                    .ok()
                    .map(|symbol| *symbol),
                driver_version: library
                    .get(b"dcmi_get_driver_version")
                    .ok()
                    .map(|symbol| *symbol),
                dcmi_version: library
                    .get(b"dcmi_get_dcmi_version")
                    .ok()
                    .map(|symbol| *symbol),
                _library: library,
            };
            check((api.init)(), "dcmi_init")?;
            Ok(api)
        }
    }

    fn devices(&self) -> Result<Vec<(c_int, c_int)>, String> {
        let mut count = 0;
        let mut cards = [0; MAX_CARDS];
        // SAFETY: count is an output and cards can hold MAX_CARDS entries.
        check(
            unsafe { (self.cards)(&mut count, cards.as_mut_ptr(), MAX_CARDS as c_int) },
            "dcmi_get_card_num_list",
        )?;
        let mut devices = Vec::new();
        for card in &cards[..count.clamp(0, MAX_CARDS as c_int) as usize] {
            let mut dies = 0;
            // SAFETY: dies is a writable integer.
            if unsafe { (self.dies)(*card, &mut dies) } == 0 {
                devices.extend((0..dies.clamp(0, 64)).map(|die| (*card, die)));
            }
        }
        if devices.is_empty() {
            return Err("DCMI returned no devices".into());
        }
        if devices.len() > 256 {
            return Err("DCMI returned more devices than the protocol supports".into());
        }
        devices.sort_unstable();
        Ok(devices)
    }

    fn utilization(&self, (card, die): (c_int, c_int), kind: c_int) -> Result<u32, String> {
        let mut value = 0;
        // SAFETY: value is a writable integer of the vendor-specified type.
        check(
            unsafe { (self.utilization)(card, die, kind, &mut value) },
            "dcmi_get_device_utilization_rate",
        )?;
        Ok(value)
    }

    fn rated_power(&self, (card, die): (c_int, c_int)) -> f64 {
        let Some(read) = self.device_info else {
            return 0.0;
        };
        let mut power = RatedPower::default();
        let mut length = size_of::<RatedPower>() as c_uint;
        // SAFETY: the power subcommand accepts a RatedPower output buffer with its byte length.
        if unsafe {
            read(
                card,
                die,
                8,
                10,
                (&mut power as *mut RatedPower).cast(),
                &mut length,
            )
        } == 0
        {
            f64::from(power.soc_rated_power_mw) / 1000.0
        } else {
            0.0
        }
    }
}

fn check(code: c_int, name: &str) -> Result<(), String> {
    if code == 0 {
        Ok(())
    } else {
        Err(format!("{name}: DCMI error {code}"))
    }
}

fn text(data: &[u8]) -> Option<String> {
    let size = data
        .iter()
        .position(|byte| *byte == 0)
        .unwrap_or(data.len());
    (size != 0).then(|| String::from_utf8_lossy(&data[..size]).into_owned())
}

fn version(read: unsafe extern "C" fn(*mut c_char, c_uint) -> c_int) -> Option<String> {
    let mut data = [0u8; 256];
    // SAFETY: data is writable for the given buffer length.
    (unsafe { read(data.as_mut_ptr().cast(), data.len() as c_uint) } == 0)
        .then(|| text(&data))
        .flatten()
}

fn call<T: Default>(read: DeviceRead<T>, (card, die): (c_int, c_int)) -> Result<T, c_int> {
    let mut value = T::default();
    // SAFETY: every caller matches the output type to the DCMI symbol's declared ABI.
    let rc = unsafe { read(card, die, &mut value) };
    if rc == 0 { Ok(value) } else { Err(rc) }
}

struct Device {
    location: (c_int, c_int),
    inventory: Gpu,
    processes: Vec<Process>,
    process_error: Option<String>,
}

pub struct Ascend {
    api: Api,
    procfs: Procfs,
    devices: Vec<Device>,
    devices_at: Option<Instant>,
    processes_at: Option<Instant>,
    versions: Sample,
}

impl Ascend {
    pub fn new() -> Result<Self, crate::Error> {
        let api = Api::load()?;
        let versions = Sample {
            source: "dcmi".into(),
            driver_version: api.driver_version.and_then(version),
            nvml_version: api.dcmi_version.and_then(version),
            ..Sample::default()
        };
        Ok(Self {
            api,
            procfs: Procfs::new(),
            devices: Vec::new(),
            devices_at: None,
            processes_at: None,
            versions,
        })
    }

    pub fn sample(&mut self, process_interval: f64) -> Result<Sample, crate::Error> {
        let start = Instant::now();
        let sampled_at = unix_time();
        let refresh_devices = self
            .devices_at
            .is_none_or(|at| at.elapsed() >= Duration::from_secs(60));
        if refresh_devices {
            let mut uuids = HashSet::new();
            self.devices = self
                .api
                .devices()?
                .into_iter()
                .enumerate()
                .map(|(index, location)| {
                    let mut inventory = inventory(&self.api, location, index as u32);
                    if !uuids.insert(inventory.uuid.clone()) {
                        let (card, die) = location;
                        inventory.uuid = format!("{}-{card}-{die}", inventory.uuid);
                    }
                    Device {
                        location,
                        inventory,
                        processes: Vec::new(),
                        process_error: None,
                    }
                })
                .collect();
            self.devices_at = Some(start);
        }
        let refresh_processes = refresh_devices
            || self
                .processes_at
                .is_none_or(|at| at.elapsed().as_secs_f64() >= process_interval);
        let mut users = HashMap::new();
        let mut details = HashMap::new();
        let mut gpus = Vec::with_capacity(self.devices.len());
        for device in &mut self.devices {
            let mut gpu = device.inventory.clone();
            fill_metrics(&self.api, device.location, &mut gpu);
            if refresh_processes {
                match resources(
                    &self.api,
                    device.location,
                    &self.procfs,
                    sampled_at,
                    &mut users,
                    &mut details,
                ) {
                    Ok(processes) => {
                        device.processes = processes;
                        device.process_error = None;
                    }
                    Err(error) => {
                        device.processes.clear();
                        device.process_error = Some(error);
                    }
                }
            }
            gpu.processes.clone_from(&device.processes);
            if let Some(error) = &device.process_error {
                gpu.telemetry_errors
                    .insert("processes".into(), error.clone());
            }
            gpus.push(gpu);
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

fn inventory(api: &Api, location: (c_int, c_int), index: u32) -> Gpu {
    let (card, die) = location;
    let chip = call(api.chip, location);
    let mut gpu = Gpu {
        index,
        device_type: "ascend".into(),
        card_id: Some(card.to_string()),
        die_id: Some(die as u32),
        name: "Ascend NPU".into(),
        ..Gpu::default()
    };
    match chip {
        Ok(chip) => {
            if let Some(name) = text(&chip.chip_name).or_else(|| text(&chip.npu_name)) {
                gpu.name = name;
            }
            gpu.architecture = text(&chip.chip_version);
        }
        Err(code) => gpu.error = check(code, "dcmi_get_device_chip_info_v2").err(),
    }
    gpu.pci_bus_id = api
        .pcie
        .and_then(|read| call(read, location).ok())
        .map(|pci| {
            format!(
                "{:04x}:{:02x}:{:02x}.{:x}",
                pci.domain, pci.bus, pci.device, pci.function
            )
        });
    gpu.uuid = gpu.pci_bus_id.as_ref().map_or_else(
        || format!("ascend-{card}-{die}"),
        |bus| format!("ascend-{bus}"),
    );
    if die == 0 {
        gpu.power_limit_watts = api.rated_power(location);
    }
    gpu
}

fn fill_metrics(api: &Api, location: (c_int, c_int), gpu: &mut Gpu) {
    let memory = api
        .hbm
        .and_then(|read| call(read, location).ok())
        .filter(|hbm| hbm.memory_size != 0)
        .map(|hbm| (hbm.memory_size, hbm.memory_usage, hbm.frequency))
        .or_else(|| {
            api.memory
                .and_then(|read| call(read, location).ok())
                .map(|mem| {
                    (
                        mem.memory_size,
                        mem.memory_size.saturating_sub(mem.memory_available),
                        mem.frequency,
                    )
                })
        });
    if let Some((total, used, frequency)) = memory {
        gpu.memory_total_mb = total;
        gpu.memory_used_mb = used;
        gpu.memory_free_mb = total.saturating_sub(used);
        gpu.clock_mem_mhz = (frequency != 0).then_some(frequency);
    } else {
        for field in ["memory_total_mb", "memory_used_mb", "memory_free_mb"] {
            gpu.telemetry_errors
                .insert(field.into(), "DCMI memory reading unavailable".into());
        }
    }
    match api.utilization(location, 2) {
        Ok(value) => gpu.utilization_gpu = value,
        Err(error) => {
            gpu.telemetry_errors.insert("utilization_gpu".into(), error);
        }
    }
    gpu.utilization_mem = api.utilization(location, 6).unwrap_or_else(|_| {
        if gpu.memory_total_mb == 0 {
            0
        } else {
            (gpu.memory_used_mb as f64 * 100.0 / gpu.memory_total_mb as f64).round() as u32
        }
    });
    match call(api.temperature, location) {
        Ok(value) => gpu.temperature_c = value.max(0) as u32,
        Err(code) => {
            gpu.telemetry_errors
                .insert("temperature_c".into(), format!("DCMI error {code}"));
        }
    }
    if location.1 == 0
        && let Some(power) = api.power
    {
        match call(power, location) {
            Ok(value) => gpu.power_watts = f64::from(value) / 10.0,
            Err(code) => {
                gpu.telemetry_errors
                    .insert("power_watts".into(), format!("DCMI error {code}"));
            }
        }
    }
}

fn resources(
    api: &Api,
    (card, die): (c_int, c_int),
    procfs: &Procfs,
    now: f64,
    users: &mut HashMap<u32, Option<String>>,
    details: &mut HashMap<u32, Process>,
) -> Result<Vec<Process>, String> {
    let read = api.resources.ok_or("DCMI process reporting unavailable")?;
    let mut values = vec![Resource::default(); MAX_PROCESSES];
    let mut count = MAX_PROCESSES as c_int;
    // SAFETY: max capacity is passed by reference and values is allocated for that capacity.
    check(
        unsafe { read(card, die, values.as_mut_ptr(), &mut count) },
        "dcmi_get_device_resource_info",
    )?;
    let mut processes = HashMap::<u32, Process>::new();
    for value in &values[..count.clamp(0, MAX_PROCESSES as c_int) as usize] {
        if value.pid <= 0 {
            continue;
        }
        let pid = value.pid as u32;
        let process = processes.entry(pid).or_insert_with(|| {
            let mut process = details
                .entry(pid)
                .or_insert_with(|| procfs.read(pid, now, users))
                .clone();
            process.kind = "compute".into();
            process
        });
        process.gpu_memory_mb = process
            .gpu_memory_mb
            .max(value.memory_bytes / (1024 * 1024));
    }
    let mut processes: Vec<_> = processes
        .into_values()
        .filter(|process| !excluded(process))
        .collect();
    processes
        .sort_unstable_by_key(|process| (std::cmp::Reverse(process.gpu_memory_mb), process.pid));
    Ok(processes)
}
