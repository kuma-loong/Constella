use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

use crate::db::{DbError, SQLiteStore, ROLLUP_1H, ROLLUP_20S, ROLLUP_2M};
use crate::schema::{GpuInfo, NodeSnapshot};

pub const HIGHRES_RETENTION_SECONDS: f64 = 2.0 * 60.0 * 60.0;
pub const HIGHRES_MAX_JOB_SECONDS: f64 = 60.0 * 60.0;
pub const HIGHRES_JOB_LOOKBACK_SECONDS: f64 = 7.0 * 24.0 * 60.0 * 60.0;
pub const HIGHRES_MIN_INTERVAL_SECONDS: f64 = 0.5;
pub const HIGHRES_DEFAULT_PADDING_SECONDS: f64 = 20.0;
const JOB_PARENT_GROUP_MAX_AGE_SECONDS: f64 = 10.0 * 60.0;
const JOB_AUTO_2M_THRESHOLD_SECONDS: f64 = 24.0 * 60.0 * 60.0;

#[derive(Debug, Clone, PartialEq)]
pub struct GpuSampleRing {
    capacity: usize,
    samples: VecDeque<GpuSample>,
}

#[derive(Debug, Clone, PartialEq)]
struct GpuSample {
    sampled_at: f64,
    utilization_gpu: f64,
    utilization_mem: f64,
    memory_used_mb: f64,
    memory_total_mb: f64,
    power_watts: f64,
    temperature_c: f64,
}

impl GpuSampleRing {
    pub fn new(capacity: usize) -> Self {
        Self {
            capacity: capacity.max(1),
            samples: VecDeque::new(),
        }
    }

    pub fn append(&mut self, sampled_at: f64, gpu: &GpuInfo) {
        if self.samples.len() == self.capacity {
            self.samples.pop_front();
        }
        self.samples.push_back(GpuSample {
            sampled_at,
            utilization_gpu: gpu.utilization_gpu as f64,
            utilization_mem: gpu.utilization_mem as f64,
            memory_used_mb: gpu.memory_used_mb as f64,
            memory_total_mb: gpu.memory_total_mb as f64,
            power_watts: gpu.power_watts,
            temperature_c: gpu.temperature_c as f64,
        });
    }

    pub fn oldest_at(&self) -> Option<f64> {
        self.samples.front().map(|sample| sample.sampled_at)
    }

    pub fn newest_at(&self) -> Option<f64> {
        self.samples.back().map(|sample| sample.sampled_at)
    }

    pub fn count(&self) -> usize {
        self.samples.len()
    }

    pub fn points(&self, since: f64, until: f64) -> Vec<Value> {
        self.samples
            .iter()
            .filter(|sample| sample.sampled_at >= since && sample.sampled_at <= until)
            .map(|sample| {
                json!({
                    "sampled_at": sample.sampled_at,
                    "bucket_start": sample.sampled_at,
                    "utilization_gpu": round1(sample.utilization_gpu),
                    "utilization_mem": round1(sample.utilization_mem),
                    "memory_used_mb": round1(sample.memory_used_mb),
                    "memory_total_mb": round1(sample.memory_total_mb),
                    "power_watts": round1(sample.power_watts),
                    "temperature_c": round1(sample.temperature_c),
                    "avg_gpu_utilization": round1(sample.utilization_gpu),
                    "avg_memory_used_mb": round1(sample.memory_used_mb),
                    "avg_power_watts": round1(sample.power_watts),
                    "avg_temperature_c": round1(sample.temperature_c),
                })
            })
            .collect()
    }

    pub fn observed_interval_seconds(&self) -> Option<f64> {
        if self.samples.len() < 2 {
            return None;
        }
        let oldest = self.oldest_at()?;
        let newest = self.newest_at()?;
        (newest > oldest).then_some((newest - oldest) / (self.samples.len() - 1) as f64)
    }
}

#[derive(Debug, Clone)]
pub struct HighresGpuCache {
    pub retention_seconds: f64,
    pub capacity: usize,
    rings: BTreeMap<(String, String), GpuSampleRing>,
    pub sample_count: u64,
    pub dropped_samples: u64,
    pub last_sample_at: Option<f64>,
}

