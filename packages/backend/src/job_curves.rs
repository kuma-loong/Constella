use crate::{App, highres, history, jobs};
use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use constella_protocol::{NVIDIA_GPM_PROFILE, unix_time};
use rusqlite::types::Value as SqlValue;
use rusqlite::{Connection, params};
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;

#[derive(Deserialize)]
pub struct Options {
    metrics: Option<String>,
    padding_seconds: Option<f64>,
    resolution: Option<String>,
}

struct Window {
    since: f64,
    until: f64,
    mode: &'static str,
    bucket: Option<i64>,
    warnings: Vec<&'static str>,
}

impl Window {
    fn new(job: &jobs::Job, options: &Options) -> Self {
        let padding = options.padding_seconds.unwrap_or(20.0).clamp(0.0, 300.0);
        let (mut mode, mut bucket) = match options
            .resolution
            .as_deref()
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase()
            .as_str()
        {
            "20s" | "20" => ("20s", Some(20)),
            "2m" | "120" => ("2m", Some(120)),
            "1h" | "3600" => ("1h", Some(3600)),
            _ => ("auto", None),
        };
        let mut warnings = Vec::new();
        if job.started_at < unix_time() - 7.0 * 86400.0 {
            if mode != "auto" {
                mode = "1h";
            }
            bucket = Some(3600);
            warnings.push("Jobs with activity older than 7 days use hourly history.");
        }
        if bucket == Some(3600) {
            warnings.push(
                "Hourly readings cover the whole GPU and may include activity outside the job.",
            );
        }
        Self {
            since: (job.started_at - padding).max(0.0),
            until: job.last_seen_at + padding,
            mode,
            bucket,
            warnings,
        }
    }

    fn bucket(&self) -> i64 {
        self.bucket
            .unwrap_or(if self.until - self.since <= 86400.0 {
                20
            } else {
                120
            })
    }

    fn lower_bound(&self) -> f64 {
        if self.bucket() == 3600 {
            (self.since / 3600.0).floor() * 3600.0
        } else {
            self.since
        }
    }

    fn limit(&self) -> i64 {
        (((self.until - self.since).max(0.0) / self.bucket() as f64).ceil() as i64 + 4)
            .clamp(1000, 20000)
    }
}

pub async fn gpu(
    State(app): State<Arc<App>>,
    Path(key): Path<String>,
    Query(options): Query<Options>,
) -> jobs::Reply {
    serve(app, key, options, false).await
}

pub async fn performance(
    State(app): State<Arc<App>>,
    Path(key): Path<String>,
    Query(options): Query<Options>,
) -> jobs::Reply {
    serve(app, key, options, true).await
}

async fn serve(app: Arc<App>, key: String, options: Options, performance: bool) -> jobs::Reply {
    let Some(database) = &app.database else {
        return Ok(Json(json!({"enabled": false, "series": []})));
    };
    if options
        .padding_seconds
        .is_some_and(|value| !value.is_finite())
    {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"detail":"invalid padding"})),
        ));
    }
    let application = app.clone();
    let payload = database
        .read(move |conn| {
            let Some(job) = jobs::lookup(conn, &key)? else {
                return Ok(None);
            };
            let window = Window::new(&job, &options);
            let cache = &application.highres;
            let mut curve = if performance {
                performance_curve(conn, cache, &job, &window, options.metrics)?
            } else {
                gpu_curve(conn, cache, &job, &window)?
            };
            curve["enabled"] = json!(true);
            curve["job_key"] = json!(job.job_key);
            curve["job"] = json!(job);
            curve["range_start"] = json!(window.since);
            curve["range_end"] = json!(window.until);
            curve["resolution_mode"] = json!(window.mode);
            Ok(Some(curve))
        })
        .await
        .map_err(jobs::read_error)?;
    payload.map(Json).ok_or_else(|| {
        (
            StatusCode::NOT_FOUND,
            Json(json!({"detail":"job not found"})),
        )
    })
}

fn label(gpu: &jobs::Device) -> String {
    let suffix = gpu
        .gpu_index
        .map_or_else(|| gpu.gpu_uuid.clone(), |index| format!("GPU{index}"));
    format!("{} {suffix}", gpu.node_id)
}

