use constella_protocol::{NVIDIA_GPM_METRICS, Performance};
use nvml_wrapper::{Device, Nvml};
use nvml_wrapper_sys::bindings::{
    nvmlGpmMetric_t, nvmlGpmMetric_t__bindgen_ty_1, nvmlGpmMetricsGet_t, nvmlGpmSample_t,
    nvmlGpmSupport_t, nvmlReturn_enum_NVML_ERROR_NOT_SUPPORTED as UNSUPPORTED,
    nvmlReturn_enum_NVML_SUCCESS as SUCCESS, nvmlReturn_t,
};
use std::collections::{BTreeMap, HashMap};
use std::ptr::null_mut;
use std::time::{Duration, Instant};

#[derive(Default)]
pub struct Gpm {
    enabled: bool,
    states: HashMap<u32, State>,
}

impl Gpm {
    pub fn new(nvml: &Nvml) -> Self {
        let value = std::env::var("CONSTELLA_NVML_GPM").unwrap_or_default();
        let lib = nvml.lib();
        let enabled = !matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "0" | "false" | "no" | "off"
        ) && lib.nvmlGpmQueryDeviceSupport.is_ok()
            && lib.nvmlGpmSampleAlloc.is_ok()
            && lib.nvmlGpmSampleFree.is_ok()
            && lib.nvmlGpmSampleGet.is_ok()
            && lib.nvmlGpmMetricsGet.is_ok();
        Self {
            enabled,
            ..Self::default()
        }
    }

    pub fn remove(&mut self, index: u32) {
        self.states.remove(&index);
    }

    pub fn retain(&mut self, count: u32) {
        self.states.retain(|index, _| *index < count);
    }

    pub fn read(
        &mut self,
        nvml: &Nvml,
        device: &Device<'_>,
        index: u32,
        sampled_at: f64,
    ) -> Performance {
        let mut output = Performance {
            profile: "nvidia.gpm.v1".into(),
            status: "unsupported".into(),
            sampled_at,
            interval_ms: None,
            metrics: BTreeMap::new(),
            supported_metrics: Vec::new(),
            invalid_metrics: Vec::new(),
            error: None,
        };
        if !self.enabled {
            return output;
        }
        let state = self.states.entry(index).or_default();
        let now = Instant::now();
        if state.retry_at.is_some_and(|deadline| now < deadline) {
            output.status = "error".into();
            output.error = Some("GPM retry pending".into());
        } else {
            match state.read(nvml, device, now, &mut output) {
                Ok(()) => state.failures = 0,
                Err(error) => {
                    state.previous_at = None;
                    state.failures += 1;
                    if state.failures >= 3 {
                        state.retry_at = Some(now + Duration::from_secs(30));
                        state.failures = 0;
                    }
                    output.status = "error".into();
                    output.error = Some(error);
                }
            }
        }
        if state.probed {
            output.supported_metrics = state
                .metrics
                .iter()
                .map(|(_, name)| (*name).to_owned())
                .collect();
        }
        output
    }
}