impl Default for HighresGpuCache {
    fn default() -> Self {
        Self::new(HIGHRES_RETENTION_SECONDS, HIGHRES_MIN_INTERVAL_SECONDS)
    }
}

impl HighresGpuCache {
    pub fn new(retention_seconds: f64, min_interval_seconds: f64) -> Self {
        let capacity = (retention_seconds / min_interval_seconds.max(0.001)).ceil() as usize;
        Self {
            retention_seconds,
            capacity: capacity.max(1),
            rings: BTreeMap::new(),
            sample_count: 0,
            dropped_samples: 0,
            last_sample_at: None,
        }
    }

    pub fn add_snapshot(&mut self, snapshot: &NodeSnapshot) {
        for gpu in &snapshot.gpus {
            let key = (snapshot.node_id.clone(), gpu.uuid.clone());
            self.rings
                .entry(key)
                .or_insert_with(|| GpuSampleRing::new(self.capacity))
                .append(snapshot.sampled_at, gpu);
            self.sample_count += 1;
        }
        self.last_sample_at = Some(snapshot.sampled_at);
    }

    pub fn series_for(
        &self,
        node_id: &str,
        gpu_uuid: &str,
        since: f64,
        until: f64,
    ) -> Option<(&GpuSampleRing, Vec<Value>)> {
        let ring = self
            .rings
            .get(&(node_id.to_string(), gpu_uuid.to_string()))?;
        Some((ring, ring.points(since, until)))
    }

    pub fn status(&self) -> Value {
        let oldest = self
            .rings
            .values()
            .filter_map(GpuSampleRing::oldest_at)
            .min_by(f64::total_cmp);
        let newest = self
            .rings
            .values()
            .filter_map(GpuSampleRing::newest_at)
            .max_by(f64::total_cmp);
        let valid_points: usize = self.rings.values().map(GpuSampleRing::count).sum();
        json!({
            "enabled": true,
            "ring_count": self.rings.len(),
            "capacity_per_gpu": self.capacity,
            "valid_point_count": valid_points,
            "approx_bytes": valid_points * (8 + 6 * 4),
            "retention_seconds": self.retention_seconds,
            "sample_count": self.sample_count,
            "dropped_samples": self.dropped_samples,
            "oldest_sample_at": oldest,
            "newest_sample_at": newest,
            "last_sample_at": self.last_sample_at,
        })
    }
}

pub fn query_jobs(
    store: &SQLiteStore,
    filter: JobFilter,
    now: Option<f64>,
) -> Result<Vec<Value>, DbError> {
    let current_time = now.unwrap_or_else(unix_now);
    let mut range_start = filter.since;
    if let Some(recent_seconds) = filter.recent_seconds {
        let recent_start = current_time - recent_seconds.max(0.0);
        range_start = Some(range_start.map_or(recent_start, |since| since.max(recent_start)));
    }
    let rows = job_rows(store, range_start, filter.until)?;
    let mut jobs: Vec<Job> = group_jobs(rows)
        .into_values()
        .filter(|job| job.matches(&filter))
        .collect();
    jobs.sort_by(|left, right| right.last_seen_at.total_cmp(&left.last_seen_at));
    Ok(jobs
        .into_iter()
        .take(filter.limit.clamp(1, 500) as usize)
        .map(Job::into_value)
        .collect())
}

pub fn get_job(store: &SQLiteStore, key: &str, now: Option<f64>) -> Result<Option<Value>, DbError> {
    let current_time = now.unwrap_or_else(unix_now);
    let rows = job_rows(
        store,
        Some(current_time - HIGHRES_JOB_LOOKBACK_SECONDS),
        None,
    )?;
    Ok(group_jobs(rows).remove(key).map(Job::into_value))
}

