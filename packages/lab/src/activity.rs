use super::{Session, ownership};
use crate::{App, cluster::ObservedNode, usage};
use axum::body::Bytes;
use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::{Extension, Json};
use base64::{Engine, engine::general_purpose::URL_SAFE};
use chrono::{Days, NaiveDate};
use constella_protocol::unix_time;
use rusqlite::types::Value as SqlValue;
use rusqlite::{Connection, params_from_iter};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;
use std::time::{Duration, Instant};

const MAX_ROWS: usize = 100_000;

type Response = Result<Json<Value>, (StatusCode, Json<Value>)>;

#[derive(Deserialize)]
pub struct QueryArgs {
    range: Option<String>,
    date: Option<String>,
    gpu_model: Option<String>,
    status: Option<String>,
    q: Option<String>,
    cursor: Option<String>,
    limit: Option<usize>,
}

#[derive(Serialize)]
struct Selection {
    date: Option<String>,
    model: String,
    query: String,
}

#[derive(Serialize, Deserialize)]
struct Prepared {
    response: Value,
    jobs: Vec<Job>,
    tracks: Vec<Job>,
}

struct Record {
    row: Arc<ownership::UsageRow>,
    start: f64,
    end: f64,
}

#[derive(Clone, Serialize, Deserialize)]
struct Interval {
    start: f64,
    end: f64,
    model: String,
    card: String,
}

#[derive(Serialize, Deserialize)]
struct SessionIdentity {
    pid: i64,
    process_start_time: Option<f64>,
}

#[derive(Serialize, Deserialize)]
struct Job {
    job_key: String,
    node_id: String,
    user: Option<String>,
    task_name: String,
    pids: BTreeSet<i64>,
    sessions: Vec<SessionIdentity>,
    started_at: f64,
    last_seen_at: f64,
    status: String,
    models: BTreeSet<String>,
    gpu_count: usize,
    segments: Vec<usage::Segment>,
    intervals: Vec<Interval>,
}

#[derive(Clone, Copy)]
struct Day {
    label: NaiveDate,
    start: f64,
    end: f64,
}

fn error(status: StatusCode, detail: &str) -> (StatusCode, Json<Value>) {
    (status, Json(json!({"detail": detail})))
}

