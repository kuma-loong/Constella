use crate::{App, Error};
use axum::Json;
use axum::extract::{Query, State};
use constella_protocol::{Gpu, NVIDIA_GPM_METRICS, NVIDIA_GPM_PROFILE, Sample, unix_time};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, VecDeque};
use std::sync::{Arc, Mutex};
use tokio::sync::Semaphore;

pub const RETENTION: f64 = 7200.0;
const CAPACITY: usize = 14_400;
const METRICS: usize = NVIDIA_GPM_METRICS.len();

#[derive(Clone, Copy)]
struct Frame {
    at: f64,
    metrics: [f32; METRICS],
    valid: u16,
}

impl Frame {
    fn value(self, index: usize) -> Option<f32> {
        (self.valid & (1 << index) != 0).then_some(self.metrics[index])
    }
}

#[derive(Clone, Copy)]
pub struct BasicFrame {
    pub at: f64,
    values: [f32; 6],
}

impl BasicFrame {
    pub fn point(self) -> Value {
        let [gpu, mem, used, total, power, temp] = self
            .values
            .map(|value| (f64::from(value) * 10.0).round() / 10.0);
        json!({
            "sampled_at": self.at, "bucket_start": self.at, "utilization_gpu": gpu,
            "utilization_mem": mem, "memory_used_mb": used, "memory_total_mb": total,
            "power_watts": power, "temperature_c": temp, "avg_gpu_utilization": gpu,
            "avg_memory_used_mb": used, "avg_power_watts": power, "avg_temperature_c": temp,
        })
    }
}

#[derive(Default)]
struct Cache {
    devices: Devices,
    basic: BTreeMap<(String, String), VecDeque<BasicFrame>>,
    sample_count: u64,
    last_sample: Option<f64>,
    next_cleanup: f64,
}

pub struct BasicWindow {
    pub oldest: f64,
    pub newest: f64,
    pub interval: Option<f64>,
    pub points: Vec<BasicFrame>,
}

fn push<T>(ring: &mut VecDeque<T>, frames: T, at: impl Fn(&T) -> f64) {
    let time = at(&frames);
    if ring.back().is_some_and(|last| at(last) >= time) {
        return;
    }
    while ring.len() >= CAPACITY
        || ring
            .front()
            .is_some_and(|first| at(first) < time - RETENTION)
    {
        ring.pop_front();
    }
    ring.push_back(frames);
}

fn basic_frame(gpu: &Gpu, at: f64) -> Option<BasicFrame> {
    crate::cluster::valid(
        gpu,
        &[
            "utilization_gpu",
            "utilization_mem",
            "memory_total_mb",
            "memory_used_mb",
            "memory_free_mb",
            "power_watts",
            "power_limit_watts",
            "temperature_c",
        ],
    )
    .then_some(BasicFrame {
        at,
        values: [
            gpu.utilization_gpu as f32,
            gpu.utilization_mem as f32,
            gpu.memory_used_mb as f32,
            gpu.memory_total_mb as f32,
            gpu.power_watts as f32,
            gpu.temperature_c as f32,
        ],
    })
}

struct Ring {
    index: u32,
    name: String,
    status: String,
    supported: u16,
    frames: VecDeque<Frame>,
}

type Devices = BTreeMap<String, BTreeMap<String, Ring>>;

pub struct Highres {
    enabled: bool,
    cache: Mutex<Cache>,
    readers: Arc<Semaphore>,
}

impl Default for Highres {
    fn default() -> Self {
        let enabled = std::env::var("CONSTELLA_NVIDIA_GPM_HIGHRES")
            .ok()
            .is_none_or(|setting| {
                !matches!(
                    setting.trim().to_ascii_lowercase().as_str(),
                    "0" | "false" | "no" | "off"
                )
            });
        Self {
            enabled,
            cache: Mutex::default(),
            readers: Arc::new(Semaphore::new(2)),
        }
    }
}