fn gpu_curve(
    conn: &Connection,
    cache: &highres::Highres,
    job: &jobs::Job,
    window: &Window,
) -> rusqlite::Result<Value> {
    let mut warnings = window.warnings.clone();
    if window.bucket.is_none() {
        if job.duration_seconds < 3600.0 {
            if let Some(curve) = cached_gpu_curve(cache, job, window, &warnings) {
                return Ok(curve);
            }
            warnings.push("high-resolution cache does not cover the full job window");
        } else {
            warnings.push("job duration is 1 hour or longer, using rollup history");
        }
    }
    let mut series = Vec::with_capacity(job.gpus.len());
    let mut coverage = (None::<f64>, None::<f64>);
    for gpu in &job.gpus {
        let points = history::gpu_rows(
            conn,
            vec![
                ("bucket_seconds = ?", SqlValue::Integer(window.bucket())),
                ("node_id = ?", SqlValue::Text(gpu.node_id.clone())),
                ("gpu_uuid = ?", SqlValue::Text(gpu.gpu_uuid.clone())),
                ("bucket_start >= ?", SqlValue::Real(window.lower_bound())),
                ("bucket_start <= ?", SqlValue::Real(window.until)),
            ],
            window.limit(),
        )?;
        if let Some((first, last)) = points
            .first()
            .and_then(|point| point["sampled_at"].as_f64())
            .zip(points.last().and_then(|point| point["sampled_at"].as_f64()))
        {
            extend_coverage(&mut coverage, first, last);
        }
        series.push(device_series(gpu, "points", json!(points)));
    }
    Ok(json!({
        "source": "rollup", "coverage_start": coverage.0, "coverage_end": coverage.1,
        "cache_retention_seconds": highres::RETENTION, "resolution_seconds": window.bucket(),
        "expired": true, "warnings": warnings, "series": series,
    }))
}

fn device_series(gpu: &jobs::Device, field: &str, value: Value) -> Value {
    let mut series = json!(gpu);
    series["label"] = json!(label(gpu));
    series[field] = value;
    series
}

fn extend_coverage(coverage: &mut (Option<f64>, Option<f64>), first: f64, last: f64) {
    coverage.0 = Some(coverage.0.map_or(first, |current| current.min(first)));
    coverage.1 = Some(coverage.1.map_or(last, |current| current.max(last)));
}

fn cached_gpu_curve(
    cache: &highres::Highres,
    job: &jobs::Job,
    window: &Window,
    warnings: &[&str],
) -> Option<Value> {
    let mut series = Vec::with_capacity(job.gpus.len());
    let mut coverage = (None, None);
    let mut interval: Option<f64> = None;
    for gpu in &job.gpus {
        let data = cache.basic_window(&gpu.node_id, &gpu.gpu_uuid, window.since, window.until)?;
        if data.oldest > job.started_at || data.newest < job.last_seen_at || data.points.is_empty()
        {
            return None;
        }
        if let Some(next) = data.interval {
            interval = Some(interval.map_or(next, |previous| previous.min(next)));
        }
        extend_coverage(&mut coverage, data.oldest, data.newest);
        let points: Vec<_> = data
            .points
            .into_iter()
            .map(highres::BasicFrame::point)
            .collect();
        series.push(device_series(gpu, "points", json!(points)));
    }
    Some(json!({
        "source": "high_res_memory", "coverage_start": coverage.0, "coverage_end": coverage.1,
        "cache_retention_seconds": highres::RETENTION, "resolution_seconds": interval, "expired": false,
        "warnings": warnings, "series": series,
    }))
}

fn performance_curve(
    conn: &Connection,
    cache: &highres::Highres,
    job: &jobs::Job,
    window: &Window,
    requested: Option<String>,
) -> rusqlite::Result<Value> {
    let concurrent = concurrent_sessions(conn, job)?;
    let mut warnings =
        vec!["performance metrics are device-wide and are not attributed to an individual process"];
    warnings.extend(&window.warnings);
    if concurrent > 0 {
        warnings.push("other process sessions overlap this GPU time window");
    }
    let automatic = window.bucket.is_none() && job.duration_seconds < 3600.0;
    let mut curve = if automatic
        && job.gpus.iter().all(|gpu| {
            cache.covers_performance(
                &gpu.node_id,
                &gpu.gpu_uuid,
                job.started_at,
                job.last_seen_at,
            )
        }) {
        let interval = job
            .gpus
            .iter()
            .filter_map(|gpu| {
                cache
                    .basic_window(&gpu.node_id, &gpu.gpu_uuid, job.started_at, job.started_at)?
                    .interval
            })
            .min_by(f64::total_cmp);
        let mut curve = cache.query(highres::PerformanceQuery {
            node_id: job.node_id.clone(),
            gpu_uuid: Some(
                job.gpus
                    .iter()
                    .map(|gpu| gpu.gpu_uuid.as_str())
                    .collect::<Vec<_>>()
                    .join(","),
            ),
            metrics: requested,
            since: Some(window.since),
            until: Some(window.until),
            max_points: Some(5000),
            summary_only: false,
        });
        curve["source"] = json!("high_res_memory");
        curve["resolution_seconds"] = json!(interval);
        curve
    } else {
        if automatic {
            warnings.push("high-resolution performance cache does not cover the full job window");
        }
        let metrics = highres::select_metrics(requested.as_deref());
        let mut series = Vec::with_capacity(job.gpus.len());
        for gpu in &job.gpus {
            series.push(device_series(
                gpu,
                "metrics",
                json!(rollup_metrics(conn, gpu, window, &metrics)?),
            ));
        }
        json!({
            "profile": NVIDIA_GPM_PROFILE, "source": "rollup",
            "resolution_seconds": window.bucket(), "series": series,
        })
    };
    curve["device_scope"] = json!(true);
    curve["concurrent_session_count"] = json!(concurrent);
    curve["warnings"] = json!(warnings);
    Ok(curve)
}

