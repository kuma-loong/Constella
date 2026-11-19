use axum::{body::Bytes, extract::ws::Utf8Bytes};
use constella_protocol::{Gpu, Sample, Settings, unix_time};
use serde::Serialize;
use std::collections::{BTreeMap, HashSet, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::sync::watch;

const HISTORY_SIZE: usize = 120;
static EMPTY_HISTORY: BTreeMap<String, Series> = BTreeMap::new();

#[derive(Default, Serialize)]
struct Series {
    gpu: VecDeque<f64>,
    memory: VecDeque<f64>,
    power: VecDeque<f64>,
    temperature: VecDeque<f64>,
}

impl Series {
    fn push(&mut self, gpu: &Gpu) {
        for (series, value) in [
            (&mut self.gpu, f64::from(gpu.utilization_gpu)),
            (&mut self.memory, memory_percent(gpu)),
            (&mut self.power, power_percent(gpu)),
            (&mut self.temperature, f64::from(gpu.temperature_c)),
        ] {
            if series.len() == HISTORY_SIZE {
                series.pop_front();
            }
            series.push_back(value);
        }
    }
}

#[derive(Default, Serialize)]
struct Totals {
    gpu_count: usize,
    accelerator_count: usize,
    card_count: usize,
    active_processes: usize,
    avg_gpu_utilization: f64,
    avg_memory_utilization: f64,
    memory_used_mb: u64,
    memory_total_mb: u64,
    power_watts: f64,
    power_limit_watts: f64,
    max_temperature_c: u32,
}

impl Totals {
    fn from_gpus<'a>(gpus: impl IntoIterator<Item = (&'a str, &'a Gpu)>) -> Self {
        let mut totals = Self::default();
        let mut cards = HashSet::new();
        let mut processes = HashSet::new();
        let mut utilization = (0.0, 0.0);
        for (node, gpu) in gpus {
            totals.gpu_count += 1;
            let fallback = gpu_id(node, gpu);
            let card = gpu.card_id.as_deref().unwrap_or(&fallback).to_owned();
            cards.insert((node, &gpu.device_type, card));
            processes.extend(gpu.processes.iter().map(|process| (node, process.pid)));
            for user in &gpu.other_users {
                totals.active_processes =
                    totals.active_processes.saturating_add(user.process_count);
            }
            if valid(gpu, &["utilization_gpu"]) {
                utilization.0 += f64::from(gpu.utilization_gpu);
                utilization.1 += 1.0;
            }
            if valid(gpu, &["memory_total_mb", "memory_used_mb"]) {
                totals.memory_total_mb = totals.memory_total_mb.saturating_add(gpu.memory_total_mb);
                totals.memory_used_mb = totals.memory_used_mb.saturating_add(gpu.memory_used_mb);
            }
            if valid(gpu, &["power_watts", "power_limit_watts"]) {
                totals.power_watts += gpu.power_watts;
                totals.power_limit_watts += gpu.power_limit_watts;
            }
            if valid(gpu, &["temperature_c"]) {
                totals.max_temperature_c = totals.max_temperature_c.max(gpu.temperature_c);
            }
        }
        totals.accelerator_count = totals.gpu_count;
        totals.card_count = cards.len();
        totals.active_processes = totals.active_processes.saturating_add(processes.len());
        totals.avg_gpu_utilization = ratio(utilization.0, utilization.1, 1.0);
        totals.avg_memory_utilization = ratio(
            totals.memory_used_mb as f64,
            totals.memory_total_mb as f64,
            100.0,
        );
        totals.power_watts = ratio(totals.power_watts, 1.0, 1.0);
        totals.power_limit_watts = ratio(totals.power_limit_watts, 1.0, 1.0);
        totals
    }
}

pub fn valid(gpu: &Gpu, metrics: &[&str]) -> bool {
    gpu.error.is_none()
        && metrics
            .iter()
            .all(|metric| !gpu.telemetry_errors.contains_key(*metric))
}

fn ratio(numerator: f64, denominator: f64, multiplier: f64) -> f64 {
    if denominator > 0.0 {
        (numerator / denominator * multiplier * 10.0).round() / 10.0
    } else {
        0.0
    }
}

