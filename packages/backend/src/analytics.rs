use crate::{App, history};
use axum::Json;
use axum::body::Bytes;
use axum::extract::{Path, Query, State};
use axum::http::{StatusCode, header};
use axum::response::{IntoResponse, Response};
use constella_protocol::unix_time;
use rusqlite::types::Value as SqlValue;
use rusqlite::{Connection, params};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::sync::Arc;

const DAY: i64 = 86400;

#[derive(Deserialize)]
pub struct Filters {
    range: Option<String>,
}

pub fn range_seconds(name: Option<&str>, default: i64) -> i64 {
    match name {
        Some("1h") => 3600,
        Some("24h") => DAY,
        Some("7d") => 7 * DAY,
        Some("30d") => 30 * DAY,
        _ => default,
    }
}

pub fn round_decimal(value: f64, scale: f64) -> f64 {
    let scaled = value * scale;
    if scaled.fract().abs() == 0.5 {
        let residual = value.mul_add(scale, -scaled);
        if residual > 0.0 {
            return scaled.ceil() / scale;
        }
        if residual < 0.0 {
            return scaled.floor() / scale;
        }
    }
    scaled.round_ties_even() / scale
}

pub async fn node(
    State(app): State<Arc<App>>,
    Path(node): Path<String>,
    Query(filters): Query<Filters>,
) -> Response {
    let span = range_seconds(filters.range.as_deref(), DAY);
    let key = format!("node:{span}:{node}");
    cached(app, key, move |conn| {
        node_analytics(conn, &node, span, unix_time())
    })
    .await
}

pub async fn cached(
    app: Arc<App>,
    key: String,
    load: impl FnOnce(&Connection) -> rusqlite::Result<Value> + Send + 'static,
) -> Response {
    if app.database.is_none() {
        return Json(json!({"enabled": false})).into_response();
    }
    let cache = app.analytics_cache.clone();
    match cache
        .read(key, async move {
            let db = app.database.as_ref().expect("enabled database");
            db.read(move |conn| {
                let transaction = conn.unchecked_transaction()?;
                let json = load(&transaction)?;
                transaction.commit()?;
                serde_json::to_vec(&json)
                    .map(Bytes::from)
                    .map_err(|error| rusqlite::Error::ToSqlConversionFailure(Box::new(error)))
            })
            .await
            .map_err(|error| {
                if error.is::<tokio::sync::TryAcquireError>() {
                    StatusCode::SERVICE_UNAVAILABLE
                } else {
                    eprintln!("SQLite analytics: {error}");
                    StatusCode::INTERNAL_SERVER_ERROR
                }
            })
        })
        .await
    {
        Ok(body) => ([(header::CONTENT_TYPE, "application/json")], body).into_response(),
        Err(status) => {
            let detail = if status == StatusCode::SERVICE_UNAVAILABLE {
                "analytics_busy"
            } else {
                "analytics unavailable"
            };
            (status, Json(json!({"detail": detail}))).into_response()
        }
    }
}

fn node_analytics(conn: &Connection, node: &str, span: i64, end: f64) -> rusqlite::Result<Value> {
    let start = end - span as f64;
    let source = if span <= 7 * DAY { 20 } else { 120 };
    let target = ((span + 559) / 560).max(source);
    let target = ((target + source - 1) / source) * source;
    let heatmap_width = if span <= 3600 {
        300
    } else if span <= DAY {
        3600
    } else if span <= 7 * DAY {
        21600
    } else {
        DAY
    };
    let gpus = history::fetch(
        conn,
        "SELECT uuid, gpu_index, name, memory_total_mb FROM gpus",
        vec![("node_id = ?", SqlValue::Text(node.to_owned()))],
        "ORDER BY gpu_index ASC",
        [],
    )?;
    let series = grouped(conn, node, start, end, source, target, false)?;
    let heatmap = grouped(conn, node, start, end, source, heatmap_width, true)?;
    Ok(json!({
        "enabled": true, "generated_at": end, "range_start": start, "range_end": end,
        "timezone": "Asia/Shanghai", "bucket_seconds": target, "node_id": node,
        "gpus": gpus, "series": series, "heatmap": heatmap, "heatmap_bucket_seconds": heatmap_width,
    }))
}

#[derive(Serialize)]
struct Series {
    gpu_uuid: String,
    gpu_index: Option<i64>,
    gpu_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    points: Option<Vec<Value>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    buckets: Option<Vec<Value>>,
}

fn grouped(
    conn: &Connection,
    node: &str,
    start: f64,
    end: f64,
    source: i64,
    target: i64,
    heatmap: bool,
) -> rusqlite::Result<Vec<Series>> {
    let mut query = conn.prepare_cached(include_str!("sql/node_analytics.sql"))?;
    let mut rows = query.query(params![target, source, node, start, end])?;
    let mut series = BTreeMap::new();
    let round = |value: f64| round_decimal(value, 10.0);
    while let Some(row) = rows.next()? {
        let uuid: String = row.get(1)?;
        let gpu = match series.entry(uuid.clone()) {
            std::collections::btree_map::Entry::Occupied(entry) => entry.into_mut(),
            std::collections::btree_map::Entry::Vacant(entry) => entry.insert(Series {
                gpu_uuid: uuid,
                gpu_index: row.get(2)?,
                gpu_name: row.get(3)?,
                points: (!heatmap).then(Vec::new),
                buckets: heatmap.then(Vec::new),
            }),
        };
        let mut point = json!({
            "bucket_start": row.get::<_, i64>(0)?,
            "avg_gpu_utilization": round(row.get(4)?),
            "max_gpu_utilization": round(row.get(5)?),
            "avg_memory_used_mb": round(row.get(6)?),
            "sample_count": row.get::<_, i64>(12)?,
        });
        if let Some(points) = &mut gpu.points {
            point["max_memory_used_mb"] = json!(row.get::<_, i64>(7)?);
            point["avg_power_watts"] = json!(round(row.get(8)?));
            point["max_power_watts"] = json!(round(row.get(9)?));
            point["avg_temperature_c"] = json!(round(row.get(10)?));
            point["max_temperature_c"] = json!(row.get::<_, i64>(11)?);
            points.push(point);
        } else if let Some(buckets) = &mut gpu.buckets {
            buckets.push(point);
        }
    }
    let mut series: Vec<_> = series.into_values().collect();
    series.sort_by_key(|gpu| (gpu.gpu_index.is_none(), gpu.gpu_index.unwrap_or(0)));
    Ok(series)
}