pub async fn endpoint(
    State(app): State<Arc<App>>,
    Extension(session): Extension<Session>,
    Query(args): Query<QueryArgs>,
) -> Response {
    if session.user.role == "viewer" {
        return Err(error(StatusCode::FORBIDDEN, "member_required"));
    }
    let period = match args.range.as_deref().unwrap_or("7d") {
        "7d" => 7,
        "30d" => 30,
        _ => {
            return Err(error(
                StatusCode::UNPROCESSABLE_ENTITY,
                "invalid_activity_range",
            ));
        }
    };
    let status = args.status.as_deref().unwrap_or("all");
    let limit = args.limit.unwrap_or(20);
    if !matches!(status, "all" | "running" | "ended" | "unknown")
        || !(1..=100).contains(&limit)
        || args
            .q
            .as_ref()
            .is_some_and(|value| value.chars().count() > 200)
        || args.cursor.as_ref().is_some_and(|value| value.len() > 500)
    {
        return Err(error(
            StatusCode::UNPROCESSABLE_ENTITY,
            "invalid_activity_filter",
        ));
    }
    if app.database.is_none() {
        return Ok(Json(
            json!({"enabled":false,"availability":"history_disabled"}),
        ));
    }
    let now = unix_time();
    let days = local_days(now, period);
    let selection = Selection {
        date: args.date.filter(|value| !value.is_empty()),
        model: args.gpu_model.unwrap_or_default(),
        query: args.q.unwrap_or_default().trim().to_lowercase(),
    };
    let day = if let Some(date) = &selection.date {
        Some(
            days.iter()
                .find(|day| day.label.to_string() == *date)
                .copied()
                .ok_or_else(|| error(StatusCode::UNPROCESSABLE_ENTITY, "date_outside_period"))?,
        )
    } else {
        None
    };
    let bindings = ownership::bindings(&app).await.map_err(|failure| {
        eprintln!("Lab activity bindings: {failure}");
        error(
            StatusCode::SERVICE_UNAVAILABLE,
            "activity_temporarily_unavailable",
        )
    })?;
    let owner = session.user.id;
    let context = ownership::digest((
        &owner,
        period,
        days[period - 1].label.to_string(),
        &bindings,
        &selection,
    ));
    let cache_key = format!("activity:{context}");
    let cache = app.analytics_cache.clone();
    let query_app = app.clone();
    let load_selection = Selection {
        date: selection.date.clone(),
        model: selection.model.clone(),
        query: selection.query.clone(),
    };
    let bytes = cache
        .read(cache_key, async move {
            query_app
                .database
                .as_ref()
                .expect("activity enabled")
                .read(move |conn| {
                    let transaction = conn.unchecked_transaction()?;
                    let prepared = prepare(
                        &transaction,
                        &bindings,
                        &owner,
                        &days,
                        now,
                        day,
                        &load_selection,
                    )?;
                    transaction.commit()?;
                    serde_json::to_vec(&prepared)
                        .map(Bytes::from)
                        .map_err(|error| rusqlite::Error::ToSqlConversionFailure(Box::new(error)))
                })
                .await
                .map_err(|failure| {
                    if failure.is::<tokio::sync::TryAcquireError>() {
                        StatusCode::SERVICE_UNAVAILABLE
                    } else {
                        eprintln!("Lab activity history: {failure}");
                        StatusCode::INTERNAL_SERVER_ERROR
                    }
                })
        })
        .await
        .map_err(|status| {
            error(
                StatusCode::SERVICE_UNAVAILABLE,
                if status == StatusCode::SERVICE_UNAVAILABLE {
                    "activity_busy"
                } else {
                    "activity_temporarily_unavailable"
                },
            )
        })?;

    let nodes = app
        .cluster
        .observed_processes(app.settings.borrow().effective(), unix_time());
    let readers = app
        .lab
        .as_ref()
        .expect("activity Lab enabled")
        .activity_readers
        .clone();
    let permit = readers
        .try_acquire_owned()
        .map_err(|_| error(StatusCode::SERVICE_UNAVAILABLE, "activity_busy"))?;
    let status = status.to_owned();
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        let prepared = serde_json::from_slice::<Prepared>(&bytes).map_err(|_| {
            error(
                StatusCode::SERVICE_UNAVAILABLE,
                "activity_temporarily_unavailable",
            )
        })?;
        render(
            prepared,
            nodes,
            day,
            selection,
            Page {
                context: &context,
                status: &status,
                cursor: args.cursor,
                limit,
            },
        )
    })
    .await
    .map_err(|_| {
        error(
            StatusCode::SERVICE_UNAVAILABLE,
            "activity_temporarily_unavailable",
        )
    })?
}

fn local_days(now: f64, period: usize) -> Vec<Day> {
    let today = ownership::date(now);
    let first = today
        .checked_sub_days(Days::new((period - 1) as u64))
        .unwrap();
    (0..period)
        .map(|index| {
            let label = first.checked_add_days(Days::new(index as u64)).unwrap();
            Day {
                label,
                start: ownership::midnight(label),
                end: ownership::midnight(label.succ_opt().unwrap()).min(now),
            }
        })
        .collect()
}

fn history(
    conn: &Connection,
    ownership: &ownership::History<'_>,
    owner: &str,
    start: f64,
    now: f64,
) -> rusqlite::Result<(Vec<Record>, usize)> {
    let deadline = Instant::now() + Duration::from_secs(2);
    conn.progress_handler(10_000, Some(move || Instant::now() > deadline));
    let result = (|| {
        let mut records = Vec::new();
        let mut legacy = BTreeSet::new();
        let mut total = 0;
        for (node, ids) in ownership.nodes(owner) {
            // Chunk parameters for old databases with many historical owners per node.
            // Legacy rows are included once for each node.
            for (index, chunk) in ids.chunks(500).enumerate() {
                let placeholders = vec!["?"; chunk.len()].join(",");
                let legacy_clause = if index == 0 {
                    " OR s.user_uid IS NULL"
                } else {
                    ""
                };
                let mut statement = conn.prepare(&format!(
                    "{} AND u.node_id=? AND
                    (s.user_uid IN ({placeholders}){legacy_clause}) LIMIT ?",
                    ownership::USAGE_SQL
                ))?;
                let values = [
                    SqlValue::Real(start),
                    SqlValue::Real(now),
                    SqlValue::Text(node.to_owned()),
                ]
                .into_iter()
                .chain(chunk.iter().copied().map(SqlValue::Integer))
                .chain([SqlValue::Integer((MAX_ROWS + 1 - total) as i64)]);
                let mut cursor = statement.query(params_from_iter(values))?;
                while let Some(row) = cursor.next()? {
                    total += 1;
                    if total > MAX_ROWS {
                        return Err(rusqlite::Error::InvalidQuery);
                    }
                    let row = Arc::new(ownership::UsageRow::read(row)?);
                    if row.uid.is_none() {
                        legacy.insert(row.session.clone());
                    }
                    ownership.split(&row, now, |binding, start, end| {
                        if binding.is_some_and(|binding| binding.owned_by(owner)) {
                            records.push(Record {
                                row: row.clone(),
                                start,
                                end,
                            });
                        }
                    });
                }
            }
        }
        Ok((records, legacy.len()))
    })();
    conn.progress_handler(0, None::<fn() -> bool>);
    result
}