fn memory_percent(gpu: &Gpu) -> f64 {
    ratio(gpu.memory_used_mb as f64, gpu.memory_total_mb as f64, 100.0)
}

fn power_percent(gpu: &Gpu) -> f64 {
    ratio(gpu.power_watts, gpu.power_limit_watts, 100.0)
}

pub fn gpu_id(node: &str, gpu: &Gpu) -> String {
    if gpu.uuid.is_empty() || gpu.uuid == "unknown" {
        format!("{node}:index:{}", gpu.index)
    } else {
        format!("{node}:{}", gpu.uuid)
    }
}

struct Node {
    hostname: String,
    agent_version: String,
    connection: Option<u64>,
    received_at: f64,
    last_seen: Instant,
    seq: Option<u64>,
    sample: Arc<Sample>,
    history: BTreeMap<String, Series>,
}

impl Node {
    fn status(&self, settings: Settings) -> &'static str {
        self.status_at(settings, Instant::now()).0
    }

    fn status_at(&self, settings: Settings, now: Instant) -> (&'static str, Option<Instant>) {
        let offline =
            self.last_seen + Duration::from_secs_f64((10.0 * settings.refresh_interval).max(30.0));
        if self.connection.is_none() || now >= offline {
            return ("offline", None);
        }
        let stale =
            self.last_seen + Duration::from_secs_f64((3.0 * settings.refresh_interval).max(5.0));
        if now >= stale {
            return ("stale", Some(offline));
        }
        let status = if self.sample.error.is_some() {
            "error"
        } else if self.seq.is_none() {
            "connecting"
        } else {
            "online"
        };
        (status, Some(stale))
    }

    fn output<'a>(
        &'a self,
        node_id: &'a str,
        settings: Settings,
        status: &'static str,
    ) -> NodeOutput<'a> {
        NodeOutput::new(
            NodeMetadata {
                node_id,
                hostname: &self.hostname,
                agent_version: &self.agent_version,
                seq: self.seq.unwrap_or_default(),
                received_at: self.received_at,
            },
            &self.sample,
            settings,
            status,
            Some(&self.history),
        )
    }
}

#[derive(Serialize)]
pub struct NodeMetadata<'a> {
    pub node_id: &'a str,
    pub hostname: &'a str,
    pub agent_version: &'a str,
    pub seq: u64,
    pub received_at: f64,
}

pub fn raw_snapshot(
    metadata: NodeMetadata<'_>,
    sample: &Sample,
    settings: Settings,
) -> serde_json::Result<String> {
    let status = if sample.error.is_some() {
        "error"
    } else {
        "online"
    };
    serde_json::to_string(&NodeOutput::new(metadata, sample, settings, status, None))
}

#[derive(Serialize)]
struct GpuOutput<'a> {
    #[serde(flatten)]
    gpu: &'a Gpu,
    node_id: &'a str,
    gpu_id: String,
    memory_percent: f64,
    power_percent: f64,
}

#[derive(Serialize)]
struct HardwareGpu<'a> {
    index: u32,
    uuid: &'a str,
    name: &'a str,
    architecture: Option<&'a str>,
}

#[derive(Serialize)]
struct Hardware<'a> {
    gpus: Vec<HardwareGpu<'a>>,
}

#[derive(Serialize)]
struct NodeOutput<'a> {
    #[serde(flatten)]
    metadata: NodeMetadata<'a>,
    status: &'static str,
    refresh_interval: f64,
    process_interval: f64,
    sampled_at: f64,
    elapsed_ms: f64,
    source: &'a str,
    driver_version: Option<&'a str>,
    cuda_driver_version: Option<&'a str>,
    nvml_version: Option<&'a str>,
    error: Option<&'a str>,
    gpus: Vec<GpuOutput<'a>>,
    history: &'a BTreeMap<String, Series>,
    totals: Totals,
    #[serde(skip_serializing_if = "Option::is_none")]
    hardware: Option<Hardware<'a>>,
    performance_profiles: std::collections::BTreeSet<&'a str>,
}