#[derive(Default)]
struct State {
    supported: Option<bool>,
    samples: Option<[Handle; 2]>,
    metrics: Vec<(u32, &'static str)>,
    probed: bool,
    next_sample: usize,
    previous_at: Option<Instant>,
    failures: u8,
    retry_at: Option<Instant>,
}

struct Handle {
    raw: nvmlGpmSample_t,
    free: unsafe extern "C" fn(nvmlGpmSample_t) -> nvmlReturn_t,
}

impl Drop for Handle {
    fn drop(&mut self) {
        // SAFETY: this handle owns a sample and is released before NVML is unloaded.
        unsafe { (self.free)(self.raw) };
    }
}

fn allocate(nvml: &Nvml) -> Result<Handle, String> {
    let lib = nvml.lib();
    let mut raw = null_mut();
    // SAFETY: the library is initialized and raw is writable storage for the handle.
    check(
        unsafe { lib.nvmlGpmSampleAlloc.as_ref().unwrap()(&mut raw) },
        "nvmlGpmSampleAlloc",
    )?;
    if raw.is_null() {
        return Err("NVML returned a null GPM sample handle".into());
    }
    Ok(Handle {
        raw,
        free: *lib.nvmlGpmSampleFree.as_ref().unwrap(),
    })
}

// Older bindings declare 98 entries. Reserve 333 as in the existing Python
// sampler so the same version-1 layout can hold either driver's metrics array.
#[repr(C)]
struct Request {
    version: u32,
    count: u32,
    first: nvmlGpmSample_t,
    second: nvmlGpmSample_t,
    metrics: [nvmlGpmMetric_t; 333],
}

impl State {
    fn read(
        &mut self,
        nvml: &Nvml,
        device: &Device<'_>,
        now: Instant,
        output: &mut Performance,
    ) -> Result<(), String> {
        let lib = nvml.lib();
        if self.supported.is_none() {
            let mut support = nvmlGpmSupport_t {
                version: 1,
                isSupportedDevice: 0,
            };
            // SAFETY: device is still attached to the initialized session and support is writable.
            let result = unsafe {
                lib.nvmlGpmQueryDeviceSupport.as_ref().unwrap()(device.handle(), &mut support)
            };
            if result != UNSUPPORTED {
                check(result, "nvmlGpmQueryDeviceSupport")?;
            }
            self.supported = Some(result == SUCCESS && support.isSupportedDevice != 0);
        }
        if self.supported == Some(false) || (self.probed && self.metrics.is_empty()) {
            return Ok(());
        }
        let interval = self.previous_at.map(|prior| now.duration_since(prior));
        if interval.is_some_and(|duration| duration <= Duration::from_millis(100)) {
            output.status = "warming".into();
            return Ok(());
        }
        if self.samples.is_none() {
            self.samples = Some([allocate(nvml)?, allocate(nvml)?]);
            self.metrics = NVIDIA_GPM_METRICS.to_vec();
        }
        let samples = self.samples.as_ref().unwrap();
        let current = samples[self.next_sample].raw;
        let previous = samples[1 - self.next_sample].raw;
        // SAFETY: current is a live allocated sample and device belongs to nvml.
        check(
            unsafe { lib.nvmlGpmSampleGet.as_ref().unwrap()(device.handle(), current) },
            "nvmlGpmSampleGet",
        )?;
        self.previous_at = Some(now);
        self.next_sample = 1 - self.next_sample;
        let Some(interval) = interval else {
            output.status = "warming".into();
            return Ok(());
        };
        output.interval_ms = Some(interval.as_secs_f64() * 1000.0);
        let mut request = Request {
            version: 1,
            count: self.metrics.len() as u32,
            first: previous,
            second: current,
            metrics: [nvmlGpmMetric_t {
                metricId: 0,
                nvmlReturn: SUCCESS,
                value: 0.0,
                metricInfo: nvmlGpmMetric_t__bindgen_ty_1 {
                    shortName: null_mut(),
                    longName: null_mut(),
                    unit: null_mut(),
                },
            }; 333],
        };
        for (metric, (id, _)) in request.metrics.iter_mut().zip(&self.metrics) {
            metric.metricId = *id;
        }
        // SAFETY: Request shares the NVML header and metric layout, with storage
        // sized for the newer version-1 driver as well as the one in the bindings.
        let ptr = (&mut request as *mut Request).cast::<nvmlGpmMetricsGet_t>();
        check(
            unsafe { lib.nvmlGpmMetricsGet.as_ref().unwrap()(ptr) },
            "nvmlGpmMetricsGet",
        )?;
        let mut position = 0;
        self.metrics.retain(|(_, name)| {
            let metric = &request.metrics[position];
            position += 1;
            if metric.nvmlReturn == UNSUPPORTED {
                return false;
            }
            let max = if name.ends_with("_per_second") {
                f32::MAX as f64
            } else {
                100.0
            };
            if metric.nvmlReturn == SUCCESS && (0.0..=max).contains(&metric.value) {
                output
                    .metrics
                    .insert((*name).into(), (metric.value * 1000.0).round() / 1000.0);
            } else {
                output.invalid_metrics.push((*name).into());
            }
            true
        });
        self.probed = true;
        if !output.metrics.is_empty() {
            output.status = "available".into();
        } else if !self.metrics.is_empty() {
            return Err("NVML GPM returned no valid readings".into());
        }
        Ok(())
    }
}

fn check(result: nvmlReturn_t, operation: &str) -> Result<(), String> {
    if result == SUCCESS {
        Ok(())
    } else {
        Err(format!("{operation}: NVML error {result}"))
    }
}