impl Highres {
    pub fn add(&self, node_id: &str, sample: &Sample) {
        let mut cache = self.cache.lock().unwrap();
        cache.sample_count += sample.gpus.len() as u64;
        cache.last_sample = Some(sample.sampled_at);
        for gpu in &sample.gpus {
            let uuid = if gpu.uuid.is_empty() || gpu.uuid == "unknown" {
                crate::cluster::gpu_id(node_id, gpu)
            } else {
                gpu.uuid.clone()
            };
            if let Some(frame) = basic_frame(gpu, sample.sampled_at) {
                push(
                    cache
                        .basic
                        .entry((node_id.to_owned(), uuid.clone()))
                        .or_default(),
                    frame,
                    |frame| frame.at,
                );
            }
            let Some(performance) = gpu
                .performance
                .as_ref()
                .filter(|perf| self.enabled && perf.profile == NVIDIA_GPM_PROFILE)
            else {
                continue;
            };
            let node = cache.devices.entry(node_id.to_owned()).or_default();
            let ring = node.entry(uuid).or_insert_with(|| Ring {
                index: gpu.index,
                name: gpu.name.clone(),
                status: String::new(),
                supported: 0,
                frames: VecDeque::new(),
            });
            ring.index = gpu.index;
            ring.name.clone_from(&gpu.name);
            ring.status.clone_from(&performance.status);
            let mut frame = Frame {
                at: sample.sampled_at,
                metrics: [0.0; METRICS],
                valid: 0,
            };
            for (index, (_, name)) in NVIDIA_GPM_METRICS.iter().enumerate() {
                let bit = 1 << index;
                if performance
                    .supported_metrics
                    .iter()
                    .any(|metric| metric == name)
                {
                    ring.supported |= bit;
                }
                if let Some(&value) = performance.metrics.get(*name) {
                    let maximum = if name.ends_with("_per_second") {
                        f32::MAX as f64
                    } else {
                        100.0
                    };
                    if (0.0..=maximum).contains(&value) && gpu.error.is_none() {
                        frame.metrics[index] = value as f32;
                        frame.valid |= bit;
                        ring.supported |= bit;
                    }
                }
            }
            push(&mut ring.frames, frame, |frame| frame.at);
        }
        let now = unix_time();
        if now >= cache.next_cleanup {
            let cutoff = now - RETENTION;
            cache
                .basic
                .retain(|_, ring| ring.back().is_some_and(|last| last.at >= cutoff));
            cache.devices.retain(|_, node| {
                node.retain(|_, ring| ring.frames.back().is_some_and(|last| last.at >= cutoff));
                !node.is_empty()
            });
            cache.next_cleanup = now + 60.0;
        }
    }

    pub fn basic_window(
        &self,
        node: &str,
        gpu: &str,
        since: f64,
        until: f64,
    ) -> Option<BasicWindow> {
        let cache = self.cache.lock().unwrap();
        let ring = cache.basic.get(&(node.to_owned(), gpu.to_owned()))?;
        let oldest = ring.front()?.at;
        let newest = ring.back()?.at;
        let start = ring.partition_point(|frame| frame.at < since);
        let end = ring.partition_point(|frame| frame.at <= until);
        Some(BasicWindow {
            oldest,
            newest,
            interval: (ring.len() > 1).then(|| (newest - oldest) / (ring.len() - 1) as f64),
            points: ring.range(start..end).copied().collect(),
        })
    }

    pub fn covers_performance(&self, node: &str, gpu: &str, start: f64, end: f64) -> bool {
        let cache = self.cache.lock().unwrap();
        cache
            .devices
            .get(node)
            .and_then(|gpus| gpus.get(gpu))
            .is_some_and(|ring| {
                ring.frames.front().is_some_and(|frame| frame.at <= start)
                    && ring.frames.back().is_some_and(|frame| frame.at >= end)
            })
    }