impl<'a> NodeOutput<'a> {
    fn new(
        metadata: NodeMetadata<'a>,
        sample: &'a Sample,
        settings: Settings,
        status: &'static str,
        history: Option<&'a BTreeMap<String, Series>>,
    ) -> Self {
        let node_id = metadata.node_id;
        let gpus = sample
            .gpus
            .iter()
            .map(|gpu| GpuOutput {
                gpu,
                node_id,
                gpu_id: gpu_id(node_id, gpu),
                memory_percent: memory_percent(gpu),
                power_percent: power_percent(gpu),
            })
            .collect();
        Self {
            metadata,
            status,
            refresh_interval: settings.refresh_interval,
            process_interval: settings.process_interval,
            sampled_at: sample.sampled_at,
            elapsed_ms: sample.elapsed_ms,
            source: &sample.source,
            driver_version: sample.driver_version.as_deref(),
            cuda_driver_version: sample.cuda_driver_version.as_deref(),
            nvml_version: sample.nvml_version.as_deref(),
            error: sample.error.as_deref(),
            gpus,
            history: history.unwrap_or(&EMPTY_HISTORY),
            totals: Totals::from_gpus(sample.gpus.iter().map(|gpu| (node_id, gpu))),
            hardware: history.map(|_| Hardware {
                gpus: sample
                    .gpus
                    .iter()
                    .map(|gpu| HardwareGpu {
                        index: gpu.index,
                        uuid: &gpu.uuid,
                        name: &gpu.name,
                        architecture: gpu.architecture.as_deref(),
                    })
                    .collect(),
            }),
            performance_profiles: sample
                .gpus
                .iter()
                .filter_map(|gpu| gpu.performance.as_ref())
                .map(|performance| performance.profile.as_str())
                .collect(),
        }
    }
}

#[derive(Serialize)]
struct Snapshot<'a> {
    ok: bool,
    seq: u64,
    timestamp: f64,
    nodes: Vec<NodeOutput<'a>>,
    totals: ClusterTotals,
    history: BTreeMap<&'a str, &'a Series>,
}

#[derive(Serialize)]
struct ClusterTotals {
    #[serde(flatten)]
    telemetry: Totals,
    node_count: usize,
    online_node_count: usize,
    stale_node_count: usize,
    offline_node_count: usize,
}

pub struct ObservedNode {
    pub complete: bool,
    pub processes: Vec<(u32, Option<f64>)>,
}

pub struct Cluster {
    nodes: Mutex<BTreeMap<String, Node>>,
    snapshot_cache: Mutex<Option<CachedSnapshot>>,
    pub updates: watch::Sender<u64>,
}

#[derive(Clone)]
pub struct ClusterPayload {
    pub json: Bytes,
    pub text: Utf8Bytes,
}

struct CachedSnapshot {
    seq: u64,
    settings: Settings,
    expires_at: Instant,
    payload: ClusterPayload,
}

impl Default for Cluster {
    fn default() -> Self {
        Self {
            nodes: Mutex::default(),
            snapshot_cache: Mutex::default(),
            updates: watch::channel(0).0,
        }
    }
}

impl Cluster {
    pub fn connect(
        &self,
        node_id: String,
        hostname: String,
        agent_version: String,
        connection: u64,
    ) {
        let mut nodes = self.nodes.lock().unwrap();
        let node = nodes.entry(node_id).or_insert_with(|| Node {
            hostname: String::new(),
            agent_version: String::new(),
            connection: None,
            received_at: 0.0,
            last_seen: Instant::now(),
            seq: None,
            sample: Arc::new(Sample {
                source: "agent".into(),
                ..Sample::default()
            }),
            history: BTreeMap::new(),
        });
        node.hostname = hostname;
        node.agent_version = agent_version;
        node.connection = Some(connection);
        node.last_seen = Instant::now();
        node.received_at = unix_time();
        node.seq = None;
        self.bump();
    }