fn concurrent_sessions(conn: &Connection, job: &jobs::Job) -> rusqlite::Result<usize> {
    let own: HashSet<_> = job
        .sessions
        .iter()
        .map(|session| session.session_id.as_str())
        .collect();
    let mut stmt = conn.prepare(
        "SELECT session_id FROM process_gpu_usages WHERE node_id = ? AND gpu_uuid = ?
         AND first_seen_at <= ? AND last_seen_at >= ?",
    )?;
    let mut count = 0;
    for gpu in &job.gpus {
        let ids = stmt.query_map(
            params![gpu.node_id, gpu.gpu_uuid, job.last_seen_at, job.started_at],
            |row| row.get::<_, String>(0),
        )?;
        for id in ids {
            count += usize::from(!own.contains(id?.as_str()));
        }
    }
    Ok(count)
}

#[derive(Default)]
struct Metric {
    count: u64,
    weighted: f64,
    max: Option<f64>,
    points: Vec<(f64, Option<f64>)>,
}

fn rollup_metrics(
    conn: &Connection,
    gpu: &jobs::Device,
    window: &Window,
    metrics: &[(usize, &'static str)],
) -> rusqlite::Result<BTreeMap<&'static str, Value>> {
    if metrics.is_empty() {
        return Ok(BTreeMap::new());
    }
    let columns = metrics
        .iter()
        .map(|(_, name)| {
            let stem = name
                .strip_prefix("nvidia.gpm.")
                .expect("validated GPM metric");
            format!("avg_{stem}, max_{stem}, {stem}_count")
        })
        .collect::<Vec<_>>()
        .join(", ");
    let mut stmt = conn.prepare_cached(&format!(
        "SELECT bucket_start, expected_count, {columns} FROM nvidia_gpm_rollups
         WHERE node_id = ? AND gpu_uuid = ? AND bucket_seconds = ? AND bucket_start >= ?
         AND bucket_start <= ? ORDER BY bucket_start ASC LIMIT ?",
    ))?;
    let mut rows = stmt.query(params![
        gpu.node_id,
        gpu.gpu_uuid,
        window.bucket(),
        window.lower_bound(),
        window.until,
        window.limit()
    ])?;
    let mut aggregates: Vec<_> = metrics.iter().map(|_| Metric::default()).collect();
    let mut expected = 0_u64;
    while let Some(row) = rows.next()? {
        let at = row.get::<_, f64>(0)?;
        expected += row.get::<_, u64>(1)?;
        for (index, metric) in aggregates.iter_mut().enumerate() {
            let column = 2 + index * 3;
            let avg = row.get::<_, Option<f64>>(column)?;
            if let Some(max) = row.get::<_, Option<f64>>(column + 1)? {
                metric.max = Some(metric.max.map_or(max, |previous| previous.max(max)));
            }
            let count = row.get::<_, u64>(column + 2)?;
            metric.count += count;
            metric.weighted += avg.unwrap_or_default() * count as f64;
            metric.points.push((at, avg));
        }
    }
    Ok(metrics
        .iter()
        .zip(aggregates)
        .map(|((_, name), metric)| {
            let avg = (metric.count > 0)
                .then(|| (metric.weighted * 1000.0 / metric.count as f64).round() / 1000.0);
            let coverage = if expected == 0 {
                0.0
            } else {
                (metric.count as f64 * 10000.0 / expected as f64).round() / 100.0
            };
            (
                *name,
                json!({"points": metric.points, "summary": {
                    "avg": avg, "min": null, "max": metric.max, "p95": null,
                    "sample_count": metric.count, "expected_count": expected, "coverage": coverage,
                }}),
            )
        })
        .collect())
}