pub fn job_curve(
    store: &SQLiteStore,
    cache: &HighresGpuCache,
    key: &str,
    padding_seconds: f64,
    resolution: &str,
    now: Option<f64>,
) -> Result<Option<Value>, DbError> {
    let Some(job_value) = get_job(store, key, now)? else {
        return Ok(None);
    };
    let job = Job::from_value(&job_value);
    let padding = padding_seconds.clamp(0.0, 300.0);
    let range_start = (job.started_at - padding).max(0.0);
    let range_end = job.last_seen_at + padding;
    let required_start = job.started_at;
    let required_end = job.last_seen_at;
    let duration = job.duration_seconds;
    let (resolution_mode, requested_bucket) = normalize_resolution(resolution);
    let mut warnings: Vec<String> = Vec::new();

    if resolution_mode == "auto" && duration < HIGHRES_MAX_JOB_SECONDS {
        if let Some(highres) = highres_curve(
            cache,
            &job,
            range_start,
            range_end,
            required_start,
            required_end,
        ) {
            return Ok(Some(json!({
                "enabled": true,
                "source": "high_res_memory",
                "job": job_value,
                "job_key": key,
                "range_start": range_start,
                "range_end": range_end,
                "coverage_start": highres.coverage_start,
                "coverage_end": highres.coverage_end,
                "cache_retention_seconds": cache.retention_seconds,
                "resolution_seconds": highres.resolution_seconds,
                "resolution_mode": resolution_mode,
                "expired": false,
                "warnings": warnings,
                "series": highres.series,
            })));
        }
        warnings.push("high-resolution cache does not cover the full job window".to_string());
    } else if resolution_mode == "auto" {
        warnings.push("job duration is 1 hour or longer, using rollup history".to_string());
    }

    let bucket_seconds =
        requested_bucket.unwrap_or_else(|| auto_rollup_bucket(range_start, range_end));
    let rollup = rollup_curve(store, &job, range_start, range_end, bucket_seconds)?;
    Ok(Some(json!({
        "enabled": true,
        "source": "rollup",
        "job": job_value,
        "job_key": key,
        "range_start": range_start,
        "range_end": range_end,
        "coverage_start": series_min_time(&rollup),
        "coverage_end": series_max_time(&rollup),
        "cache_retention_seconds": cache.retention_seconds,
        "resolution_seconds": bucket_seconds,
        "resolution_mode": resolution_mode,
        "expired": true,
        "warnings": warnings,
        "series": rollup,
    })))
}

#[derive(Debug, Clone)]
pub struct JobFilter {
    pub q: Option<String>,
    pub user: Option<String>,
    pub pid: Option<i64>,
    pub node_id: Option<String>,
    pub status: Option<String>,
    pub since: Option<f64>,
    pub until: Option<f64>,
    pub max_duration_seconds: Option<f64>,
    pub recent_seconds: Option<f64>,
    pub limit: i64,
}

impl Default for JobFilter {
    fn default() -> Self {
        Self {
            q: None,
            user: None,
            pid: None,
            node_id: None,
            status: None,
            since: None,
            until: None,
            max_duration_seconds: None,
            recent_seconds: Some(HIGHRES_JOB_LOOKBACK_SECONDS),
            limit: 100,
        }
    }
}

#[derive(Debug, Clone)]
struct JobRow {
    session_id: String,
    node_id: String,
    pid: Option<i64>,
    ppid: Option<i64>,
    process_start_time: Option<f64>,
    parent_start_time: Option<f64>,
    user: Option<String>,
    task_name: String,
    process_name: String,
    exe: Option<String>,
    cmdline_text: Option<String>,
    first_seen_at: f64,
    last_seen_at: f64,
    status: String,
    gpu_uuid: String,
    gpu_index: Option<i64>,
    gpu_name: Option<String>,
    memory_total_mb: Option<i64>,
}