    pub fn ingest(
        &self,
        node_id: &str,
        connection: u64,
        seq: u64,
        mut sample: Sample,
    ) -> Option<Arc<Sample>> {
        if sample.gpus.len() > 256 || sample.sampled_at <= 0.0 || sample.elapsed_ms < 0.0 {
            return None;
        }
        let mut ids = HashSet::new();
        for gpu in &mut sample.gpus {
            if !ids.insert(gpu_id(node_id, gpu)) {
                return None;
            }
            sanitize(gpu);
        }
        let mut nodes = self.nodes.lock().unwrap();
        let node = nodes.get_mut(node_id)?;
        if node.connection != Some(connection) || node.seq.is_some_and(|previous| seq <= previous) {
            return None;
        }
        node.history.retain(|id, _| ids.contains(id));
        for gpu in &sample.gpus {
            let id = gpu_id(node_id, gpu);
            let fields = [
                "utilization_gpu",
                "memory_total_mb",
                "memory_used_mb",
                "power_watts",
                "temperature_c",
            ];
            if valid(gpu, &fields) {
                node.history.entry(id).or_default().push(gpu);
            } else {
                node.history.remove(&id);
            }
        }
        let sample = Arc::new(sample);
        node.sample = sample.clone();
        node.seq = Some(seq);
        node.last_seen = Instant::now();
        node.received_at = unix_time();
        self.bump();
        Some(sample)
    }

    pub fn disconnect(&self, node_id: &str, connection: u64) {
        let mut nodes = self.nodes.lock().unwrap();
        if let Some(node) = nodes.get_mut(node_id)
            && node.connection == Some(connection)
        {
            node.connection = None;
            self.bump();
        }
    }

    fn bump(&self) {
        self.updates.send_modify(|seq| *seq += 1);
    }

    pub fn observed_processes(
        &self,
        settings: Settings,
        now: f64,
    ) -> BTreeMap<String, ObservedNode> {
        let nodes = self.nodes.lock().unwrap();
        nodes
            .iter()
            .filter(|(_, node)| {
                node.status(settings) == "online"
                    && now - node.sample.sampled_at < (3.0 * settings.process_interval).max(30.0)
            })
            .map(|(id, node)| {
                let gpus = &node.sample.gpus;
                let observed = ObservedNode {
                    complete: !gpus.is_empty()
                        && gpus.iter().all(|gpu| {
                            gpu.error.is_none()
                                && gpu.other_users.is_empty()
                                && !gpu.telemetry_errors.contains_key("processes")
                        }),
                    processes: gpus
                        .iter()
                        .flat_map(|gpu| {
                            gpu.processes
                                .iter()
                                .map(|process| (process.pid, process.process_start_time))
                        })
                        .collect(),
                };
                (id.clone(), observed)
            })
            .collect()
    }

    pub fn snapshot(&self, settings: Settings) -> ClusterPayload {
        self.snapshot_at(settings, Instant::now())
    }

    fn snapshot_at(&self, settings: Settings, now: Instant) -> ClusterPayload {
        let nodes = self.nodes.lock().unwrap();
        let seq = *self.updates.borrow();
        let mut cache = self.snapshot_cache.lock().unwrap();
        if let Some(snapshot) = &*cache
            && snapshot.seq == seq
            && snapshot.settings == settings
            && now < snapshot.expires_at
        {
            return snapshot.payload.clone();
        }
        // Refresh the timestamp on idle heartbeats and expire earlier if a node changes status.
        let mut expires_at = now + Duration::from_secs(5);
        let payloads: Vec<_> = nodes
            .iter()
            .map(|(id, node)| {
                let (status, change) = node.status_at(settings, now);
                if let Some(change) = change {
                    expires_at = expires_at.min(change);
                }
                node.output(id, settings, status)
            })
            .collect();
        let totals = ClusterTotals {
            node_count: nodes.len(),
            online_node_count: payloads
                .iter()
                .filter(|node| node.status == "online")
                .count(),
            stale_node_count: payloads
                .iter()
                .filter(|node| node.status == "stale")
                .count(),
            offline_node_count: payloads
                .iter()
                .filter(|node| node.status == "offline")
                .count(),
            telemetry: Totals::from_gpus(
                payloads
                    .iter()
                    .filter(|node| node.status != "offline")
                    .flat_map(|node| node.gpus.iter().map(|gpu| (gpu.node_id, gpu.gpu))),
            ),
        };
        let history = payloads
            .iter()
            .flat_map(|node| {
                node.history
                    .iter()
                    .map(|(id, series)| (id.as_str(), series))
            })
            .collect();
        let json = serde_json::to_vec(&Snapshot {
            ok: totals.online_node_count != 0,
            seq,
            timestamp: unix_time(),
            nodes: payloads,
            totals,
            history,
        })
        .expect("cluster response is serializable");
        let json = Bytes::from(json);
        let text = Utf8Bytes::try_from(json.clone()).expect("serialized JSON is UTF-8");
        let payload = ClusterPayload { json, text };
        *cache = Some(CachedSnapshot {
            seq,
            settings,
            expires_at,
            payload: payload.clone(),
        });
        payload
    }

