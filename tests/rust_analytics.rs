use axum::body::Body;
use axum::http::Request;
use constella::analytics::{gpu_weight, node_analytics, overlap_seconds, overview_analytics};
use constella::api::{app, AppState};
use constella::cluster::ClusterState;
use constella::db::{RollupRow, SQLiteStore, ROLLUP_20S};
use constella::schema::{node_totals_from_gpus, GpuInfo, GpuProcess, NodeSnapshot};
use constella::settings::ManagerSettings;
use http_body_util::BodyExt;
use serde_json::Value;
use tower::ServiceExt;

fn make_snapshot(
    sampled_at: f64,
    user: &str,
    pid: i64,
    gpu_memory_mb: i64,
    gpu_util: i64,
) -> NodeSnapshot {
    let process = GpuProcess {
        pid,
        name: "python".to_string(),
        task_name: Some("train.py".to_string()),
        user: Some(user.to_string()),
        cmdline_hash: Some("cmdhash".to_string()),
        gpu_memory_mb,
        ppid: Some(10),
        process_start_time: Some(100.0),
        parent_start_time: Some(80.0),
        ..Default::default()
    };
    let gpus = vec![
        GpuInfo {
            index: 0,
            node_id: Some("node-a".to_string()),
            gpu_id: Some("node-a:GPU-0".to_string()),
            uuid: "GPU-0".to_string(),
            name: "NVIDIA H100 80GB HBM3".to_string(),
            utilization_gpu: gpu_util,
            memory_total_mb: 80 * 1024,
            memory_used_mb: gpu_memory_mb,
            power_watts: 120.0,
            power_limit_watts: 700.0,
            temperature_c: 43,
            processes: vec![process.clone()],
            ..Default::default()
        },
        GpuInfo {
            index: 1,
            node_id: Some("node-a".to_string()),
            gpu_id: Some("node-a:GPU-1".to_string()),
            uuid: "GPU-1".to_string(),
            name: "NVIDIA RTX PRO 6000".to_string(),
            utilization_gpu: gpu_util + 1,
            memory_total_mb: 96 * 1024,
            memory_used_mb: gpu_memory_mb,
            power_watts: 130.0,
            power_limit_watts: 600.0,
            temperature_c: 44,
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
        gpus: gpus.clone(),
        totals: node_totals_from_gpus(&gpus),
        error: None,
        agent_version: None,
        driver_version: None,
        cuda_driver_version: None,
        nvml_version: None,
        elapsed_ms: 0.0,
        history: Default::default(),
        hardware: None,
    }
}

fn seed_rollups(store: &SQLiteStore, base: f64) {
    let mut rows = Vec::new();
    for bucket_start in [base + 2000.0, base + 4000.0, base + 6000.0, base + 8000.0] {
        for (gpu_uuid, util) in [("GPU-0", 3.0), ("GPU-1", 4.0)] {
            rows.push(RollupRow {
                bucket_start,
                bucket_seconds: ROLLUP_20S,
                node_id: "node-a".to_string(),
                gpu_uuid: gpu_uuid.to_string(),
                avg_gpu_utilization: util,
                max_gpu_utilization: util + 2.0,
                avg_memory_used_mb: 24.0 * 1024.0,
                max_memory_used_mb: 24 * 1024,
                avg_power_watts: 120.0,
                max_power_watts: 140.0,
                avg_temperature_c: 43.0,
                max_temperature_c: 45,
                sample_count: 3,
            });
        }
    }
    store.upsert_gpu_metric_rollups(&rows).unwrap();
}

#[test]
fn overlap_and_gpu_weight_helpers_match_python_contract() {
    assert_eq!(overlap_seconds(10.0, 30.0, 20.0, 40.0), 10.0);
    assert_eq!(overlap_seconds(10.0, 30.0, 40.0, 50.0), 0.0);
    assert_eq!(gpu_weight(Some("NVIDIA RTX PRO 6000 Ada")), 0.9);
    assert_eq!(gpu_weight(Some("NVIDIA H100")), 1.0);
}

#[test]
fn overview_analytics_aggregates_users_jobs_and_anomalies() {
    let dir = tempfile::tempdir().unwrap();
    let mut store = SQLiteStore::new(dir.path().join("constella.db"));
    store.open().unwrap();
    store
        .write_node_snapshot(&make_snapshot(1000.0, "alice", 111, 24 * 1024, 4), false)
        .unwrap();
    store
        .write_node_snapshot(&make_snapshot(9000.0, "alice", 111, 24 * 1024, 4), false)
        .unwrap();
    seed_rollups(&store, 0.0);

    let payload = overview_analytics(&store, "7d", Some(10_000.0)).unwrap();

    assert_eq!(payload["enabled"], true);
    assert_eq!(payload["timezone"], "Asia/Shanghai");
    let user = &payload["user_gpu_hours"][0];
    assert_eq!(user["user"], "alice");
    assert_eq!(user["task_count"], 1);
    assert_eq!(user["job_count"], 1);
    assert_eq!(user["gpu_hours"], 4.44);
    assert_eq!(user["weighted_gpu_hours"], 4.22);
    assert_eq!(payload["job_rankings"][0]["gpu_count"], 2);
    assert_eq!(payload["anomalies"][0]["user"], "alice");
    assert_eq!(
        payload["anomalies"][0]["gpu_indices"],
        serde_json::json!([0, 1])
    );
    assert_eq!(payload["anomalies"][0]["pids"], serde_json::json!([111]));
    assert!(
        payload["anomalies"][0]["recent_avg_gpu_utilization"]
            .as_f64()
            .unwrap()
            < 5.0
    );
}

#[test]
fn node_analytics_returns_series_and_heatmap() {
    let dir = tempfile::tempdir().unwrap();
    let mut store = SQLiteStore::new(dir.path().join("constella.db"));
    store.open().unwrap();
    store
        .write_node_snapshot(&make_snapshot(1000.0, "alice", 111, 24 * 1024, 4), false)
        .unwrap();
    seed_rollups(&store, 0.0);

    let payload = node_analytics(&store, "node-a", "24h", Some(10_000.0)).unwrap();

    assert_eq!(payload["enabled"], true);
    assert!(payload["bucket_seconds"].as_i64().unwrap() >= 20);
    assert_eq!(payload["gpus"][0]["uuid"], "GPU-0");
    assert_eq!(payload["series"][0]["gpu_uuid"], "GPU-0");
    assert_eq!(
        payload["series"][0]["points"][0]["avg_gpu_utilization"],
        3.0
    );
    assert_eq!(payload["heatmap_bucket_seconds"], 3600);
    assert!(!payload["heatmap"][0]["buckets"]
        .as_array()
        .unwrap()
        .is_empty());
}

#[tokio::test]
async fn analytics_api_reads_optional_db_store() {
    let dir = tempfile::tempdir().unwrap();
    let now = unix_now();
    let mut store = SQLiteStore::new(dir.path().join("constella.db"));
    store.open().unwrap();
    store
        .write_node_snapshot(
            &make_snapshot(now - 2000.0, "alice", 111, 24 * 1024, 4),
            false,
        )
        .unwrap();
    store
        .write_node_snapshot(
            &make_snapshot(now - 1000.0, "alice", 111, 24 * 1024, 4),
            false,
        )
        .unwrap();
    seed_rollups(&store, now - 9000.0);
    let app = app(AppState::new(
        ClusterState::new("local".to_string()),
        ManagerSettings::new(1.0, 3.0).unwrap(),
        None,
    )
    .with_db_store(store));

    let overview = request_json(&app, "/api/analytics/overview?range=7d").await;
    let node = request_json(&app, "/api/analytics/node/node-a?range=24h").await;

    assert_eq!(overview["enabled"], true);
    assert_eq!(overview["user_gpu_hours"][0]["user"], "alice");
    assert_eq!(node["enabled"], true);
    assert_eq!(node["node_id"], "node-a");
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
