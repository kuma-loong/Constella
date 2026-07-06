use constella::cluster::{parse_agent_hello, AgentHello, ClusterState};
use constella::schema::{
    cluster_snapshot_from_nodes, process_session_id, snapshot_to_node_snapshot, GpuInfo,
    GpuProcess, Snapshot,
};
use serde_json::json;

fn sample_message(node_id: &str, seq: i64, util: i64) -> serde_json::Value {
    json!({
        "type": "sample",
        "schema_version": 1,
        "node_id": node_id,
        "seq": seq,
        "sampled_at": 100.0 + seq as f64,
        "refresh_interval": 1.0,
        "process_interval": 3.0,
        "snapshot": {
            "ok": true,
            "source": "test",
            "hostname": format!("{node_id}-host"),
            "timestamp": 100.0 + seq as f64,
            "elapsed_ms": 2.0,
            "gpus": [{
                "index": 0,
                "uuid": "GPU-abc",
                "name": "NVIDIA Test",
                "utilization_gpu": util,
                "memory_total_mb": 100,
                "memory_used_mb": 25,
                "processes": [{
                    "pid": 123,
                    "name": "python",
                    "task_name": "train.py",
                    "gpu_memory_mb": 25,
                    "ppid": 42,
                    "kind": "compute",
                    "process_start_time": 90.0,
                    "parent_start_time": 80.0
                }]
            }]
        }
    })
}

#[test]
fn snapshot_totals_match_python_contract() {
    let snapshot = Snapshot {
        ok: true,
        source: "test".to_string(),
        hostname: "node".to_string(),
        timestamp: 1.0,
        elapsed_ms: 2.0,
        gpus: vec![
            GpuInfo {
                index: 0,
                utilization_gpu: 50,
                memory_total_mb: 100,
                memory_used_mb: 25,
                power_watts: 100.0,
                power_limit_watts: 200.0,
                temperature_c: 40,
                ..Default::default()
            },
            GpuInfo {
                index: 1,
                utilization_gpu: 100,
                memory_total_mb: 100,
                memory_used_mb: 50,
                power_watts: 150.0,
                power_limit_watts: 200.0,
                temperature_c: 60,
                ..Default::default()
            },
        ],
        driver_version: None,
        cuda_driver_version: None,
        nvml_version: None,
        error: None,
        seq: 0,
        refresh_interval: 1.0,
        history: Default::default(),
    };

    let totals = snapshot.totals();

    assert_eq!(totals.gpu_count, 2);
    assert_eq!(totals.avg_gpu_utilization, 75.0);
    assert_eq!(totals.avg_memory_utilization, 37.5);
    assert_eq!(totals.power_watts, 250.0);
    assert_eq!(totals.max_temperature_c, 60);
}

#[test]
fn snapshot_wraps_to_node_snapshot_with_stable_gpu_ids() {
    let mut history = std::collections::BTreeMap::new();
    history.insert(
        "0".to_string(),
        [
            ("gpu".to_string(), vec![10.0]),
            ("memory".to_string(), vec![20.0]),
        ]
        .into_iter()
        .collect(),
    );
    let snapshot = Snapshot {
        ok: true,
        source: "test".to_string(),
        hostname: "host-a".to_string(),
        timestamp: 10.0,
        elapsed_ms: 1.0,
        seq: 7,
        refresh_interval: 1.0,
        history,
        gpus: vec![GpuInfo {
            index: 0,
            uuid: "GPU-shared".to_string(),
            memory_total_mb: 100,
            memory_used_mb: 10,
            ..Default::default()
        }],
        driver_version: None,
        cuda_driver_version: None,
        nvml_version: None,
        error: None,
    };

    let node =
        snapshot_to_node_snapshot(&snapshot, Some("node-a"), None, Some(11.0), 3.0, None, None);

    assert_eq!(node.node_id, "node-a");
    assert_eq!(node.seq, 7);
    assert_eq!(node.gpus[0].gpu_id.as_deref(), Some("node-a:GPU-shared"));
    assert!(node.history.contains_key("node-a:GPU-shared"));
    assert_eq!(node.totals.memory_used_mb, 10);
}

#[test]
fn cluster_aggregation_keeps_same_index_gpus_distinct() {
    let left = snapshot_to_node_snapshot(
        &Snapshot {
            ok: true,
            source: "test".to_string(),
            hostname: "left".to_string(),
            timestamp: 1.0,
            elapsed_ms: 1.0,
            seq: 1,
            gpus: vec![GpuInfo {
                index: 0,
                uuid: "GPU-0".to_string(),
                utilization_gpu: 50,
                memory_total_mb: 100,
                ..Default::default()
            }],
            driver_version: None,
            cuda_driver_version: None,
            nvml_version: None,
            error: None,
            refresh_interval: 1.0,
            history: Default::default(),
        },
        Some("node-left"),
        None,
        None,
        3.0,
        None,
        None,
    );
    let right = snapshot_to_node_snapshot(
        &Snapshot {
            hostname: "right".to_string(),
            gpus: vec![GpuInfo {
                index: 0,
                uuid: "GPU-0".to_string(),
                utilization_gpu: 100,
                memory_total_mb: 100,
                ..Default::default()
            }],
            ..Snapshot {
                ok: true,
                source: "test".to_string(),
                hostname: String::new(),
                timestamp: 1.0,
                elapsed_ms: 1.0,
                seq: 1,
                gpus: vec![],
                driver_version: None,
                cuda_driver_version: None,
                nvml_version: None,
                error: None,
                refresh_interval: 1.0,
                history: Default::default(),
            }
        },
        Some("node-right"),
        None,
        None,
        3.0,
        None,
        None,
    );

    let cluster = cluster_snapshot_from_nodes(vec![right, left], 3, 2.0);

    assert_eq!(cluster.totals.node_count, 2);
    assert_eq!(cluster.totals.node.gpu_count, 2);
    assert_eq!(cluster.totals.node.avg_gpu_utilization, 75.0);
    let ids: Vec<_> = cluster
        .nodes
        .iter()
        .flat_map(|node| node.gpus.iter())
        .map(|gpu| gpu.gpu_id.as_deref().unwrap())
        .collect();
    assert_eq!(ids, vec!["node-left:GPU-0", "node-right:GPU-0"]);
}