    pub fn health(&self, settings: Settings, agent_ingest_enabled: bool) -> serde_json::Value {
        let nodes = self.nodes.lock().unwrap();
        let mut degraded = 0;
        let mut invalid = 0;
        let mut gpu_count = 0;
        let mut online = 0;
        for node in nodes.values() {
            let status = node.status(settings);
            online += usize::from(status == "online");
            if status != "offline" {
                gpu_count += node.sample.gpus.len();
            }
            for gpu in &node.sample.gpus {
                let bad_performance = gpu.performance.as_ref().is_some_and(|performance| {
                    invalid += performance.invalid_metrics.len();
                    performance.error.is_some() || !performance.invalid_metrics.is_empty()
                });
                degraded += usize::from(
                    gpu.error.is_some() || !gpu.telemetry_errors.is_empty() || bad_performance,
                );
            }
        }
        serde_json::json!({
            "ok": true, "seq": *self.updates.borrow(), "source": "manager",
            "agent_ingest_enabled": agent_ingest_enabled, "node_count": nodes.len(),
            "online_node_count": online, "gpu_count": gpu_count,
            "telemetry": {"degraded_gpu_count": degraded, "invalid_metric_count": invalid},
            "database": {"enabled": false, "healthy": true},
        })
    }
}

fn sanitize(gpu: &mut Gpu) {
    for (value, field, max) in [
        (&mut gpu.utilization_gpu, "utilization_gpu", 100),
        (&mut gpu.utilization_mem, "utilization_mem", 100),
        (&mut gpu.temperature_c, "temperature_c", 150),
    ] {
        if *value > max {
            *value = 0;
            gpu.telemetry_errors
                .insert(field.into(), "Reading out of range".into());
        }
    }
    if gpu.memory_used_mb > gpu.memory_total_mb {
        gpu.memory_used_mb = 0;
        gpu.telemetry_errors.insert(
            "memory_used_mb".into(),
            "Memory usage exceeds capacity".into(),
        );
    }
    for (value, field) in [
        (&mut gpu.power_watts, "power_watts"),
        (&mut gpu.power_limit_watts, "power_limit_watts"),
    ] {
        if !(0.0..=10_000.0).contains(value) {
            *value = 0.0;
            gpu.telemetry_errors
                .insert(field.into(), "Reading out of range".into());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshots_share_bytes_and_expire_on_updates_settings_and_status() {
        let cluster = Cluster::default();
        cluster.connect("node".into(), "host".into(), "test".into(), 1);
        let settings = Settings::new(1.0, 5.0).unwrap();
        let connecting = cluster.snapshot(settings);
        cluster
            .ingest(
                "node",
                1,
                1,
                Sample {
                    sampled_at: 1.0,
                    ..Sample::default()
                },
            )
            .unwrap();
        let now = cluster.nodes.lock().unwrap()["node"].last_seen;
        let online = cluster.snapshot_at(settings, now + Duration::from_secs(4));
        let same = cluster.snapshot_at(settings, now + Duration::from_millis(4500));
        assert_eq!(online.json.as_ptr(), same.json.as_ptr());
        assert_eq!(online.json.as_ptr(), online.text.as_str().as_ptr());
        assert_ne!(connecting.json.as_ptr(), online.json.as_ptr());
        assert_eq!(status(&online), "online");

        let stale = cluster.snapshot_at(settings, now + Duration::from_secs(5));
        assert_eq!(status(&stale), "stale");
        let slow = Settings::new(5.0, 5.0).unwrap();
        let slower = cluster.snapshot_at(slow, now + Duration::from_secs(5));
        assert_eq!(status(&slower), "online");
        let offline = cluster.snapshot_at(settings, now + Duration::from_secs(30));
        assert_eq!(status(&offline), "offline");
    }

    fn status(payload: &ClusterPayload) -> String {
        serde_json::from_slice::<serde_json::Value>(&payload.json).unwrap()["nodes"][0]["status"]
            .as_str()
            .unwrap()
            .to_owned()
    }
}
