use std::collections::{BTreeMap, BTreeSet};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

use crate::db::{DbError, SQLiteStore, ROLLUP_1H, ROLLUP_20S, ROLLUP_2M};

const TIMEZONE: &str = "Asia/Shanghai";
const JOB_PARENT_GROUP_MAX_AGE_SECONDS: f64 = 10.0 * 60.0;

pub fn gpu_weight(name: Option<&str>) -> f64 {
    let normalized = name.unwrap_or("").to_uppercase().replace("  ", " ");
    if normalized.contains("H100") {
        1.0
    } else if normalized.contains("PRO 6000") {
        0.9
    } else {
        1.0
    }
}

pub fn overlap_seconds(
    first_seen_at: f64,
    last_seen_at: f64,
    range_start: f64,
    range_end: f64,
) -> f64 {
    (last_seen_at.min(range_end) - first_seen_at.max(range_start)).max(0.0)
}

pub fn overview_analytics(
    store: &SQLiteStore,
    range_name: &str,
    now: Option<f64>,
) -> Result<Value, DbError> {
    let range_end = now.unwrap_or_else(unix_now);
    let range_start = range_end - range_seconds(range_name, "7d");
    let rows = usage_rows(store, range_start, range_end)?;
    let (users, jobs) = roll_up_usage(&rows, range_start, range_end);
    let mut user_payloads: Vec<Value> = users.values().map(UserUsage::payload).collect();
    user_payloads.sort_by(|left, right| {
        right["weighted_gpu_hours"]
            .as_f64()
            .unwrap_or(0.0)
            .total_cmp(&left["weighted_gpu_hours"].as_f64().unwrap_or(0.0))
    });
    let mut job_payloads: Vec<Value> = jobs.values().map(JobUsage::payload).collect();
    job_payloads.sort_by(|left, right| {
        right["weighted_gpu_hours"]
            .as_f64()
            .unwrap_or(0.0)
            .total_cmp(&left["weighted_gpu_hours"].as_f64().unwrap_or(0.0))
    });

    let anomaly_start = range_end - range_seconds("24h", "24h");
    let anomaly_rows = usage_rows(store, anomaly_start, range_end)?;
    let (_, anomaly_jobs) = roll_up_usage(&anomaly_rows, anomaly_start, range_end);
    let mut anomaly_candidates: Vec<JobUsage> = anomaly_jobs.into_values().collect();
    anomaly_candidates
        .sort_by(|left, right| right.weighted_gpu_hours.total_cmp(&left.weighted_gpu_hours));

    Ok(json!({
        "enabled": true,
        "generated_at": range_end,
        "range_start": range_start,
        "range_end": range_end,
        "timezone": TIMEZONE,
        "user_gpu_hours": user_payloads.into_iter().take(20).collect::<Vec<_>>(),
        "job_rankings": job_payloads.into_iter().take(20).collect::<Vec<_>>(),
        "anomalies": anomaly_payloads(store, &anomaly_candidates, anomaly_start, range_end)?,
        "off_hours": {
            "night_job_count": 0,
            "weekend_job_count": 0,
            "night_gpu_hours": 0.0,
            "weekend_gpu_hours": 0.0,
            "top_users": [],
        },
    }))
}

pub fn node_analytics(
    store: &SQLiteStore,
    node_id: &str,
    range_name: &str,
    now: Option<f64>,
) -> Result<Value, DbError> {
    let range_end = now.unwrap_or_else(unix_now);
    let range_start = range_end - range_seconds(range_name, "24h");
    let source_bucket = select_rollup_bucket(range_start, range_end);
    let series_bucket = target_bucket(range_end - range_start, source_bucket, 560);
    let heatmap_bucket = heatmap_bucket(range_end - range_start);
    Ok(json!({
        "enabled": true,
        "generated_at": range_end,
        "range_start": range_start,
        "range_end": range_end,
        "timezone": TIMEZONE,
        "bucket_seconds": series_bucket,
        "node_id": node_id,
        "gpus": node_gpus(store, node_id)?,
        "series": rollup_series(store, node_id, range_start, range_end, source_bucket, series_bucket)?,
        "heatmap": rollup_heatmap(store, node_id, range_start, range_end, source_bucket, heatmap_bucket)?,
        "heatmap_bucket_seconds": heatmap_bucket,
    }))
}