#[test]
fn process_session_id_uses_node_pid_and_start_time() {
    let first = GpuProcess {
        pid: 123,
        name: "python".to_string(),
        gpu_memory_mb: 1024,
        process_start_time: Some(100.25),
        ..Default::default()
    };
    let second = GpuProcess {
        process_start_time: Some(200.25),
        ..first.clone()
    };

    assert_eq!(
        process_session_id("node-a", &first),
        "node-a:123:100.250000"
    );
    assert_ne!(
        process_session_id("node-a", &first),
        process_session_id("node-a", &second)
    );
}

#[test]
fn cluster_state_registers_sample_and_drops_old_seq() {
    let state = ClusterState::new("manager".to_string());
    state.register_hello(
        AgentHello {
            node_id: "node-a".to_string(),
            hostname: "host-a".to_string(),
            agent_version: None,
            capabilities: None,
            hardware: None,
        },
        Some(10.0),
        None,
    );

    assert!(state
        .ingest_sample(&sample_message("node-a", 2, 40), Some(12.0), None)
        .unwrap());
    assert!(!state
        .ingest_sample(&sample_message("node-a", 1, 99), Some(13.0), None)
        .unwrap());

    let cluster = state.snapshot(Some(13.0));
    let node = &cluster.nodes[0];
    assert_eq!(node.node_id, "node-a");
    assert_eq!(node.seq, 2);
    assert_eq!(node.status, "online");
    assert_eq!(node.gpus[0].gpu_id.as_deref(), Some("node-a:GPU-abc"));
    assert_eq!(node.gpus[0].utilization_gpu, 40);
    assert_eq!(node.gpus[0].processes[0].ppid, Some(42));
    assert_eq!(node.gpus[0].processes[0].parent_start_time, Some(80.0));
    assert_eq!(node.history["node-a:GPU-abc"]["gpu"], vec![40.0]);
    assert_eq!(node.history["node-a:GPU-abc"]["memory"], vec![25.0]);
}

#[test]
fn cluster_state_builds_short_history_from_samples_without_agent_history() {
    let state = ClusterState::with_options("manager".to_string(), None, None, 2);
    state.register_hello(
        AgentHello {
            node_id: "node-a".to_string(),
            hostname: "host-a".to_string(),
            agent_version: None,
            capabilities: None,
            hardware: None,
        },
        Some(10.0),
        None,
    );

    for (seq, util) in [(1, 40), (2, 55), (3, 70)] {
        state
            .ingest_sample(
                &sample_message("node-a", seq, util),
                Some(10.0 + seq as f64),
                None,
            )
            .unwrap();
    }

    let node = state.snapshot(Some(14.0)).nodes.remove(0);
    let history = &node.history["node-a:GPU-abc"];
    assert_eq!(history["gpu"], vec![55.0, 70.0]);
    assert_eq!(history["memory"], vec![25.0, 25.0]);
    assert_eq!(history["power"], vec![0.0, 0.0]);
    assert_eq!(history["temperature"], vec![0.0, 0.0]);
}

#[test]
fn cluster_state_marks_stale_offline_and_disconnect() {
    let state = ClusterState::with_options("manager".to_string(), Some(5.0), Some(30.0), 120);
    state.register_hello(
        AgentHello {
            node_id: "node-a".to_string(),
            hostname: "host-a".to_string(),
            agent_version: None,
            capabilities: None,
            hardware: None,
        },
        Some(10.0),
        None,
    );
    state
        .ingest_sample(&sample_message("node-a", 1, 50), Some(10.0), None)
        .unwrap();

    assert_eq!(state.snapshot(Some(16.0)).nodes[0].status, "stale");
    assert_eq!(state.snapshot(Some(41.0)).nodes[0].status, "offline");

    state.ingest_heartbeat("node-a", Some(3), Some(42.0), None);
    assert_eq!(state.snapshot(Some(42.1)).nodes[0].status, "online");

    state.disconnect("node-a", Some(43.0), None);
    assert_eq!(state.snapshot(Some(43.0)).nodes[0].status, "offline");
}

#[test]
fn cluster_state_keeps_static_hardware_from_hello() {
    let state = ClusterState::new("manager".to_string());
    let hello = parse_agent_hello(&json!({
        "type": "hello",
        "node_id": "node-a",
        "hostname": "host-a",
        "hardware": {"gpus": [{"index": 0, "uuid": "GPU-abc", "name": "NVIDIA H100", "architecture": "Hopper"}]}
    }))
    .unwrap();

    state.register_hello(hello, Some(10.0), None);
    state
        .ingest_sample(&sample_message("node-a", 1, 50), Some(11.0), None)
        .unwrap();

    let value = serde_json::to_value(state.snapshot(Some(11.0))).unwrap();
    assert_eq!(
        value["nodes"][0]["hardware"]["gpus"][0]["architecture"],
        "Hopper"
    );
    assert!(value["nodes"][0]["gpus"][0].get("architecture").is_none());
}