    fn status(&self) -> Value {
        let cache = self.cache.lock().unwrap();
        let rings = || cache.devices.values().flat_map(BTreeMap::values);
        let count = rings().count();
        let size: usize = rings().map(|ring| ring.frames.len()).sum();
        let capacity: usize = rings().map(|ring| ring.frames.capacity()).sum();
        let basic_size: usize = cache.basic.values().map(VecDeque::len).sum();
        let newest = cache
            .basic
            .values()
            .filter_map(|ring| ring.back())
            .map(|frame| frame.at)
            .max_by(f64::total_cmp);
        let oldest = cache
            .basic
            .values()
            .filter_map(|ring| ring.front())
            .map(|frame| frame.at)
            .min_by(f64::total_cmp);
        json!({
            "enabled": true, "ring_count": cache.basic.len(), "capacity_per_gpu": CAPACITY,
            "valid_point_count": basic_size, "approx_bytes": basic_size * size_of::<BasicFrame>(),
            "retention_seconds": RETENTION, "sample_count": cache.sample_count, "dropped_samples": 0,
            "oldest_sample_at": oldest, "newest_sample_at": newest, "last_sample_at": cache.last_sample,
            "performance": {
                "enabled": self.enabled, "profile": NVIDIA_GPM_PROFILE, "ring_count": count,
                "capacity_per_gpu": CAPACITY, "valid_point_count": size, "approx_bytes": size * size_of::<Frame>(),
                "allocated_bytes": capacity * size_of::<Frame>(),
            },
        })
    }

    pub fn query(&self, request: PerformanceQuery) -> Value {
        let until = request.until.unwrap_or_else(unix_time).min(unix_time());
        let since = request.since.unwrap_or(until - 900.0).max(0.0);
        let (since, until) = (since.min(until), since.max(until));
        let metrics = select_metrics(request.metrics.as_deref());
        let selected = split(request.gpu_uuid.as_deref());
        let max_points = request.max_points.unwrap_or(1500).clamp(10, 5000) as usize;
        let rings = {
            let cache = self.cache.lock().unwrap();
            let mut rings = Vec::new();
            for (uuid, ring) in cache.devices.get(&request.node_id).into_iter().flatten() {
                if !selected.is_empty() && !selected.contains(&uuid.as_str()) {
                    continue;
                }
                let start = ring.frames.partition_point(|frame| frame.at < since);
                let end = ring.frames.partition_point(|frame| frame.at <= until);
                rings.push((
                    ring.index,
                    uuid.clone(),
                    ring.name.clone(),
                    ring.status.clone(),
                    ring.supported,
                    ring.frames.range(start..end).copied().collect::<Vec<_>>(),
                ));
            }
            rings.sort_unstable_by(|a, b| (a.0, &a.1).cmp(&(b.0, &b.1)));
            rings
        };
        let series: Vec<_> = rings.into_iter().map(|(index, uuid, name, status, supported, frames)| {
            let values: BTreeMap<_, _> = metrics.iter().filter(|(index, _)| supported & (1 << index) != 0)
                .map(|(index, name)| (*name, summarize(&frames, *index, max_points, request.summary_only)))
                .collect();
            json!({"node_id": request.node_id, "gpu_uuid": uuid, "gpu_index": index, "name": name,
                "status": status, "metrics": values})
        }).collect();
        let names: Vec<_> = metrics.into_iter().map(|(_, name)| name).collect();
        json!({"enabled": self.enabled, "profile": NVIDIA_GPM_PROFILE, "since": since,
            "until": until, "metrics": names, "series": series})
    }
}

fn split(value: Option<&str>) -> Vec<&str> {
    value
        .unwrap_or_default()
        .split(',')
        .map(str::trim)
        .filter(|part| !part.is_empty())
        .collect()
}

pub fn select_metrics(request: Option<&str>) -> Vec<(usize, &'static str)> {
    let selected = split(request);
    if selected.is_empty() {
        return NVIDIA_GPM_METRICS
            .iter()
            .enumerate()
            .map(|(index, (_, name))| (index, *name))
            .collect();
    }
    selected
        .into_iter()
        .filter_map(|metric| {
            NVIDIA_GPM_METRICS
                .iter()
                .enumerate()
                .find_map(|(index, (_, name))| (metric == *name).then_some((index, *name)))
        })
        .collect()
}

#[derive(Deserialize)]
pub struct PerformanceQuery {
    pub node_id: String,
    pub gpu_uuid: Option<String>,
    pub metrics: Option<String>,
    pub since: Option<f64>,
    pub until: Option<f64>,
    pub max_points: Option<i64>,
    #[serde(default)]
    pub summary_only: bool,
}

#[derive(Serialize)]
struct Summary {
    avg: Option<f64>,
    min: Option<f64>,
    max: Option<f64>,
    p95: Option<f64>,
    sample_count: usize,
    expected_count: usize,
    coverage: f64,
}