fn prepare(
    conn: &Connection,
    bindings: &[ownership::Binding],
    owner: &str,
    days: &[Day],
    now: f64,
    selected_day: Option<Day>,
    selection: &Selection,
) -> rusqlite::Result<Prepared> {
    let start = days[0].start;
    let ownership = ownership::History::new(bindings, start);
    let (records, legacy) = history(conn, &ownership, owner, start, now)?;
    let all: Vec<_> = records.iter().collect();
    let selected: Vec<_> = all
        .iter()
        .copied()
        .filter(|record| selection.model.is_empty() || record.row.model == selection.model)
        .collect();
    let mut model_usage = usage::CardUsage::default();
    for record in &records {
        model_usage.add(
            &record.row.card,
            &record.row.model,
            record.start,
            record.end,
        );
    }
    let range = selected_day.map_or((start, now), |day| (day.start, day.end));
    let (selection_summary, _) = summarize(&selected, range.0, range.1);
    let (summary, _) = summarize(&all, start, now);
    let day_values = days
        .iter()
        .map(|day| {
            let (mut value, segments) = summarize(&selected, day.start, day.end);
            value["date"] = json!(day.label.to_string());
            value["start"] = json!(day.start);
            value["end"] = json!(day.end);
            value["segments"] = json!(segments);
            value
        })
        .collect::<Vec<_>>();
    let tracks = selected_day.map_or_else(Vec::new, |day| {
        jobs(
            selected
                .iter()
                .copied()
                .filter(|r| in_day(r.start, r.end, day)),
        )
    });
    let mut jobs = jobs(all.into_iter());
    if !selection.query.is_empty() {
        jobs.retain(|job| {
            let pids = job
                .pids
                .iter()
                .map(ToString::to_string)
                .collect::<Vec<_>>()
                .join(" ");
            format!(
                "{} {} {} {}",
                job.task_name,
                job.node_id,
                job.user.as_deref().unwrap_or("None"),
                pids
            )
            .to_lowercase()
            .contains(&selection.query)
        });
    }
    let spans = usage::segments(
        selected
            .iter()
            .map(|r| (r.row.card.as_str(), r.start, r.end)),
        start,
        now,
    );
    let busy = busiest(days, &spans, &ownership.eligible(owner, now));
    Ok(Prepared {
        response: json!({
            "enabled":true,"as_of":now,"range_start":start,"timezone":"Asia/Shanghai",
            "accuracy":"observed_intervals","coverage":"unknown","missing_uid_sessions":legacy,
            "summary":summary,"selection":selection_summary,"days":day_values,
            "gpu_models":ownership::models(model_usage),"busiest_hours":busy,
            "availability": if bindings.iter().any(|binding| binding.owned_by(owner)) { "ready" } else { "unbound" },
        }),
        jobs,
        tracks,
    })
}

fn in_day(start: f64, end: f64, day: Day) -> bool {
    start < day.end && (end > day.start || (start == end && end == day.start))
}

