use crate::App;
use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use constella_protocol::unix_time;
use rusqlite::types::Value as SqlValue;
use rusqlite::{Connection, Row, params_from_iter};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::Arc;

const DAY: f64 = 86_400.0;
const LOOKBACK: f64 = 30.0 * DAY;

#[derive(Default, Deserialize)]
pub struct Filters {
    q: Option<String>,
    user: Option<String>,
    pid: Option<i64>,
    node_id: Option<String>,
    status: Option<String>,
    since: Option<f64>,
    until: Option<f64>,
    max_duration_seconds: Option<f64>,
    recent_seconds: Option<f64>,
    limit: Option<usize>,
}

#[derive(Serialize)]
pub struct Session {
    pub session_id: String,
    pid: i64,
    ppid: Option<i64>,
    task_name: String,
    process_name: String,
    exe: Option<String>,
    cmdline_text: Option<String>,
    started_at: f64,
    last_seen_at: f64,
    status: String,
}

#[derive(Serialize)]
pub struct Device {
    pub node_id: String,
    pub gpu_uuid: String,
    pub gpu_index: Option<i64>,
    gpu_name: Option<String>,
    memory_total_mb: Option<i64>,
}

#[derive(Serialize)]
pub struct Job {
    pub job_key: String,
    pub node_id: String,
    user: String,
    task_name: String,
    pub started_at: f64,
    pub last_seen_at: f64,
    pub duration_seconds: f64,
    status: String,
    pub sessions: Vec<Session>,
    pids: BTreeSet<i64>,
    pub gpus: Vec<Device>,
    gpu_count: usize,
    session_count: usize,
}

struct Group {
    job: Job,
    session_ids: BTreeSet<String>,
    devices: BTreeMap<String, Device>,
}

impl Group {
    fn new(job_key: String, node_id: String, user: String, session: &Session) -> Self {
        Self {
            job: Job {
                job_key,
                node_id,
                user,
                task_name: session.task_name.clone(),
                started_at: session.started_at,
                last_seen_at: session.last_seen_at,
                duration_seconds: 0.0,
                status: session.status.clone(),
                sessions: Vec::new(),
                pids: BTreeSet::new(),
                gpus: Vec::new(),
                gpu_count: 0,
                session_count: 0,
            },
            session_ids: BTreeSet::new(),
            devices: BTreeMap::new(),
        }
    }

    fn add(&mut self, session: Session, device: Device) {
        self.job.started_at = self.job.started_at.min(session.started_at);
        self.job.last_seen_at = self.job.last_seen_at.max(session.last_seen_at);
        if session.status == "running" {
            self.job.status.clone_from(&session.status);
        }
        self.job.pids.insert(session.pid);
        self.devices.insert(device.gpu_uuid.clone(), device);
        if self.session_ids.insert(session.session_id.clone()) {
            self.job.sessions.push(session);
        }
    }

    fn finalize(self) -> Job {
        let mut job = self.job;
        job.duration_seconds = (job.last_seen_at - job.started_at).max(0.0);
        job.sessions
            .sort_by(|a, b| a.started_at.total_cmp(&b.started_at));
        job.gpus = self.devices.into_values().collect();
        job.gpus
            .sort_by_key(|gpu| gpu.gpu_index.unwrap_or(i64::MAX));
        job.gpu_count = job.gpus.len();
        job.session_count = job.sessions.len();
        job
    }
}

pub fn key(
    node: &str,
    user: &str,
    pid: i64,
    ppid: Option<i64>,
    first_seen: f64,
    process_start: Option<f64>,
    parent_start: Option<f64>,
) -> String {
    let started = process_start.filter(|at| *at != 0.0).unwrap_or(first_seen);
    let (at, pid) = match parent_start.zip(ppid) {
        Some((parent, ppid)) if (0.0..=600.0).contains(&(started - parent)) => (parent, ppid),
        _ => (started, pid),
    };
    let at = serde_json::to_string(&at).expect("SQLite timestamp is serializable");
    format!("{node}:{user}:{at}:{pid}")
}

fn query(conn: &Connection, filters: &Filters, owner: Option<i64>) -> rusqlite::Result<Vec<Job>> {
    let earliest = unix_time()
        - filters
            .recent_seconds
            .unwrap_or(7.0 * DAY)
            .clamp(60.0, LOOKBACK);
    let since = filters.since.unwrap_or(earliest).max(earliest);
    let mut clauses = vec!["s.last_seen_at >= ?"];
    let mut values = vec![SqlValue::Real(since)];
    if let Some(end) = filters.until {
        clauses.push("s.first_seen_at <= ?");
        values.push(SqlValue::Real(end));
    }
    if let Some(node) = filters.node_id.as_ref().filter(|node| !node.is_empty()) {
        clauses.push("s.node_id = ?");
        values.push(SqlValue::Text(node.clone()));
    }
    if let Some(user) = filters.user.as_ref().filter(|user| !user.is_empty()) {
        clauses.push(if user == "unknown" {
            "COALESCE(NULLIF(s.user, ''), 'unknown') = ?"
        } else {
            "s.user = ?"
        });
        values.push(SqlValue::Text(user.clone()));
    }
    if let Some(owner) = owner {
        clauses.push("(s.pid = ? OR s.ppid = ?)");
        values.extend([SqlValue::Integer(owner), SqlValue::Integer(owner)]);
    }

    let mut stmt = conn.prepare(&format!(
        "
        SELECT s.session_id, s.pid, s.ppid, s.task_name, s.process_name, s.exe, s.cmdline_text,
            s.first_seen_at, s.last_seen_at, s.status, s.node_id, s.user, s.process_start_time,
            s.parent_start_time, u.gpu_uuid, g.gpu_index, g.name, g.memory_total_mb
        FROM process_sessions s
        JOIN process_gpu_usages u ON u.session_id = s.session_id
        LEFT JOIN gpus g ON g.node_id = u.node_id AND g.uuid = u.gpu_uuid
        WHERE {} ORDER BY s.last_seen_at DESC",
        clauses.join(" AND ")
    ))?;
    let mut rows = stmt.query(params_from_iter(values))?;
    let mut groups: HashMap<String, Group> = HashMap::new();
    while let Some(row) = rows.next()? {
        add_row(&mut groups, row)?;
    }
    let mut jobs: Vec<_> = groups.into_values().map(Group::finalize).collect();
    jobs.sort_by(|a, b| {
        b.last_seen_at
            .total_cmp(&a.last_seen_at)
            .then_with(|| a.job_key.cmp(&b.job_key))
    });
    Ok(jobs)
}