#[derive(Debug, Clone)]
struct UsageRow {
    session_id: String,
    node_id: String,
    pid: Option<i64>,
    ppid: Option<i64>,
    process_start_time: Option<f64>,
    parent_start_time: Option<f64>,
    user: Option<String>,
    task_name: String,
    session_first_seen_at: f64,
    session_last_seen_at: f64,
    status: String,
    gpu_uuid: String,
    first_seen_at: f64,
    last_seen_at: f64,
    avg_memory_mb: f64,
    gpu_name: Option<String>,
    gpu_index: Option<i64>,
}

fn usage_rows(
    store: &SQLiteStore,
    range_start: f64,
    range_end: f64,
) -> Result<Vec<UsageRow>, DbError> {
    let mut stmt = store.connection()?.prepare(
        r#"
        SELECT
          s.session_id, s.node_id, s.pid, s.ppid, s.process_start_time, s.parent_start_time,
          s.user, s.task_name, s.first_seen_at AS session_first_seen_at,
          s.last_seen_at AS session_last_seen_at, s.status,
          u.gpu_uuid, u.first_seen_at, u.last_seen_at, u.avg_memory_mb,
          g.name AS gpu_name, g.gpu_index
        FROM process_gpu_usages u
        JOIN process_sessions s ON s.session_id = u.session_id
        LEFT JOIN gpus g ON g.node_id = u.node_id AND g.uuid = u.gpu_uuid
        WHERE u.last_seen_at >= ?1 AND u.first_seen_at <= ?2
        "#,
    )?;
    let rows = stmt
        .query_map([range_start, range_end], |row| {
            Ok(UsageRow {
                session_id: row.get("session_id")?,
                node_id: row.get("node_id")?,
                pid: row.get("pid")?,
                ppid: row.get("ppid")?,
                process_start_time: row.get("process_start_time")?,
                parent_start_time: row.get("parent_start_time")?,
                user: row.get("user")?,
                task_name: row.get("task_name")?,
                session_first_seen_at: row.get("session_first_seen_at")?,
                session_last_seen_at: row.get("session_last_seen_at")?,
                status: row.get("status")?,
                gpu_uuid: row.get("gpu_uuid")?,
                first_seen_at: row.get("first_seen_at")?,
                last_seen_at: row.get("last_seen_at")?,
                avg_memory_mb: row.get("avg_memory_mb")?,
                gpu_name: row.get("gpu_name")?,
                gpu_index: row.get("gpu_index")?,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

#[derive(Debug, Clone, Default)]
struct UserUsage {
    user: String,
    gpu_hours: f64,
    weighted_gpu_hours: f64,
    sessions: BTreeSet<String>,
    jobs: BTreeSet<String>,
    last_seen_at: f64,
    gpu_model_seconds: BTreeMap<String, f64>,
}

#[derive(Debug, Clone)]
struct JobUsage {
    job_key: String,
    user: String,
    node_id: String,
    task_name: String,
    started_at: f64,
    last_seen_at: f64,
    status: String,
    gpu_hours: f64,
    weighted_gpu_hours: f64,
    sessions: BTreeSet<String>,
    gpu_uuids: BTreeSet<String>,
    gpu_indices: BTreeSet<i64>,
    pids: BTreeSet<i64>,
    memory_seconds: f64,
    usage_seconds: f64,
}

impl UserUsage {
    fn payload(&self) -> Value {
        let mut top_gpu_models: Vec<_> = self.gpu_model_seconds.iter().collect();
        top_gpu_models.sort_by(|left, right| right.1.total_cmp(left.1));
        json!({
            "user": self.user,
            "gpu_hours": round2(self.gpu_hours),
            "weighted_gpu_hours": round2(self.weighted_gpu_hours),
            "task_count": self.sessions.len(),
            "job_count": self.jobs.len(),
            "last_seen_at": self.last_seen_at,
            "top_gpu_models": top_gpu_models.into_iter().take(3).map(|(name, seconds)| json!({
                "name": name,
                "gpu_hours": round2(*seconds / 3600.0),
            })).collect::<Vec<_>>(),
        })
    }
}

impl JobUsage {
    fn avg_memory_mb(&self) -> f64 {
        if self.usage_seconds <= 0.0 {
            0.0
        } else {
            self.memory_seconds / self.usage_seconds
        }
    }

    fn payload(&self) -> Value {
        json!({
            "job_key": self.job_key,
            "user": self.user,
            "node_id": self.node_id,
            "task_name": self.task_name,
            "started_at": self.started_at,
            "last_seen_at": self.last_seen_at,
            "duration_seconds": (self.last_seen_at - self.started_at).max(0.0),
            "gpu_count": self.gpu_uuids.len(),
            "session_count": self.sessions.len(),
            "gpu_hours": round2(self.gpu_hours),
            "weighted_gpu_hours": round2(self.weighted_gpu_hours),
            "status": self.status,
        })
    }
}

fn roll_up_usage(
    rows: &[UsageRow],
    range_start: f64,
    range_end: f64,
) -> (BTreeMap<String, UserUsage>, BTreeMap<String, JobUsage>) {
    let mut users = BTreeMap::new();
    let mut jobs = BTreeMap::new();
    for row in rows {
        let seconds = overlap_seconds(row.first_seen_at, row.last_seen_at, range_start, range_end);
        if seconds <= 0.0 {
            continue;
        }
        let weight = gpu_weight(row.gpu_name.as_deref());
        let user = row.user.clone().unwrap_or_else(|| "unknown".to_string());
        let key = job_key(row);
        let user_usage = users.entry(user.clone()).or_insert_with(|| UserUsage {
            user: user.clone(),
            ..Default::default()
        });
        user_usage.gpu_hours += seconds / 3600.0;
        user_usage.weighted_gpu_hours += seconds * weight / 3600.0;
        user_usage.sessions.insert(row.session_id.clone());
        user_usage.jobs.insert(key.clone());
        user_usage.last_seen_at = user_usage.last_seen_at.max(row.last_seen_at);
        *user_usage
            .gpu_model_seconds
            .entry(compact_gpu_name(row.gpu_name.as_deref()))
            .or_default() += seconds;

        let job = jobs.entry(key.clone()).or_insert_with(|| JobUsage {
            job_key: key,
            user: user.clone(),
            node_id: row.node_id.clone(),
            task_name: row.task_name.clone(),
            started_at: row.session_first_seen_at,
            last_seen_at: row.session_last_seen_at,
            status: row.status.clone(),
            gpu_hours: 0.0,
            weighted_gpu_hours: 0.0,
            sessions: BTreeSet::new(),
            gpu_uuids: BTreeSet::new(),
            gpu_indices: BTreeSet::new(),
            pids: BTreeSet::new(),
            memory_seconds: 0.0,
            usage_seconds: 0.0,
        });
        job.started_at = job.started_at.min(row.session_first_seen_at);
        job.last_seen_at = job.last_seen_at.max(row.session_last_seen_at);
        if row.status == "running" {
            job.status = "running".to_string();
        }
        job.gpu_hours += seconds / 3600.0;
        job.weighted_gpu_hours += seconds * weight / 3600.0;
        job.sessions.insert(row.session_id.clone());
        job.gpu_uuids.insert(row.gpu_uuid.clone());
        if let Some(index) = row.gpu_index {
            job.gpu_indices.insert(index);
        }
        if let Some(pid) = row.pid {
            job.pids.insert(pid);
        }
        job.memory_seconds += row.avg_memory_mb * seconds;
        job.usage_seconds += seconds;
    }
    (users, jobs)
}

fn anomaly_payloads(
    store: &SQLiteStore,
    jobs: &[JobUsage],
    range_start: f64,
    range_end: f64,
) -> Result<Vec<Value>, DbError> {
    let mut items = Vec::new();
    let recent_start = range_start.max(range_end - 3600.0);
    for job in jobs {
        let duration = (job.last_seen_at - job.started_at).max(0.0);
        if duration < 7200.0 || job.avg_memory_mb() < 20.0 * 1024.0 {
            continue;
        }
        let recent_avg =
            avg_gpu_utilization(store, &job.node_id, &job.gpu_uuids, recent_start, range_end)?;
        let lifetime_avg = avg_gpu_utilization(
            store,
            &job.node_id,
            &job.gpu_uuids,
            range_start.max(job.started_at),
            range_end.min(job.last_seen_at),
        )?;
        let decision_avg = recent_avg.or(lifetime_avg);
        if decision_avg.is_none_or(|value| value >= 5.0) {
            continue;
        }
        items.push(json!({
            "user": job.user,
            "node_id": job.node_id,
            "task_name": job.task_name,
            "duration_seconds": duration,
            "gpu_memory_gb": round1(job.avg_memory_mb() / 1024.0),
            "recent_avg_gpu_utilization": round1(recent_avg.unwrap_or(decision_avg.unwrap_or(0.0))),
            "lifetime_avg_gpu_utilization": round1(lifetime_avg.unwrap_or(0.0)),
            "idle_tail_seconds": if recent_avg.is_some() { 3600 } else { 0 },
            "gpu_uuids": job.gpu_uuids.iter().collect::<Vec<_>>(),
            "gpu_indices": job.gpu_indices.iter().collect::<Vec<_>>(),
            "pids": job.pids.iter().collect::<Vec<_>>(),
            "last_seen_at": job.last_seen_at,
            "reason": "long memory-heavy job with low recent GPU utilization",
        }));
        if items.len() >= 20 {
            break;
        }
    }
    Ok(items)
}

fn avg_gpu_utilization(
    store: &SQLiteStore,
    node_id: &str,
    gpu_uuids: &BTreeSet<String>,
    since: f64,
    until: f64,
) -> Result<Option<f64>, DbError> {
    if gpu_uuids.is_empty() || until <= since {
        return Ok(None);
    }
    let bucket = select_rollup_bucket(since, until);
    let placeholders = std::iter::repeat("?")
        .take(gpu_uuids.len())
        .collect::<Vec<_>>()
        .join(",");
    let sql = format!(
        "SELECT SUM(avg_gpu_utilization * sample_count) / SUM(sample_count) AS value \
         FROM gpu_metric_rollups WHERE bucket_seconds = ? AND node_id = ? \
         AND gpu_uuid IN ({placeholders}) AND bucket_start >= ? AND bucket_start <= ?"
    );
    let mut values: Vec<rusqlite::types::Value> = vec![bucket.into(), node_id.to_string().into()];
    values.extend(gpu_uuids.iter().cloned().map(Into::into));
    values.push(since.into());
    values.push(until.into());
    let value = store
        .connection()?
        .query_row(&sql, rusqlite::params_from_iter(values), |row| row.get(0))
        .unwrap_or(None);
    Ok(value)
}

fn node_gpus(store: &SQLiteStore, node_id: &str) -> Result<Vec<Value>, DbError> {
    let mut stmt = store.connection()?.prepare(
        "SELECT uuid, gpu_index, name, memory_total_mb FROM gpus WHERE node_id = ? ORDER BY gpu_index ASC",
    )?;
    let rows = stmt
        .query_map([node_id], |row| {
            Ok(json!({
                "uuid": row.get::<_, String>("uuid")?,
                "gpu_index": row.get::<_, i64>("gpu_index")?,
                "name": row.get::<_, String>("name")?,
                "memory_total_mb": row.get::<_, i64>("memory_total_mb")?,
            }))
        })?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

fn rollup_series(
    store: &SQLiteStore,
    node_id: &str,
    range_start: f64,
    range_end: f64,
    source_bucket: i64,
    target_bucket: i64,
) -> Result<Vec<Value>, DbError> {
    let rows = grouped_rollups(
        store,
        node_id,
        range_start,
        range_end,
        source_bucket,
        target_bucket,
    )?;
    let mut by_gpu: BTreeMap<String, Value> = BTreeMap::new();
    for row in rows {
        let entry = by_gpu.entry(row.gpu_uuid.clone()).or_insert_with(|| {
            json!({
                "gpu_uuid": row.gpu_uuid,
                "gpu_index": row.gpu_index,
                "gpu_name": row.gpu_name,
                "points": [],
            })
        });
        entry["points"].as_array_mut().unwrap().push(row.point());
    }
    Ok(by_gpu.into_values().collect())
}

fn rollup_heatmap(
    store: &SQLiteStore,
    node_id: &str,
    range_start: f64,
    range_end: f64,
    source_bucket: i64,
    target_bucket: i64,
) -> Result<Vec<Value>, DbError> {
    let rows = grouped_rollups(
        store,
        node_id,
        range_start,
        range_end,
        source_bucket,
        target_bucket,
    )?;
    let mut by_gpu: BTreeMap<String, Value> = BTreeMap::new();
    for row in rows {
        let entry = by_gpu.entry(row.gpu_uuid.clone()).or_insert_with(|| {
            json!({
                "gpu_uuid": row.gpu_uuid,
                "gpu_index": row.gpu_index,
                "gpu_name": row.gpu_name,
                "buckets": [],
            })
        });
        entry["buckets"].as_array_mut().unwrap().push(json!({
            "bucket_start": row.bucket_start,
            "avg_gpu_utilization": round1(row.avg_gpu_utilization),
            "max_gpu_utilization": round1(row.max_gpu_utilization),
            "avg_memory_used_mb": round1(row.avg_memory_used_mb),
            "sample_count": row.sample_count,
        }));
    }
    Ok(by_gpu.into_values().collect())
}

#[derive(Debug, Clone)]
struct GroupedRollup {
    bucket_start: f64,
    gpu_uuid: String,
    gpu_index: Option<i64>,
    gpu_name: Option<String>,
    avg_gpu_utilization: f64,
    max_gpu_utilization: f64,
    avg_memory_used_mb: f64,
    max_memory_used_mb: i64,
    avg_power_watts: f64,
    max_power_watts: f64,
    avg_temperature_c: f64,
    max_temperature_c: i64,
    sample_count: i64,
}

impl GroupedRollup {
    fn point(&self) -> Value {
        json!({
            "bucket_start": self.bucket_start,
            "avg_gpu_utilization": round1(self.avg_gpu_utilization),
            "max_gpu_utilization": round1(self.max_gpu_utilization),
            "avg_memory_used_mb": round1(self.avg_memory_used_mb),
            "max_memory_used_mb": self.max_memory_used_mb,
            "avg_power_watts": round1(self.avg_power_watts),
            "max_power_watts": round1(self.max_power_watts),
            "avg_temperature_c": round1(self.avg_temperature_c),
            "max_temperature_c": self.max_temperature_c,
            "sample_count": self.sample_count,
        })
    }
}

fn grouped_rollups(
    store: &SQLiteStore,
    node_id: &str,
    range_start: f64,
    range_end: f64,
    source_bucket: i64,
    target_bucket: i64,
) -> Result<Vec<GroupedRollup>, DbError> {
    let mut stmt = store.connection()?.prepare(
        r#"
        SELECT
          CAST(r.bucket_start / ?1 AS INTEGER) * ?2 AS bucket_start,
          r.gpu_uuid,
          g.gpu_index,
          g.name AS gpu_name,
          SUM(r.avg_gpu_utilization * r.sample_count) / SUM(r.sample_count) AS avg_gpu_utilization,
          MAX(r.max_gpu_utilization) AS max_gpu_utilization,
          SUM(r.avg_memory_used_mb * r.sample_count) / SUM(r.sample_count) AS avg_memory_used_mb,
          MAX(r.max_memory_used_mb) AS max_memory_used_mb,
          SUM(r.avg_power_watts * r.sample_count) / SUM(r.sample_count) AS avg_power_watts,
          MAX(r.max_power_watts) AS max_power_watts,
          SUM(r.avg_temperature_c * r.sample_count) / SUM(r.sample_count) AS avg_temperature_c,
          MAX(r.max_temperature_c) AS max_temperature_c,
          SUM(r.sample_count) AS sample_count
        FROM gpu_metric_rollups r
        LEFT JOIN gpus g ON g.node_id = r.node_id AND g.uuid = r.gpu_uuid
        WHERE r.bucket_seconds = ?3
          AND r.node_id = ?4
          AND r.bucket_start >= ?5
          AND r.bucket_start <= ?6
        GROUP BY CAST(r.bucket_start / ?7 AS INTEGER) * ?8, r.gpu_uuid
        ORDER BY bucket_start ASC, g.gpu_index ASC
        "#,
    )?;
    let rows = stmt
        .query_map(
            rusqlite::params![
                target_bucket,
                target_bucket,
                source_bucket,
                node_id,
                range_start,
                range_end,
                target_bucket,
                target_bucket
            ],
            |row| {
                Ok(GroupedRollup {
                    bucket_start: row.get("bucket_start")?,
                    gpu_uuid: row.get("gpu_uuid")?,
                    gpu_index: row.get("gpu_index")?,
                    gpu_name: row.get("gpu_name")?,
                    avg_gpu_utilization: row.get("avg_gpu_utilization")?,
                    max_gpu_utilization: row.get("max_gpu_utilization")?,
                    avg_memory_used_mb: row.get("avg_memory_used_mb")?,
                    max_memory_used_mb: row.get("max_memory_used_mb")?,
                    avg_power_watts: row.get("avg_power_watts")?,
                    max_power_watts: row.get("max_power_watts")?,
                    avg_temperature_c: row.get("avg_temperature_c")?,
                    max_temperature_c: row.get("max_temperature_c")?,
                    sample_count: row.get("sample_count")?,
                })
            },
        )?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

fn job_key(row: &UsageRow) -> String {
    let process_start = row.process_start_time.or(Some(row.first_seen_at));
    let parent_start = row.parent_start_time;
    let (start, owner) = if let (Some(process_start), Some(parent_start), Some(ppid)) =
        (process_start, parent_start, row.ppid)
    {
        if (0.0..=JOB_PARENT_GROUP_MAX_AGE_SECONDS).contains(&(process_start - parent_start)) {
            (Some(parent_start), Some(ppid))
        } else {
            (Some(process_start), row.pid.or(row.ppid))
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

fn compact_gpu_name(name: Option<&str>) -> String {
    name.unwrap_or("unknown").replace("NVIDIA ", "")
}

fn range_seconds(name: &str, default: &str) -> f64 {
    match name {
        "1h" => 60.0 * 60.0,
        "24h" => 24.0 * 60.0 * 60.0,
        "7d" => 7.0 * 24.0 * 60.0 * 60.0,
        "30d" => 30.0 * 24.0 * 60.0 * 60.0,
        _ => range_seconds(default, "7d"),
    }
}

fn select_rollup_bucket(range_start: f64, range_end: f64) -> i64 {
    let span = range_end - range_start;
    if span <= 7.0 * 24.0 * 60.0 * 60.0 {
        ROLLUP_20S
    } else if span <= 60.0 * 24.0 * 60.0 * 60.0 {
        ROLLUP_2M
    } else {
        ROLLUP_1H
    }
}

fn target_bucket(span: f64, source_bucket: i64, target_points: i64) -> i64 {
    let wanted = (span / target_points as f64)
        .ceil()
        .max(source_bucket as f64);
    ((wanted / source_bucket as f64).ceil() as i64) * source_bucket
}

fn heatmap_bucket(span: f64) -> i64 {
    if span <= 60.0 * 60.0 {
        5 * 60
    } else if span <= 24.0 * 60.0 * 60.0 {
        60 * 60
    } else if span <= 7.0 * 24.0 * 60.0 * 60.0 {
        6 * 60 * 60
    } else {
        24 * 60 * 60
    }
}

fn round1(value: f64) -> f64 {
    (value * 10.0).round() / 10.0
}

fn round2(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
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
