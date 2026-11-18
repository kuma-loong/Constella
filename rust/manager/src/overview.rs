use crate::lab::ownership;
use crate::{App, analytics, usage};
use axum::extract::{Query, State};
use axum::response::{IntoResponse, Response};
use constella_protocol::unix_time;
use rusqlite::types::Value as SqlValue;
use rusqlite::{Connection, params, params_from_iter};
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

const DAY: f64 = 86_400.0;
const LIMIT: usize = 20;

#[derive(Deserialize)]
pub struct Filters {
    range: Option<String>,
}

pub async fn endpoint(State(app): State<Arc<App>>, Query(filters): Query<Filters>) -> Response {
    let span = analytics::range_seconds(filters.range.as_deref(), 7 * DAY as i64);
    let bindings = if app.lab.is_some() && app.database.is_some() {
        match ownership::bindings(&app).await {
            Ok(bindings) => Some(bindings),
            Err(error) => {
                eprintln!("Lab ranking bindings: {error}");
                return (
                    axum::http::StatusCode::SERVICE_UNAVAILABLE,
                    axum::Json(json!({"detail":"analytics_busy"})),
                )
                    .into_response();
            }
        }
    } else {
        None
    };
    let signature = bindings.as_ref().map(ownership::digest).unwrap_or_default();
    analytics::cached(app, format!("overview:{span}:{signature}"), move |conn| {
        load(conn, span, unix_time(), bindings.as_deref())
    })
    .await
}

#[derive(Default)]
struct User {
    sessions: BTreeSet<String>,
    jobs: BTreeSet<String>,
    last_seen: f64,
    cards: usage::CardUsage,
}

struct Job {
    user: String,
    node: String,
    task: String,
    started: f64,
    last_seen: f64,
    status: String,
    sessions: BTreeSet<String>,
    card_ids: BTreeSet<String>,
    uuids: BTreeSet<String>,
    indices: BTreeSet<i64>,
    pids: BTreeSet<i64>,
    memory_seconds: f64,
    duration_sum: f64,
    intervals: usage::CardUsage,
}

impl Job {
    fn new(
        user: String,
        node: String,
        task: String,
        started: f64,
        last_seen: f64,
        status: String,
    ) -> Self {
        Self {
            user,
            node,
            task,
            started,
            last_seen,
            status,
            sessions: BTreeSet::new(),
            card_ids: BTreeSet::new(),
            uuids: BTreeSet::new(),
            indices: BTreeSet::new(),
            pids: BTreeSet::new(),
            memory_seconds: 0.0,
            duration_sum: 0.0,
            intervals: usage::CardUsage::default(),
        }
    }
}

struct Ranking {
    key: String,
    job: Job,
    hours: f64,
    weighted_hours: f64,
}

fn load(
    conn: &Connection,
    span: i64,
    now: f64,
    bindings: Option<&[ownership::Binding]>,
) -> rusqlite::Result<Value> {
    let start = now - span as f64;
    let (users, jobs) = summarize(conn, start, now, true, bindings)?;
    let anomaly_start = now - DAY;
    let anomaly_jobs = if span == DAY as i64 {
        None
    } else {
        Some(summarize(conn, anomaly_start, now, false, None)?.1)
    };
    let anomalies = anomalies(
        conn,
        anomaly_jobs.as_deref().unwrap_or(&jobs),
        anomaly_start,
        now,
    )?;
    let rankings: Vec<_> = jobs.iter().take(LIMIT).map(|row| {
        let job = &row.job;
        json!({
            "job_key": row.key, "user": job.user, "node_id": job.node, "task_name": job.task,
            "started_at": job.started, "last_seen_at": job.last_seen, "duration_seconds": (job.last_seen - job.started).max(0.0),
            "gpu_count": job.card_ids.len(), "session_count": job.sessions.len(),
            "gpu_hours": analytics::round_decimal(row.hours, 100.0),
            "weighted_gpu_hours": analytics::round_decimal(row.weighted_hours, 100.0),
            "status": job.status,
        })
    }).collect();
    Ok(json!({
        "enabled": true, "generated_at": now, "range_start": start, "range_end": now,
        "timezone": "Asia/Shanghai", "user_gpu_hours": users, "job_rankings": rankings, "anomalies": anomalies,
    }))
}

fn user_rankings(users: BTreeMap<String, User>) -> Vec<Value> {
    let mut ranked: Vec<_> = users
        .into_iter()
        .map(|(name, mut user)| {
            let models = std::mem::take(&mut user.cards).totals();
            let (hours, weighted) = usage::hours(&models);
            (name, user, hours, weighted, models)
        })
        .collect();
    ranked.sort_by(|a, b| b.3.total_cmp(&a.3).then_with(|| a.0.cmp(&b.0)));
    ranked.into_iter().take(LIMIT).map(|(name, user, hrs, weighted, models)| {
        let mut models: Vec<_> = models.into_iter().collect();
        models.sort_by(|a,b| b.1.total_cmp(&a.1));
        let top: Vec<_> = models.into_iter().take(3).map(|(name, seconds)| {
            json!({"name": name, "gpu_hours": analytics::round_decimal(seconds / 3600.0, 100.0)})
        }).collect();
        json!({
            "user": name, "gpu_hours": analytics::round_decimal(hrs, 100.0),
            "weighted_gpu_hours": analytics::round_decimal(weighted, 100.0), "task_count": user.sessions.len(),
            "job_count": user.jobs.len(), "last_seen_at": user.last_seen, "top_gpu_models": top,
        })
    }).collect()
}

