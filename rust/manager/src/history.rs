use crate::App;
use axum::Json;
use axum::extract::{Query, State};
use axum::http::{StatusCode, header};
use constella_protocol::unix_time;
use rusqlite::types::{Value as SqlValue, ValueRef};
use rusqlite::{Connection, params_from_iter};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::sync::Arc;

type Reply = Result<([(header::HeaderName, &'static str); 1], Vec<u8>), (StatusCode, Json<Value>)>;

#[derive(Serialize)]
struct History {
    enabled: bool,
    items: Vec<Value>,
}

fn encode(history: History) -> rusqlite::Result<Vec<u8>> {
    serde_json::to_vec(&history)
        .map_err(|error| rusqlite::Error::ToSqlConversionFailure(Box::new(error)))
}

#[derive(Deserialize)]
pub struct GpuQuery {
    node_id: Option<String>,
    gpu_uuid: Option<String>,
    since: Option<f64>,
    until: Option<f64>,
    limit: Option<i64>,
}

#[derive(Deserialize)]
pub struct TaskQuery {
    user: Option<String>,
    status: Option<String>,
    limit: Option<i64>,
}

pub async fn gpu(State(app): State<Arc<App>>, Query(query): Query<GpuQuery>) -> Reply {
    read(&app, move |conn| {
        let span = query
            .since
            .map_or(0.0, |since| query.until.unwrap_or_else(unix_time) - since);
        let bucket = if span <= 7.0 * 86400.0 {
            20
        } else if span <= 60.0 * 86400.0 {
            120
        } else {
            3600
        };
        let mut filters = vec![("bucket_seconds = ?", SqlValue::Integer(bucket))];
        text_filter(&mut filters, "node_id = ?", query.node_id);
        text_filter(&mut filters, "gpu_uuid = ?", query.gpu_uuid);
        if let Some(since) = query.since {
            filters.push(("bucket_start >= ?", SqlValue::Real(since)));
        }
        if let Some(until) = query.until {
            filters.push(("bucket_start <= ?", SqlValue::Real(until)));
        }
        gpu_rows(conn, filters, query.limit.unwrap_or(1000).clamp(1, 5000))
    })
    .await
}

pub fn gpu_rows(
    conn: &Connection,
    filters: Vec<(&str, SqlValue)>,
    limit: i64,
) -> rusqlite::Result<Vec<Value>> {
    fetch(conn, "
            SELECT bucket_start AS sampled_at, bucket_start, bucket_seconds, node_id, gpu_uuid,
                   avg_gpu_utilization AS utilization_gpu, avg_memory_used_mb AS memory_used_mb,
                   avg_power_watts AS power_watts, avg_temperature_c AS temperature_c,
                   avg_gpu_utilization, max_gpu_utilization, avg_memory_used_mb, max_memory_used_mb,
                   avg_power_watts, max_power_watts, avg_temperature_c, max_temperature_c, sample_count
            FROM gpu_metric_rollups", filters, "ORDER BY bucket_start ASC LIMIT ?", [SqlValue::Integer(limit)])
}

pub async fn tasks(State(app): State<Arc<App>>, Query(query): Query<TaskQuery>) -> Reply {
    read(&app, move |conn| {
        let mut filters = Vec::new();
        text_filter(&mut filters, "user = ?", query.user);
        text_filter(&mut filters, "status = ?", query.status);
        fetch(
            conn,
            "
            SELECT session_id, node_id, pid, process_start_time, user, user_uid, task_name, ppid,
                   parent_start_time, process_name, exe, cmdline_hash, first_seen_at, last_seen_at,
                   duration_seconds, status, sample_count FROM process_sessions",
            filters,
            "ORDER BY last_seen_at DESC LIMIT ?",
            [SqlValue::Integer(query.limit.unwrap_or(200).clamp(1, 1000))],
        )
    })
    .await
}

pub async fn users(State(app): State<Arc<App>>) -> Reply {
    read(&app, |conn| {
        fetch(
            conn,
            "
        SELECT user, COUNT(*) AS task_count, SUM(duration_seconds) AS total_duration_seconds,
               MAX(last_seen_at) AS last_seen_at FROM process_sessions WHERE user IS NOT NULL
        GROUP BY user ORDER BY last_seen_at DESC",
            Vec::new(),
            "",
            [],
        )
    })
    .await
}

async fn read(
    app: &App,
    query: impl FnOnce(&Connection) -> rusqlite::Result<Vec<Value>> + Send + 'static,
) -> Reply {
    let result = match &app.database {
        Some(database) => {
            database
                .read(move |conn| {
                    encode(History {
                        enabled: true,
                        items: query(conn)?,
                    })
                })
                .await
        }
        None => Ok(br#"{"enabled":false,"items":[]}"#.to_vec()),
    };
    match result {
        Ok(payload) => Ok(([(header::CONTENT_TYPE, "application/json")], payload)),
        Err(error) => {
            eprintln!("SQLite history query: {error}");
            let status = if error.is::<tokio::sync::TryAcquireError>() {
                StatusCode::SERVICE_UNAVAILABLE
            } else {
                StatusCode::INTERNAL_SERVER_ERROR
            };
            Err((status, Json(json!({"detail": "history query unavailable"}))))
        }
    }
}

fn text_filter(
    filters: &mut Vec<(&'static str, SqlValue)>,
    condition: &'static str,
    value: Option<String>,
) {
    if let Some(value) = value.filter(|value| !value.is_empty()) {
        filters.push((condition, SqlValue::Text(value)));
    }
}

pub fn fetch(
    conn: &Connection,
    select: &str,
    filters: Vec<(&str, SqlValue)>,
    suffix: &str,
    tail: impl IntoIterator<Item = SqlValue>,
) -> rusqlite::Result<Vec<Value>> {
    let (clauses, values): (Vec<_>, Vec<_>) = filters.into_iter().unzip();
    let conditions = if clauses.is_empty() {
        String::new()
    } else {
        format!("WHERE {}", clauses.join(" AND "))
    };
    let mut statement = conn.prepare(&format!("{select} {conditions} {suffix}"))?;
    let columns: Vec<_> = statement
        .column_names()
        .into_iter()
        .map(str::to_owned)
        .collect();
    statement
        .query_map(params_from_iter(values.into_iter().chain(tail)), |row| {
            let mut object = serde_json::Map::new();
            for (index, column) in columns.iter().enumerate() {
                let value = match row.get_ref(index)? {
                    ValueRef::Integer(value) => json!(value),
                    ValueRef::Real(value) => json!(value),
                    ValueRef::Text(value) => json!(String::from_utf8_lossy(value)),
                    ValueRef::Null | ValueRef::Blob(_) => Value::Null,
                };
                object.insert(column.clone(), value);
            }
            Ok(Value::Object(object))
        })?
        .collect()
}
