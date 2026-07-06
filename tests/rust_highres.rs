use axum::body::Body;
use axum::http::Request;
use constella::api::{app, AppState};
use constella::cluster::ClusterState;
use constella::db::{SQLiteStore, ROLLUP_1H, ROLLUP_2M};
use constella::highres::{
    gpu_sample_message, query_jobs, GpuSampleRing, JobFilter, HIGHRES_JOB_LOOKBACK_SECONDS,
};
use constella::schema::{node_totals_from_gpus, GpuInfo, GpuProcess, NodeSnapshot};
use constella::settings::ManagerSettings;
use http_body_util::BodyExt;
use serde_json::Value;
use tower::ServiceExt;

fn make_node_snapshot(
    sampled_at: f64,
    gpu_util: i64,
    pid: i64,
    process_start_time: f64,
    ppid: i64,
    parent_start_time: f64,
) -> NodeSnapshot {
    let process = GpuProcess {
        pid,
        name: "python".to_string(),
        task_name: Some("train.py".to_string()),
        user: Some("alice".to_string()),
        cmdline: Some("python train.py".to_string()),
        cmdline_hash: Some("hash".to_string()),
        gpu_memory_mb: 2048,
        ppid: Some(ppid),
        process_start_time: Some(process_start_time),
        parent_start_time: Some(parent_start_time),
        ..Default::default()
    };
    let gpus = vec![
        GpuInfo {
            index: 0,
            node_id: Some("node-a".to_string()),
            gpu_id: Some("node-a:GPU-0".to_string()),
            uuid: "GPU-0".to_string(),
            name: "NVIDIA Test".to_string(),
            utilization_gpu: gpu_util,
            utilization_mem: 20,
            memory_total_mb: 100,
            memory_used_mb: 20,
            power_watts: 100.0,
            power_limit_watts: 200.0,
            temperature_c: 40,
            processes: vec![process.clone()],
            ..Default::default()
        },
        GpuInfo {
            index: 1,
            node_id: Some("node-a".to_string()),
            gpu_id: Some("node-a:GPU-1".to_string()),
            uuid: "GPU-1".to_string(),
            name: "NVIDIA Test".to_string(),
            utilization_gpu: gpu_util + 10,
            utilization_mem: 30,
            memory_total_mb: 100,
            memory_used_mb: 30,
            power_watts: 120.0,
            power_limit_watts: 200.0,
            temperature_c: 45,
            processes: vec![process],
            ..Default::default()
        },
    ];
    NodeSnapshot {
        node_id: "node-a".to_string(),
        hostname: "node-a-host".to_string(),
        seq: sampled_at as i64,
        sampled_at,
        received_at: Some(sampled_at + 0.1),
        refresh_interval: 1.0,
        process_interval: 3.0,
        status: "online".to_string(),
        source: "test".to_string(),
        totals: node_totals_from_gpus(&gpus),
        gpus,
        error: None,
        agent_version: Some("0.2.0".to_string()),
        driver_version: None,
        cuda_driver_version: None,
        nvml_version: None,
        elapsed_ms: 0.0,
        history: Default::default(),
        hardware: None,
    }
}

fn default_snapshot(sampled_at: f64, gpu_util: i64) -> NodeSnapshot {
    make_node_snapshot(sampled_at, gpu_util, 1234, 90.0, 4321, 80.0)
}

#[test]
fn gpu_sample_ring_wraps_and_returns_chronological_window() {
    let mut ring = GpuSampleRing::new(3);
    for sampled_at in [1.0, 2.0, 3.0, 4.0] {
        ring.append(sampled_at, &default_snapshot(sampled_at, 50).gpus[0]);
    }

    let points = ring.points(2.5, 4.0);

    assert_eq!(ring.oldest_at(), Some(2.0));
    assert_eq!(ring.newest_at(), Some(4.0));
    assert_eq!(
        points
            .iter()
            .map(|point| point["sampled_at"].as_f64().unwrap())
            .collect::<Vec<_>>(),
        vec![3.0, 4.0]
    );
}

#[test]
fn query_jobs_groups_sessions_by_existing_job_key() {
    let dir = tempfile::tempdir().unwrap();
    let mut store = SQLiteStore::new(dir.path().join("constella.db"));
    store.open().unwrap();
    store
        .write_node_snapshot(&default_snapshot(100.0, 50), false)
        .unwrap();
    store
        .write_node_snapshot(&default_snapshot(110.0, 60), false)
        .unwrap();

    let jobs = query_jobs(&store, JobFilter::default(), Some(120.0)).unwrap();

    assert_eq!(jobs.len(), 1);
    assert_eq!(jobs[0]["job_key"], "node-a:alice:80.0:4321");
    assert_eq!(jobs[0]["task_name"], "train.py");
    assert_eq!(jobs[0]["pids"], serde_json::json!([1234]));
    assert_eq!(jobs[0]["gpu_count"], 2);
    assert_eq!(jobs[0]["duration_seconds"], 10.0);
}