fn summarize(
    conn: &Connection,
    start: f64,
    end: f64,
    include_users: bool,
    bindings: Option<&[ownership::Binding]>,
) -> rusqlite::Result<(Vec<Value>, Vec<Ranking>)> {
    let mut stmt = conn.prepare_cached(ownership::USAGE_SQL)?;
    let mut rows = stmt.query(params![start, end])?;
    let mut users: BTreeMap<String, User> = BTreeMap::new();
    let mut jobs: BTreeMap<String, Job> = BTreeMap::new();
    let mut owners = ownership::Rankings::default();
    let identity = bindings.map(|bindings| ownership::History::new(bindings, start));
    while let Some(row) = rows.next()? {
        let row = ownership::UsageRow::read(row)?;
        if let Some(history) = &identity {
            history.split(&row, end, |binding, first, last| {
                owners.add(&row, binding, first, last)
            });
        }
        let first = row.first.max(start);
        let clipped_last = row.last.min(end);
        let seconds = clipped_last - first;
        if seconds <= 0.0 {
            continue;
        }
        let user = row.user.unwrap_or_else(|| "unknown".into());
        if include_users && identity.is_none() {
            let entry = users.entry(user.clone()).or_default();
            entry.cards.add(&row.card, &row.model, first, clipped_last);
            entry.jobs.insert(row.key.clone());
            entry.sessions.insert(row.session.clone());
            entry.last_seen = entry.last_seen.max(row.last);
        }
        let job = match jobs.entry(row.key) {
            std::collections::btree_map::Entry::Occupied(entry) => entry.into_mut(),
            std::collections::btree_map::Entry::Vacant(entry) => entry.insert(Job::new(
                user,
                row.node,
                row.task,
                row.session_start,
                row.session_end,
                row.status.clone(),
            )),
        };
        job.started = job.started.min(row.session_start);
        job.last_seen = job.last_seen.max(row.session_end);
        if row.status == "running" {
            job.status = row.status;
        }
        job.intervals
            .add(&row.card, &row.model, first, clipped_last);
        job.sessions.insert(row.session);
        job.card_ids.insert(row.card);
        job.uuids.insert(row.uuid);
        job.indices.extend(row.index);
        job.pids.insert(row.pid);
        job.memory_seconds += row.memory * seconds;
        job.duration_sum += seconds;
    }
    let mut ranked: Vec<_> = jobs
        .into_iter()
        .map(|(key, mut job)| {
            let (hours, weighted_hours) =
                usage::hours(&std::mem::take(&mut job.intervals).totals());
            Ranking {
                key,
                job,
                hours,
                weighted_hours,
            }
        })
        .collect();
    ranked.sort_by(|a, b| {
        b.weighted_hours
            .total_cmp(&a.weighted_hours)
            .then_with(|| a.key.cmp(&b.key))
    });
    let users = if identity.is_some() {
        owners.finish(start, end)
    } else {
        user_rankings(users)
    };
    Ok((users, ranked))
}

fn avg_utilization(
    conn: &Connection,
    job: &Job,
    since: f64,
    until: f64,
) -> rusqlite::Result<Option<f64>> {
    if job.uuids.is_empty() || since >= until {
        return Ok(None);
    }
    let place = vec!["?"; job.uuids.len()].join(", ");
    let query = format!(
        "SELECT SUM(avg_gpu_utilization * sample_count) / SUM(sample_count)
        FROM gpu_metric_rollups WHERE bucket_seconds = 20 AND node_id = ?
        AND bucket_start BETWEEN ? AND ? AND gpu_uuid IN ({place})"
    );
    let filter = [
        SqlValue::Text(job.node.clone()),
        SqlValue::Real(since),
        SqlValue::Real(until),
    ]
    .into_iter()
    .chain(job.uuids.iter().cloned().map(SqlValue::Text));
    conn.prepare_cached(&query)?
        .query_row(params_from_iter(filter), |row| row.get(0))
}

fn anomalies(
    conn: &Connection,
    jobs: &[Ranking],
    start: f64,
    now: f64,
) -> rusqlite::Result<Vec<Value>> {
    let mut result = Vec::new();
    for row in jobs {
        let job = &row.job;
        let seconds = (job.last_seen - job.started).max(0.0);
        let memory = job.memory_seconds / job.duration_sum;
        if seconds < 7200.0 || memory < 20.0 * 1024.0 {
            continue;
        }
        let recent = avg_utilization(conn, job, now - 3600.0, now)?;
        if recent.is_some_and(|avg| avg >= 5.0) {
            continue;
        }
        let lifetime = avg_utilization(conn, job, start.max(job.started), now.min(job.last_seen))?;
        if let Some(score) = recent.or(lifetime).filter(|score| *score < 5.0) {
            result.push(json!({
                "user": job.user, "node_id": job.node, "task_name": job.task, "duration_seconds": seconds,
                "gpu_memory_gb": analytics::round_decimal(memory / 1024.0, 10.0),
                "recent_avg_gpu_utilization": analytics::round_decimal(score, 10.0),
                "lifetime_avg_gpu_utilization": analytics::round_decimal(lifetime.unwrap_or_default(), 10.0),
                "idle_tail_seconds": if recent.is_some() { 3600 } else { 0 },
                "gpu_uuids": job.uuids, "gpu_indices": job.indices, "pids": job.pids, "last_seen_at": job.last_seen,
                "reason": "long memory-heavy job with low recent GPU utilization",
            }));
            if result.len() == LIMIT {
                break;
            }
        }
    }
    Ok(result)
}
