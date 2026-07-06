use axum::body::Body;
use axum::http::Request;
use constella::api::{app, AppState};
use constella::cluster::ClusterState;
use constella::db::{RollupRow, SQLiteStore, ROLLUP_20S, ROLLUP_2M};
use constella::schema::{node_totals_from_gpus, GpuInfo, GpuProcess, NodeSnapshot};
use constella::settings::ManagerSettings;
use http_body_util::BodyExt;
use serde_json::Value;
use tower::ServiceExt;

fn make_node_snapshot(sampled_at: f64, gpu_util: i64) -> NodeSnapshot {
    let process = GpuProcess {
        pid: 1234,
        name: "python".to_string(),
        task_name: Some("train.py".to_string()),
        user: Some("alice".to_string()),
        cmdline: Some("python train.py".to_string()),
        cmdline_hash: Some("hash".to_string()),
        gpu_memory_mb: 2048,
        ppid: Some(4321),
        process_start_time: Some(90.0),
        parent_start_time: Some(80.0),
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

#[test]
fn sqlite_store_writes_sessions_and_multi_gpu_usage() {
    let dir = tempfile::tempdir().unwrap();
    let mut store = SQLiteStore::new(dir.path().join("constella.db"));
    store.open().unwrap();

    store
        .write_node_snapshot(&make_node_snapshot(100.0, 50), false)
        .unwrap();

    assert_eq!(store.scalar_i64("SELECT COUNT(*) FROM nodes").unwrap(), 1);
    assert_eq!(store.scalar_i64("SELECT COUNT(*) FROM gpus").unwrap(), 2);
    assert_eq!(
        store
            .scalar_i64("SELECT COUNT(*) FROM gpu_metric_samples")
            .unwrap(),
        0
    );
    assert_eq!(
        store
            .scalar_i64("SELECT COUNT(*) FROM process_sessions")
            .unwrap(),
        1
    );
    assert_eq!(
        store
            .scalar_i64("SELECT COUNT(*) FROM process_gpu_usages")
            .unwrap(),
        2
    );
    let session: (String, i64, f64, i64) = store
        .connection()
        .unwrap()
        .query_row(
            "SELECT task_name, ppid, parent_start_time, sample_count FROM process_sessions",
            [],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .unwrap();
    assert_eq!(session, ("train.py".to_string(), 4321, 80.0, 1));
}

#[test]
fn sqlite_store_rollup_uses_sample_count_weighting() {
    let dir = tempfile::tempdir().unwrap();
    let mut store = SQLiteStore::new(dir.path().join("constella.db"));
    store.open().unwrap();
    store
        .upsert_gpu_metric_rollups(&[
            RollupRow {
                bucket_start: 0.0,
                bucket_seconds: ROLLUP_20S,
                node_id: "node-a".to_string(),
                gpu_uuid: "GPU-0".to_string(),
                avg_gpu_utilization: 20.0,
                max_gpu_utilization: 25.0,
                avg_memory_used_mb: 10.0,
                max_memory_used_mb: 12,
                avg_power_watts: 100.0,
                max_power_watts: 110.0,
                avg_temperature_c: 40.0,
                max_temperature_c: 42,
                sample_count: 1,
            },
            RollupRow {
                bucket_start: 20.0,
                bucket_seconds: ROLLUP_20S,
                node_id: "node-a".to_string(),
                gpu_uuid: "GPU-0".to_string(),
                avg_gpu_utilization: 80.0,
                max_gpu_utilization: 90.0,
                avg_memory_used_mb: 30.0,
                max_memory_used_mb: 40,
                avg_power_watts: 200.0,
                max_power_watts: 250.0,
                avg_temperature_c: 60.0,
                max_temperature_c: 70,
                sample_count: 3,
            },
        ])
        .unwrap();

    assert_eq!(
        store
            .rollup_gpu_metric_rollups(ROLLUP_20S, ROLLUP_2M, 400.0)
            .unwrap(),
        1
    );
    let rollup: (f64, f64, f64, i64, i64) = store
        .connection()
        .unwrap()
        .query_row(
            "SELECT avg_gpu_utilization, max_gpu_utilization, avg_memory_used_mb, max_memory_used_mb, sample_count FROM gpu_metric_rollups WHERE bucket_seconds=120 AND node_id='node-a' AND gpu_uuid='GPU-0'",
            [],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?)),
        )
        .unwrap();
    assert_eq!((rollup.0 * 10.0).round() / 10.0, 65.0);
    assert_eq!(rollup.1, 90.0);
    assert_eq!((rollup.2 * 10.0).round() / 10.0, 25.0);
    assert_eq!(rollup.3, 40);
    assert_eq!(rollup.4, 4);
}

#[tokio::test]
async fn db_history_api_reads_store() {
    let dir = tempfile::tempdir().unwrap();
    let mut store = SQLiteStore::new(dir.path().join("constella.db"));
    store.open().unwrap();
    store
        .write_node_snapshot(&make_node_snapshot(100.0, 50), false)
        .unwrap();
    store
        .upsert_gpu_metric_rollups(&[RollupRow {
            bucket_start: 100.0,
            bucket_seconds: ROLLUP_20S,
            node_id: "node-a".to_string(),
            gpu_uuid: "GPU-0".to_string(),
            avg_gpu_utilization: 42.0,
            max_gpu_utilization: 50.0,
            avg_memory_used_mb: 2048.0,
            max_memory_used_mb: 4096,
            avg_power_watts: 125.0,
            max_power_watts: 140.0,
            avg_temperature_c: 44.0,
            max_temperature_c: 46,
            sample_count: 2,
        }])
        .unwrap();
    let app = app(AppState::new(
        ClusterState::new("local".to_string()),
        ManagerSettings::new(1.0, 3.0).unwrap(),
        None,
    )
    .with_db_store(store));

    let tasks = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/history/tasks?user=alice")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let tasks = json_body(tasks).await;
    assert_eq!(tasks["enabled"], true);
    assert_eq!(tasks["items"][0]["task_name"], "train.py");

    let history = app
        .oneshot(
            Request::builder()
                .uri("/api/history/gpu?node_id=node-a&gpu_uuid=GPU-0&since=90&until=130")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let history = json_body(history).await;
    assert_eq!(history["enabled"], true);
    assert_eq!(
        history["items"][0],
        serde_json::json!({
            "sampled_at": 100.0,
            "bucket_start": 100.0,
            "bucket_seconds": 20,
            "node_id": "node-a",
            "gpu_uuid": "GPU-0",
            "utilization_gpu": 42.0,
            "memory_used_mb": 2048.0,
            "power_watts": 125.0,
            "temperature_c": 44.0,
            "avg_gpu_utilization": 42.0,
            "max_gpu_utilization": 50.0,
            "avg_memory_used_mb": 2048.0,
            "max_memory_used_mb": 4096,
            "avg_power_watts": 125.0,
            "max_power_watts": 140.0,
            "avg_temperature_c": 44.0,
            "max_temperature_c": 46,
            "sample_count": 2
        })
    );
}

async fn json_body(response: axum::response::Response) -> Value {
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}