fn summarize(frames: &[Frame], index: usize, limit: usize, summary_only: bool) -> Value {
    let mut values: Vec<_> = frames
        .iter()
        .filter_map(|frame| frame.value(index))
        .collect();
    let round = |value: f64| (value * 1000.0).round() / 1000.0;
    let count = values.len();
    let mut summary = Summary {
        avg: None,
        min: None,
        max: None,
        p95: None,
        sample_count: count,
        expected_count: frames.len(),
        coverage: 0.0,
    };
    if count > 0 {
        values.sort_unstable_by(f32::total_cmp);
        summary.avg = Some(round(
            values.iter().map(|value| f64::from(*value)).sum::<f64>() / count as f64,
        ));
        summary.min = Some(round(f64::from(values[0])));
        summary.max = Some(round(f64::from(values[count - 1])));
        summary.p95 = Some(round(f64::from(values[(count * 95).div_ceil(100) - 1])));
        summary.coverage = (count as f64 * 10000.0 / frames.len() as f64).round() / 100.0;
    }
    let indices = if summary_only {
        Vec::new()
    } else if frames.len() <= limit {
        (0..frames.len()).collect()
    } else {
        downsample(frames, index, limit)
    };
    let points: Vec<_> = indices
        .into_iter()
        .map(|offset| (frames[offset].at, frames[offset].value(index)))
        .collect();
    json!({ "summary": summary, "points": points })
}

fn downsample(frames: &[Frame], index: usize, limit: usize) -> Vec<usize> {
    let bucket_size = frames.len().div_ceil((limit - 2) / 3);
    let mut offsets = vec![0, frames.len() - 1];
    for (bucket, chunk) in frames.chunks(bucket_size).enumerate() {
        let start = bucket * bucket_size;
        let mut low = None::<(usize, f32)>;
        let mut high = None::<(usize, f32)>;
        let mut gap = None;
        for (offset, frame) in chunk.iter().enumerate() {
            match frame.value(index) {
                Some(value) => {
                    if low.is_none_or(|(_, minimum)| value < minimum) {
                        low = Some((start + offset, value));
                    }
                    if high.is_none_or(|(_, maximum)| value > maximum) {
                        high = Some((start + offset, value));
                    }
                }
                None => {
                    gap.get_or_insert(start + offset);
                }
            }
        }
        offsets.extend(low.map(|(offset, _)| offset));
        offsets.extend(high.map(|(offset, _)| offset));
        offsets.extend(gap);
    }
    offsets.sort_unstable();
    offsets.dedup();
    offsets
}

pub async fn status(State(app): State<Arc<App>>) -> Json<Value> {
    Json(app.highres.status())
}

pub async fn performance(
    State(app): State<Arc<App>>,
    Query(request): Query<PerformanceQuery>,
) -> Result<Json<Value>, (axum::http::StatusCode, Json<Value>)> {
    if request
        .since
        .into_iter()
        .chain(request.until)
        .any(|at| !at.is_finite())
    {
        return Err((
            axum::http::StatusCode::BAD_REQUEST,
            Json(json!({"detail": "invalid time range"})),
        ));
    }
    let task = async move {
        let permit = app.highres.readers.clone().try_acquire_owned()?;
        let result = tokio::task::spawn_blocking(move || {
            let _permit = permit;
            app.highres.query(request)
        })
        .await?;
        Ok::<_, Error>(result)
    };
    task.await.map(Json).map_err(|error| {
        eprintln!("HighRes performance query: {error}");
        (
            axum::http::StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"detail":"performance query unavailable"})),
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compact_curve_keeps_peaks_and_gaps() {
        let frames: Vec<_> = (0..100)
            .map(|i| {
                let mut frame = Frame {
                    at: f64::from(i),
                    metrics: [0.0; METRICS],
                    valid: 1,
                };
                if i == 33 {
                    frame.metrics[0] = 91.0;
                }
                if i == 34 {
                    frame.valid = 0;
                }
                frame
            })
            .collect();
        let selected = downsample(&frames, 0, 10);
        assert!(selected.len() <= 10);
        assert!(selected.contains(&33) && selected.contains(&34));
        assert_eq!((selected.first(), selected.last()), (Some(&0), Some(&99)));
    }
}