#[test]
fn gpu_sample_message_matches_manager_stream_contract() {
    let snapshot = default_snapshot(10.0, 42);

    let message = gpu_sample_message(&snapshot);

    assert_eq!(message["type"], "gpu_sample");
    assert_eq!(message["node_id"], "node-a");
    assert_eq!(message["refresh_interval"], 1.0);
    assert_eq!(
        message["gpus"][0],
        serde_json::json!({
            "uuid": "GPU-0",
            "gpu_index": 0,
            "name": "NVIDIA Test",
            "utilization_gpu": 42,
            "utilization_mem": 20,
            "memory_used_mb": 20,
            "memory_total_mb": 100,
            "power_watts": 100.0,
            "temperature_c": 40,
        })
    );
}

#[test]
fn query_jobs_does_not_merge_short_tasks_from_long_lived_parent() {
    let dir = tempfile::tempdir().unwrap();
    let mut store = SQLiteStore::new(dir.path().join("constella.db"));
    store.open().unwrap();
    store
        .write_node_snapshot(
            &make_node_snapshot(100.0, 50, 1234, 95.0, 4321, -1000.0),
            false,
        )
        .unwrap();
    store
        .write_node_snapshot(
            &make_node_snapshot(140.0, 60, 5678, 135.0, 4321, -1000.0),
            false,
        )
        .unwrap();

    let jobs = query_jobs(&store, JobFilter::default(), Some(160.0)).unwrap();

    assert_eq!(jobs.len(), 2);
    assert_eq!(jobs[0]["pids"], serde_json::json!([5678]));
    assert_eq!(jobs[1]["pids"], serde_json::json!([1234]));
}

#[test]
fn query_jobs_defaults_to_seven_days_and_includes_long_jobs() {
    let dir = tempfile::tempdir().unwrap();
    let mut store = SQLiteStore::new(dir.path().join("constella.db"));
    store.open().unwrap();
    store
        .write_node_snapshot(&default_snapshot(100.0, 50), false)
        .unwrap();
    store
        .write_node_snapshot(&default_snapshot(4100.0, 60), false)
        .unwrap();

    let recent_jobs = query_jobs(&store, JobFilter::default(), Some(4200.0)).unwrap();
    let expired_jobs = query_jobs(
        &store,
        JobFilter::default(),
        Some(4100.0 + HIGHRES_JOB_LOOKBACK_SECONDS + 1.0),
    )
    .unwrap();

    assert_eq!(recent_jobs.len(), 1);
    assert_eq!(recent_jobs[0]["duration_seconds"], 4000.0);
    assert!(expired_jobs.is_empty());
}

#[tokio::test]
async fn highres_job_curve_api_returns_memory_series() {
    let dir = tempfile::tempdir().unwrap();
    let base = unix_now();
    let mut store = SQLiteStore::new(dir.path().join("constella.db"));
    store.open().unwrap();
    store
        .write_node_snapshot(&default_snapshot(base, 50), false)
        .unwrap();
    store
        .write_node_snapshot(&default_snapshot(base + 10.0, 60), false)
        .unwrap();
    let state = AppState::new(
        ClusterState::new("local".to_string()),
        ManagerSettings::new(1.0, 3.0).unwrap(),
        None,
    )
    .with_db_store(store);
    for offset in -30..=30 {
        state
            .highres_cache
            .write()
            .add_snapshot(&default_snapshot(base + offset as f64, 50));
    }
    let app = app(state);

    let jobs = request_json(&app, "/api/highres/jobs?q=alice").await;
    let job_key = jobs["items"][0]["job_key"].as_str().unwrap();
    let curve = request_json(
        &app,
        &format!("/api/highres/jobs/{job_key}/gpu?padding_seconds=20"),
    )
    .await;

    assert_eq!(curve["enabled"], true);
    assert_eq!(curve["source"], "high_res_memory");
    assert_eq!(curve["expired"], false);
    assert_eq!(curve["series"].as_array().unwrap().len(), 2);
    assert_eq!(curve["series"][0]["points"][0]["sampled_at"], base - 20.0);
}

#[tokio::test]
async fn highres_job_curve_uses_rollup_for_multiday_jobs() {
    let dir = tempfile::tempdir().unwrap();
    let base = unix_now() - 4.0 * 24.0 * 60.0 * 60.0;
    let mut store = SQLiteStore::new(dir.path().join("constella.db"));
    store.open().unwrap();
    store
        .write_node_snapshot(&default_snapshot(base, 50), false)
        .unwrap();
    store
        .write_node_snapshot(
            &default_snapshot(base + 3.0 * 24.0 * 60.0 * 60.0, 60),
            false,
        )
        .unwrap();
    let app = app(AppState::new(
        ClusterState::new("local".to_string()),
        ManagerSettings::new(1.0, 3.0).unwrap(),
        None,
    )
    .with_db_store(store));

    let jobs = request_json(&app, "/api/highres/jobs?q=alice").await;
    let job_key = jobs["items"][0]["job_key"].as_str().unwrap();
    let curve = request_json(&app, &format!("/api/highres/jobs/{job_key}/gpu")).await;
    let manual = request_json(
        &app,
        &format!("/api/highres/jobs/{job_key}/gpu?resolution=1h"),
    )
    .await;

    assert_eq!(curve["source"], "rollup");
    assert_eq!(curve["resolution_seconds"], ROLLUP_2M);
    assert_eq!(manual["source"], "rollup");
    assert_eq!(manual["resolution_seconds"], ROLLUP_1H);
}

async fn request_json(app: &axum::Router, uri: &str) -> Value {
    let response = app
        .clone()
        .oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
        .await
        .unwrap();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

fn unix_now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs_f64()
}