fn add_row(groups: &mut HashMap<String, Group>, row: &Row<'_>) -> rusqlite::Result<()> {
    let session = Session {
        session_id: row.get(0)?,
        pid: row.get(1)?,
        ppid: row.get(2)?,
        task_name: row.get(3)?,
        process_name: row.get(4)?,
        exe: row.get(5)?,
        cmdline_text: row.get(6)?,
        started_at: row.get(7)?,
        last_seen_at: row.get(8)?,
        status: row.get(9)?,
    };
    let node_id: String = row.get(10)?;
    let user = row
        .get::<_, Option<String>>(11)?
        .filter(|user| !user.is_empty())
        .unwrap_or_else(|| "unknown".into());
    let key = key(
        &node_id,
        &user,
        session.pid,
        session.ppid,
        session.started_at,
        row.get(12)?,
        row.get(13)?,
    );
    let device = Device {
        node_id: node_id.clone(),
        gpu_uuid: row.get(14)?,
        gpu_index: row.get(15)?,
        gpu_name: row.get(16)?,
        memory_total_mb: row.get(17)?,
    };
    groups
        .entry(key.clone())
        .or_insert_with(|| Group::new(key, node_id, user, &session))
        .add(session, device);
    Ok(())
}

fn matches(job: &Job, request: &Filters, needle: &str) -> bool {
    if request
        .status
        .as_ref()
        .is_some_and(|status| !status.is_empty() && *status != job.status)
        || request.pid.is_some_and(|pid| !job.pids.contains(&pid))
        || request
            .max_duration_seconds
            .is_some_and(|max| job.duration_seconds > max.clamp(1.0, 7.0 * DAY))
    {
        return false;
    }
    needle.is_empty()
        || job.user.to_lowercase().contains(needle)
        || job.sessions.iter().any(|session| {
            format!(
                "{} {} {} {} {}",
                session.task_name,
                session.process_name,
                session.exe.as_deref().unwrap_or_default(),
                session.cmdline_text.as_deref().unwrap_or_default(),
                session.pid
            )
            .to_lowercase()
            .contains(needle)
        })
}

pub type Reply = Result<Json<Value>, (StatusCode, Json<Value>)>;

pub async fn search(State(app): State<Arc<App>>, Query(request): Query<Filters>) -> Reply {
    let Some(db) = &app.database else {
        return Ok(Json(json!({"enabled": false, "items": []})));
    };
    if [
        request.since,
        request.until,
        request.max_duration_seconds,
        request.recent_seconds,
    ]
    .into_iter()
    .flatten()
    .any(|number| !number.is_finite())
    {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "invalid Jobs time filter"})),
        ));
    }
    let items = db
        .read(move |conn| {
            let needle = request
                .q
                .as_deref()
                .unwrap_or_default()
                .trim()
                .to_lowercase();
            let jobs = query(conn, &request, None)?;
            Ok(jobs
                .into_iter()
                .filter(|job| matches(job, &request, &needle))
                .take(request.limit.unwrap_or(100).clamp(1, 500))
                .map(|job| json!(job))
                .collect::<Vec<_>>())
        })
        .await
        .map_err(read_error)?;
    Ok(Json(json!({"enabled": true, "items": items})))
}

pub fn lookup(conn: &Connection, job_key: &str) -> rusqlite::Result<Option<Job>> {
    let mut parts = job_key.rsplitn(4, ':');
    let (Some(owner), Some(at), Some(user), Some(node)) =
        (parts.next(), parts.next(), parts.next(), parts.next())
    else {
        return Ok(None);
    };
    let (Ok(owner), Ok(at)) = (owner.parse::<i64>(), at.parse::<f64>()) else {
        return Ok(None);
    };
    if !at.is_finite() || owner <= 0 {
        return Ok(None);
    }
    let filters = Filters {
        user: Some(user.into()),
        node_id: Some(node.into()),
        recent_seconds: Some(LOOKBACK),
        ..Filters::default()
    };
    Ok(query(conn, &filters, Some(owner))?
        .into_iter()
        .find(|job| job.job_key == job_key))
}

pub async fn detail(State(app): State<Arc<App>>, Path(job_key): Path<String>) -> Reply {
    let Some(db) = &app.database else {
        return Ok(Json(json!({"enabled": false})));
    };
    let job = db
        .read(move |conn| lookup(conn, &job_key))
        .await
        .map_err(read_error)?;
    job.map(|item| Json(json!({"enabled": true, "item": item})))
        .ok_or_else(|| {
            (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "job not found"})),
            )
        })
}

pub fn read_error(error: crate::Error) -> (StatusCode, Json<Value>) {
    eprintln!("SQLite Jobs query: {error}");
    let status = if error.is::<tokio::sync::TryAcquireError>() {
        StatusCode::SERVICE_UNAVAILABLE
    } else {
        StatusCode::INTERNAL_SERVER_ERROR
    };
    (status, Json(json!({"detail": "Jobs query unavailable"})))
}
