use std::collections::{BTreeMap, VecDeque};

use crate::nvidia_smi;
use crate::schema::{HistoryPayload, Snapshot};
use crate::settings::{validate_refresh_interval, SettingsError, ALLOWED_REFRESH_INTERVALS};

#[derive(Debug, Clone)]
pub struct SnapshotCollector {
    pub refresh_interval: f64,
    process_interval: f64,
    history_size: usize,
    seq: i64,
    snapshot: Option<Snapshot>,
    history: BTreeMap<String, BTreeMap<String, VecDeque<f64>>>,
}

impl SnapshotCollector {
    pub fn new(
        refresh_interval: f64,
        process_interval: f64,
        history_size: usize,
    ) -> Result<Self, SettingsError> {
        Ok(Self {
            refresh_interval: validate_refresh_interval(refresh_interval)?,
            process_interval: process_interval.max(1.0),
            history_size,
            seq: 0,
            snapshot: None,
            history: BTreeMap::new(),
        })
    }

    pub fn process_interval(&self) -> f64 {
        self.process_interval.max(self.refresh_interval)
    }

    pub fn snapshot(&self) -> Option<&Snapshot> {
        self.snapshot.as_ref()
    }

    pub fn set_refresh_interval(&mut self, seconds: f64) -> Result<f64, SettingsError> {
        let interval = validate_refresh_interval(seconds)?;
        self.refresh_interval = interval;
        Ok(interval)
    }

    pub fn set_process_interval(&mut self, seconds: f64) -> f64 {
        self.process_interval = seconds.max(1.0);
        self.process_interval()
    }

    pub fn settings_payload(&self) -> serde_json::Value {
        serde_json::json!({
            "refresh_interval": self.refresh_interval,
            "allowed_refresh_intervals": ALLOWED_REFRESH_INTERVALS,
            "process_interval": self.process_interval(),
        })
    }

    pub fn sample_once(&mut self, collect_processes: bool) -> Snapshot {
        let snapshot = match nvidia_smi::sample(collect_processes) {
            Ok(snapshot) => snapshot,
            Err(error) => nvidia_smi::error_snapshot(error.to_string(), "none"),
        };
        self.publish(snapshot)
    }

    pub fn publish(&mut self, mut snapshot: Snapshot) -> Snapshot {
        self.seq += 1;
        snapshot.seq = self.seq;
        snapshot.refresh_interval = self.refresh_interval;
        for gpu in &snapshot.gpus {
            let key = gpu.index.to_string();
            append_history(
                self.history_size,
                self.history.entry(key).or_insert_with(|| {
                    ["gpu", "memory", "power", "temperature"]
                        .into_iter()
                        .map(|name| (name.to_string(), VecDeque::new()))
                        .collect()
                }),
                "gpu",
                gpu.utilization_gpu as f64,
            );
            append_history(
                self.history_size,
                self.history.get_mut(&gpu.index.to_string()).unwrap(),
                "memory",
                gpu.memory_percent(),
            );
            append_history(
                self.history_size,
                self.history.get_mut(&gpu.index.to_string()).unwrap(),
                "power",
                gpu.power_percent(),
            );
            append_history(
                self.history_size,
                self.history.get_mut(&gpu.index.to_string()).unwrap(),
                "temperature",
                gpu.temperature_c as f64,
            );
        }
        snapshot.history = self.history_payload();
        self.snapshot = Some(snapshot.clone());
        snapshot
    }

    fn history_payload(&self) -> HistoryPayload {
        self.history
            .iter()
            .map(|(gpu_index, series)| {
                (
                    gpu_index.clone(),
                    series
                        .iter()
                        .map(|(name, values)| (name.clone(), values.iter().copied().collect()))
                        .collect(),
                )
            })
            .collect()
    }
}

fn append_history(
    history_size: usize,
    series: &mut BTreeMap<String, VecDeque<f64>>,
    name: &str,
    value: f64,
) {
    let values = series.entry(name.to_string()).or_default();
    values.push_back(value);
    while values.len() > history_size {
        values.pop_front();
    }
}