fn summarize(records: &[&Record], start: f64, end: f64) -> (Value, Vec<usage::Segment>) {
    let spans = usage::segments(
        records
            .iter()
            .map(|r| (r.row.card.as_str(), r.start, r.end)),
        start,
        end,
    );
    let (gpu, active) = ownership::segment_hours(&spans);
    let mut days = BTreeSet::new();
    for record in records {
        let (a, b) = (record.start.max(start), record.end.min(end));
        if a < end && (b > a || (b == a && record.start == record.end)) {
            ownership::add_days(&mut days, a, b);
        }
    }
    (
        json!({"gpu_hours":gpu,"active_hours":active,"active_days":days.len()}),
        spans,
    )
}

fn jobs<'a>(records: impl Iterator<Item = &'a Record>) -> Vec<Job> {
    let mut groups = BTreeMap::<&str, Vec<_>>::new();
    for record in records {
        groups.entry(&record.row.key).or_default().push(record);
    }
    groups
        .into_iter()
        .map(|(key, items)| {
            let row = &items[0].row;
            let mut seen = BTreeSet::new();
            let sessions = items
                .iter()
                .filter(|r| seen.insert(r.row.session.as_str()))
                .map(|r| SessionIdentity {
                    pid: r.row.pid,
                    process_start_time: r.row.process_start,
                })
                .collect();
            let start = items.iter().map(|r| r.start).fold(f64::INFINITY, f64::min);
            let end = items
                .iter()
                .map(|r| r.end)
                .fold(f64::NEG_INFINITY, f64::max);
            let segments = usage::segments(
                items.iter().map(|r| (r.row.card.as_str(), r.start, r.end)),
                start,
                end,
            );
            Job {
                job_key: key.to_owned(),
                node_id: row.node.clone(),
                user: row.user.clone(),
                task_name: row.task.clone(),
                pids: items.iter().map(|r| r.row.pid).collect(),
                sessions,
                started_at: items
                    .iter()
                    .map(|r| r.row.session_start)
                    .fold(f64::INFINITY, f64::min),
                last_seen_at: items
                    .iter()
                    .map(|r| r.row.session_end)
                    .fold(f64::NEG_INFINITY, f64::max),
                status: if items.iter().any(|r| r.row.status == "running") {
                    "running"
                } else {
                    "ended"
                }
                .into(),
                models: items.iter().map(|r| r.row.model.clone()).collect(),
                gpu_count: items
                    .iter()
                    .map(|r| &r.row.card)
                    .collect::<BTreeSet<_>>()
                    .len(),
                segments,
                intervals: items
                    .iter()
                    .map(|r| Interval {
                        start: r.start,
                        end: r.end,
                        card: r.row.card.clone(),
                        model: r.row.model.clone(),
                    })
                    .collect(),
            }
        })
        .collect()
}

fn busiest(days: &[Day], spans: &[usage::Segment], eligible: &[(f64, f64)]) -> Option<Value> {
    let mut usage = [0.0; 24];
    let mut exposure = [0.0; 24];
    let overlap = |a: f64, b: f64, c: f64, d: f64| (b.min(d) - a.max(c)).max(0.0);
    for day in days {
        for hour in 0..24 {
            let a = day.start + hour as f64 * 3600.0;
            let b = day.end.min(a + 3600.0);
            if a >= b {
                break;
            }
            let first = spans.partition_point(|span| span.end <= a);
            usage[hour] += spans[first..]
                .iter()
                .take_while(|span| span.start < b)
                .map(|span| overlap(a, b, span.start, span.end) * f64::from(span.count))
                .sum::<f64>();
            let first = eligible.partition_point(|&(_, end)| end <= a);
            exposure[hour] += eligible[first..]
                .iter()
                .take_while(|&&(start, _)| start < b)
                .map(|&(start, end)| overlap(a, b, start, end))
                .sum::<f64>();
        }
    }
    let avg = |hour: usize| {
        if exposure[hour % 24] > 0.0 {
            usage[hour % 24] / exposure[hour % 24]
        } else {
            0.0
        }
    };
    let (hour, score) = (0..24)
        .map(|hour| (hour, avg(hour) + avg(hour + 1) + avg(hour + 2)))
        .max_by(|a, b| a.1.total_cmp(&b.1).then(b.0.cmp(&a.0)))?;
    (score > 0.0).then(|| json!({"start_hour":hour,"end_hour":(hour+3)%24}))
}