fn job_rows(
    store: &SQLiteStore,
    range_start: Option<f64>,
    range_end: Option<f64>,
) -> Result<Vec<JobRow>, DbError> {
    let mut sql = String::from(
        r#"
        SELECT
          s.session_id, s.node_id, s.pid, s.ppid, s.process_start_time,
          s.parent_start_time, s.user, s.task_name, s.process_name, s.exe,
          s.cmdline_text, s.first_seen_at, s.last_seen_at,
          s.status, u.gpu_uuid, g.gpu_index, g.name AS gpu_name, g.memory_total_mb
        FROM process_sessions s
        JOIN process_gpu_usages u ON u.session_id = s.session_id
        LEFT JOIN gpus g ON g.node_id = u.node_id AND g.uuid = u.gpu_uuid
        WHERE 1 = 1
        "#,
    );
    let mut values: Vec<rusqlite::types::Value> = vec![];
    if let Some(range_start) = range_start {
        sql.push_str(" AND s.last_seen_at >= ?");
        values.push(range_start.into());
    }
    if let Some(range_end) = range_end {
        sql.push_str(" AND s.first_seen_at <= ?");
        values.push(range_end.into());
    }
    sql.push_str(" ORDER BY s.last_seen_at DESC");
    let mut stmt = store.connection()?.prepare(&sql)?;
    let rows = stmt
        .query_map(rusqlite::params_from_iter(values), |row| {
            Ok(JobRow {
                session_id: row.get("session_id")?,
                node_id: row.get("node_id")?,
                pid: row.get("pid")?,
                ppid: row.get("ppid")?,
                process_start_time: row.get("process_start_time")?,
                parent_start_time: row.get("parent_start_time")?,
                user: row.get("user")?,
                task_name: row.get("task_name")?,
                process_name: row.get("process_name")?,
                exe: row.get("exe")?,
                cmdline_text: row.get("cmdline_text")?,
                first_seen_at: row.get("first_seen_at")?,
                last_seen_at: row.get("last_seen_at")?,
                status: row.get("status")?,
                gpu_uuid: row.get("gpu_uuid")?,
                gpu_index: row.get("gpu_index")?,
                gpu_name: row.get("gpu_name")?,
                memory_total_mb: row.get("memory_total_mb")?,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

#[derive(Debug, Clone)]
struct Job {
    job_key: String,
    node_id: String,
    user: String,
    task_name: String,
    started_at: f64,
    last_seen_at: f64,
    duration_seconds: f64,
    status: String,
    sessions: BTreeMap<String, SessionPayload>,
    pids: BTreeSet<i64>,
    gpus: BTreeMap<(String, String), GpuPayload>,
    search_text: String,
}

#[derive(Debug, Clone)]
struct SessionPayload {
    session_id: String,
    pid: Option<i64>,
    ppid: Option<i64>,
    task_name: String,
    process_name: String,
    exe: Option<String>,
    cmdline_text: Option<String>,
    started_at: f64,
    last_seen_at: f64,
    status: String,
}

#[derive(Debug, Clone)]
struct GpuPayload {
    node_id: String,
    gpu_uuid: String,
    gpu_index: Option<i64>,
    gpu_name: Option<String>,
    memory_total_mb: Option<i64>,
}

impl Job {
    fn ingest_row(&mut self, row: JobRow) {
        self.started_at = self.started_at.min(row.first_seen_at);
        self.last_seen_at = self.last_seen_at.max(row.last_seen_at);
        self.duration_seconds = (self.last_seen_at - self.started_at).max(0.0);
        if row.status == "running" {
            self.status = "running".to_string();
        }
        self.sessions
            .entry(row.session_id.clone())
            .and_modify(|session| session.last_seen_at = session.last_seen_at.max(row.last_seen_at))
            .or_insert_with(|| SessionPayload {
                session_id: row.session_id.clone(),
                pid: row.pid,
                ppid: row.ppid,
                task_name: row.task_name.clone(),
                process_name: row.process_name.clone(),
                exe: row.exe.clone(),
                cmdline_text: row.cmdline_text.clone(),
                started_at: row.first_seen_at,
                last_seen_at: row.last_seen_at,
                status: row.status.clone(),
            });
        if let Some(pid) = row.pid {
            self.pids.insert(pid);
        }
        self.gpus.insert(
            (row.node_id.clone(), row.gpu_uuid.clone()),
            GpuPayload {
                node_id: row.node_id.clone(),
                gpu_uuid: row.gpu_uuid.clone(),
                gpu_index: row.gpu_index,
                gpu_name: row.gpu_name.clone(),
                memory_total_mb: row.memory_total_mb,
            },
        );
        self.search_text.push_str(&format!(
            " {} {} {} {} {} {}",
            row.task_name,
            row.process_name,
            row.exe.unwrap_or_default(),
            row.cmdline_text.unwrap_or_default(),
            row.user.unwrap_or_default(),
            row.pid.map(|pid| pid.to_string()).unwrap_or_default()
        ));
    }

    fn matches(&self, filter: &JobFilter) -> bool {
        if filter.user.as_deref().is_some_and(|user| self.user != user) {
            return false;
        }
        if filter
            .node_id
            .as_deref()
            .is_some_and(|node_id| self.node_id != node_id)
        {
            return false;
        }
        if filter
            .status
            .as_deref()
            .is_some_and(|status| self.status != status)
        {
            return false;
        }
        if filter.pid.is_some_and(|pid| !self.pids.contains(&pid)) {
            return false;
        }
        if filter
            .max_duration_seconds
            .is_some_and(|max_duration| self.duration_seconds > max_duration)
        {
            return false;
        }
        if let Some(q) = filter.q.as_ref().map(|value| value.trim().to_lowercase()) {
            if !q.is_empty()
                && !self.search_text.to_lowercase().contains(&q)
                && !self.user.to_lowercase().contains(&q)
            {
                return false;
            }
        }
        true
    }

    fn into_value(self) -> Value {
        let mut sessions: Vec<Value> = self
            .sessions
            .into_values()
            .map(SessionPayload::into_value)
            .collect();
        sessions.sort_by(|left, right| {
            left["started_at"]
                .as_f64()
                .unwrap_or(0.0)
                .total_cmp(&right["started_at"].as_f64().unwrap_or(0.0))
        });
        let mut gpus: Vec<Value> = self
            .gpus
            .into_values()
            .map(GpuPayload::into_value)
            .collect();
        gpus.sort_by(|left, right| {
            let left_key = (
                left["node_id"].as_str().unwrap_or(""),
                left["gpu_index"].is_null(),
                left["gpu_index"].as_i64().unwrap_or(0),
                left["gpu_uuid"].as_str().unwrap_or(""),
            );
            let right_key = (
                right["node_id"].as_str().unwrap_or(""),
                right["gpu_index"].is_null(),
                right["gpu_index"].as_i64().unwrap_or(0),
                right["gpu_uuid"].as_str().unwrap_or(""),
            );
            left_key.cmp(&right_key)
        });
        let pids: Vec<i64> = self.pids.into_iter().collect();
        json!({
            "job_key": self.job_key,
            "node_id": self.node_id,
            "user": self.user,
            "task_name": self.task_name,
            "started_at": self.started_at,
            "last_seen_at": self.last_seen_at,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "sessions": sessions,
            "pids": pids,
            "gpus": gpus,
            "gpu_count": gpus.len(),
            "session_count": sessions.len(),
        })
    }

    fn from_value(value: &Value) -> Self {
        let sessions = value["sessions"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(SessionPayload::from_value)
            .map(|session| (session.session_id.clone(), session))
            .collect();
        let pids = value["pids"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_i64)
            .collect();
        let gpus = value["gpus"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(GpuPayload::from_value)
            .map(|gpu| ((gpu.node_id.clone(), gpu.gpu_uuid.clone()), gpu))
            .collect();
        Self {
            job_key: string_field(value, "job_key"),
            node_id: string_field(value, "node_id"),
            user: string_field(value, "user"),
            task_name: string_field(value, "task_name"),
            started_at: value["started_at"].as_f64().unwrap_or(0.0),
            last_seen_at: value["last_seen_at"].as_f64().unwrap_or(0.0),
            duration_seconds: value["duration_seconds"].as_f64().unwrap_or(0.0),
            status: string_field(value, "status"),
            sessions,
            pids,
            gpus,
            search_text: String::new(),
        }
    }
}

impl SessionPayload {
    fn into_value(self) -> Value {
        json!({
            "session_id": self.session_id,
            "pid": self.pid,
            "ppid": self.ppid,
            "task_name": self.task_name,
            "process_name": self.process_name,
            "exe": self.exe,
            "cmdline_text": self.cmdline_text,
            "started_at": self.started_at,
            "last_seen_at": self.last_seen_at,
            "status": self.status,
        })
    }

    fn from_value(value: &Value) -> Option<Self> {
        Some(Self {
            session_id: string_field(value, "session_id"),
            pid: value["pid"].as_i64(),
            ppid: value["ppid"].as_i64(),
            task_name: string_field(value, "task_name"),
            process_name: string_field(value, "process_name"),
            exe: value["exe"].as_str().map(str::to_string),
            cmdline_text: value["cmdline_text"].as_str().map(str::to_string),
            started_at: value["started_at"].as_f64()?,
            last_seen_at: value["last_seen_at"].as_f64()?,
            status: string_field(value, "status"),
        })
    }
}

impl GpuPayload {
    fn into_value(self) -> Value {
        json!({
            "node_id": self.node_id,
            "gpu_uuid": self.gpu_uuid,
            "gpu_index": self.gpu_index,
            "gpu_name": self.gpu_name,
            "memory_total_mb": self.memory_total_mb,
        })
    }

    fn from_value(value: &Value) -> Option<Self> {
        Some(Self {
            node_id: string_field(value, "node_id"),
            gpu_uuid: string_field(value, "gpu_uuid"),
            gpu_index: value["gpu_index"].as_i64(),
            gpu_name: value["gpu_name"].as_str().map(str::to_string),
            memory_total_mb: value["memory_total_mb"].as_i64(),
        })
    }
}

fn group_jobs(rows: Vec<JobRow>) -> BTreeMap<String, Job> {
    let mut jobs = BTreeMap::new();
    for row in rows {
        let key = job_key(&row);
        let user = row.user.clone().unwrap_or_else(|| "unknown".to_string());
        jobs.entry(key.clone())
            .or_insert_with(|| Job {
                job_key: key,
                node_id: row.node_id.clone(),
                user,
                task_name: row.task_name.clone(),
                started_at: row.first_seen_at,
                last_seen_at: row.last_seen_at,
                duration_seconds: 0.0,
                status: row.status.clone(),
                sessions: BTreeMap::new(),
                pids: BTreeSet::new(),
                gpus: BTreeMap::new(),
                search_text: String::new(),
            })
            .ingest_row(row);
    }
    jobs
}

fn job_key(row: &JobRow) -> String {
    let process_start = row.process_start_time.or(Some(row.first_seen_at));
    let parent_start = row.parent_start_time;
    let (start, owner) = if let (Some(process_start), Some(parent_start), Some(ppid)) =
        (process_start, parent_start, row.ppid)
    {
        if (0.0..=JOB_PARENT_GROUP_MAX_AGE_SECONDS).contains(&(process_start - parent_start)) {
            (Some(parent_start), Some(ppid))
        } else {
            (process_start.into(), row.pid.or(row.ppid))
        }
    } else {
        (
            process_start.or(parent_start).or(Some(row.first_seen_at)),
            row.pid.or(row.ppid),
        )
    };
    format!(
        "{}:{}:{}:{}",
        row.node_id,
        row.user.clone().unwrap_or_else(|| "unknown".to_string()),
        start.map(format_python_float).unwrap_or_default(),
        owner.map(|value| value.to_string()).unwrap_or_default()
    )
}

struct HighresCurve {
    coverage_start: Option<f64>,
    coverage_end: Option<f64>,
    resolution_seconds: Option<f64>,
    series: Vec<Value>,
}

fn highres_curve(
    cache: &HighresGpuCache,
    job: &Job,
    point_start: f64,
    point_end: f64,
    required_start: f64,
    required_end: f64,
) -> Option<HighresCurve> {
    let mut series = Vec::new();
    let mut coverage_start: Option<f64> = None;
    let mut coverage_end: Option<f64> = None;
    let mut intervals = Vec::new();
    for gpu in job.gpus.values() {
        let (ring, points) =
            cache.series_for(&gpu.node_id, &gpu.gpu_uuid, point_start, point_end)?;
        if points.is_empty() {
            return None;
        }
        let oldest = ring.oldest_at()?;
        let newest = ring.newest_at()?;
        if oldest > required_start || newest < required_end {
            return None;
        }
        if let Some(interval) = ring.observed_interval_seconds() {
            intervals.push(interval);
        }
        coverage_start = Some(coverage_start.map_or(oldest, |value| value.min(oldest)));
        coverage_end = Some(coverage_end.map_or(newest, |value| value.max(newest)));
        let mut payload = gpu.clone().into_value();
        payload["label"] = json!(gpu_label(gpu));
        payload["points"] = json!(points);
        series.push(payload);
    }
    Some(HighresCurve {
        coverage_start,
        coverage_end,
        resolution_seconds: intervals.into_iter().min_by(f64::total_cmp),
        series,
    })
}

fn rollup_curve(
    store: &SQLiteStore,
    job: &Job,
    range_start: f64,
    range_end: f64,
    bucket_seconds: i64,
) -> Result<Vec<Value>, DbError> {
    let limit = rollup_point_limit(range_start, range_end, bucket_seconds);
    let mut series = Vec::new();
    for gpu in job.gpus.values() {
        let points = store.query_gpu_history(
            Some(&gpu.node_id),
            Some(&gpu.gpu_uuid),
            Some(range_start),
            Some(range_end),
            Some(bucket_seconds),
            limit,
        )?;
        let mut payload = gpu.clone().into_value();
        payload["label"] = json!(gpu_label(gpu));
        payload["points"] = json!(points);
        series.push(payload);
    }
    Ok(series)
}

fn normalize_resolution(value: &str) -> (String, Option<i64>) {
    let resolution = value.trim().to_lowercase();
    match resolution.as_str() {
        "auto" | "" => ("auto".to_string(), None),
        "20s" => ("20s".to_string(), Some(ROLLUP_20S)),
        "2m" => ("2m".to_string(), Some(ROLLUP_2M)),
        "1h" => ("1h".to_string(), Some(ROLLUP_1H)),
        "20" => ("20s".to_string(), Some(ROLLUP_20S)),
        "120" => ("2m".to_string(), Some(ROLLUP_2M)),
        "3600" => ("1h".to_string(), Some(ROLLUP_1H)),
        _ => ("auto".to_string(), None),
    }
}

fn auto_rollup_bucket(range_start: f64, range_end: f64) -> i64 {
    if (range_end - range_start).max(0.0) <= JOB_AUTO_2M_THRESHOLD_SECONDS {
        ROLLUP_20S
    } else {
        ROLLUP_2M
    }
}

fn rollup_point_limit(range_start: f64, range_end: f64, bucket_seconds: i64) -> i64 {
    let span = (range_end - range_start).max(0.0);
    let expected = (span / bucket_seconds.max(1) as f64).ceil() as i64 + 4;
    expected.clamp(1000, 20000)
}

fn series_min_time(series: &[Value]) -> Option<f64> {
    series
        .iter()
        .flat_map(|item| item["points"].as_array().into_iter().flatten())
        .filter_map(|point| point["sampled_at"].as_f64())
        .min_by(f64::total_cmp)
}

fn series_max_time(series: &[Value]) -> Option<f64> {
    series
        .iter()
        .flat_map(|item| item["points"].as_array().into_iter().flatten())
        .filter_map(|point| point["sampled_at"].as_f64())
        .max_by(f64::total_cmp)
}

fn gpu_label(gpu: &GpuPayload) -> String {
    let suffix = gpu
        .gpu_index
        .map(|index| format!("GPU{index}"))
        .unwrap_or_else(|| gpu.gpu_uuid.clone());
    format!("{} {}", gpu.node_id, suffix)
}

pub fn gpu_sample_message(snapshot: &NodeSnapshot) -> Value {
    json!({
        "type": "gpu_sample",
        "node_id": snapshot.node_id,
        "sampled_at": snapshot.sampled_at,
        "refresh_interval": snapshot.refresh_interval,
        "gpus": snapshot.gpus.iter().map(|gpu| json!({
            "uuid": gpu.uuid,
            "gpu_index": gpu.index,
            "name": gpu.name,
            "utilization_gpu": gpu.utilization_gpu,
            "utilization_mem": gpu.utilization_mem,
            "memory_used_mb": gpu.memory_used_mb,
            "memory_total_mb": gpu.memory_total_mb,
            "power_watts": gpu.power_watts,
            "temperature_c": gpu.temperature_c,
        })).collect::<Vec<_>>(),
    })
}

fn string_field(value: &Value, key: &str) -> String {
    value[key].as_str().unwrap_or("").to_string()
}

fn round1(value: f64) -> f64 {
    (value * 10.0).round() / 10.0
}

fn format_python_float(value: f64) -> String {
    if value.is_finite() && value.fract() == 0.0 {
        format!("{value:.1}")
    } else {
        value.to_string()
    }
}

fn unix_now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .unwrap_or(0.0)
}