fn refresh(jobs: &mut [Job], nodes: &BTreeMap<String, ObservedNode>) {
    for job in &mut *jobs {
        let state = if let Some(node) = nodes.get(&job.node_id) {
            if job.sessions.iter().any(|session| {
                u32::try_from(session.pid)
                    .is_ok_and(|pid| node.processes.contains(&(pid, session.process_start_time)))
            }) {
                "running"
            } else if node.complete {
                "ended"
            } else {
                "unknown"
            }
        } else if job.status == "ended" {
            "ended"
        } else {
            "unknown"
        };
        job.status = state.to_owned();
    }
    jobs.sort_by(|a, b| {
        (a.status != "running")
            .cmp(&(b.status != "running"))
            .then_with(|| b.last_seen_at.total_cmp(&a.last_seen_at))
            .then_with(|| a.job_key.cmp(&b.job_key))
    });
}

struct Page<'a> {
    context: &'a str,
    status: &'a str,
    cursor: Option<String>,
    limit: usize,
}

fn render(
    mut prepared: Prepared,
    nodes: BTreeMap<String, ObservedNode>,
    day: Option<Day>,
    selection: Selection,
    page: Page<'_>,
) -> Response {
    refresh(&mut prepared.jobs, &nodes);
    refresh(&mut prepared.tracks, &nodes);
    let matching: Vec<_> = prepared
        .jobs
        .into_iter()
        .filter(|job| {
            (page.status == "all" || job.status == page.status)
                && (job.status == "running"
                    || job.intervals.iter().any(|interval| {
                        (selection.model.is_empty() || interval.model == selection.model)
                            && day.is_none_or(|day| in_day(interval.start, interval.end, day))
                    }))
        })
        .collect();
    let keys: Vec<_> = matching.iter().map(|job| &job.job_key).collect();
    let signature =
        ownership::digest((page.context, &prepared.response["as_of"], page.status, keys));
    let signature = &signature[..20];
    let mut offset = 0;
    if let Some(cursor) = page.cursor.filter(|cursor| !cursor.is_empty()) {
        let invalid = || error(StatusCode::UNPROCESSABLE_ENTITY, "invalid_cursor");
        let decoded = URL_SAFE.decode(cursor).map_err(|_| invalid())?;
        let (token, index) =
            serde_json::from_slice::<(String, usize)>(&decoded).map_err(|_| invalid())?;
        if token != signature {
            return Err(error(StatusCode::CONFLICT, "activity_page_expired"));
        }
        if index > matching.len() {
            return Err(invalid());
        }
        offset = index;
    }
    let following = offset + page.limit;
    let next = (following < matching.len())
        .then(|| URL_SAFE.encode(serde_json::to_vec(&(signature, following)).unwrap()));
    let end = following.min(matching.len());
    prepared.response["jobs"] =
        json!({"items":&matching[offset..end],"next_cursor":next,"total":matching.len()});
    let total = prepared.tracks.len();
    prepared.tracks.truncate(100);
    prepared.response["day_jobs"] = json!({"items":prepared.tracks,"total":total});
    Ok(Json(prepared.response))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn live_status_uses_process_start_time_and_treats_partial_observations_as_unknown() {
        let mut jobs = vec![Job {
            job_key: "fixture".into(),
            node_id: "node".into(),
            user: None,
            task_name: "fixture".into(),
            pids: [12].into(),
            sessions: vec![SessionIdentity {
                pid: 12,
                process_start_time: Some(100.0),
            }],
            started_at: 100.0,
            last_seen_at: 120.0,
            status: "running".into(),
            models: BTreeSet::new(),
            gpu_count: 1,
            segments: Vec::new(),
            intervals: Vec::new(),
        }];
        let mut nodes = BTreeMap::from([(
            "node".into(),
            ObservedNode {
                complete: true,
                processes: vec![(12, Some(100.0))],
            },
        )]);
        refresh(&mut jobs, &nodes);
        assert_eq!(jobs[0].status, "running");
        let node = nodes.get_mut("node").unwrap();
        node.complete = false;
        node.processes[0].1 = Some(150.0);
        refresh(&mut jobs, &nodes);
        assert_eq!(jobs[0].status, "unknown");
        nodes.get_mut("node").unwrap().complete = true;
        refresh(&mut jobs, &nodes);
        assert_eq!(jobs[0].status, "ended");
    }
}
