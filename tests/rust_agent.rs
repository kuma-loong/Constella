use constella::agent::{
    agent_heartbeat, agent_hello, agent_sample, apply_manager_message, hardware_from_snapshot,
    reconnect_delay, run_connection, snapshot_to_agent_payload, write_state_file, AgentConfig,
    AgentError, AgentStatus,
};
use constella::collector::SnapshotCollector;
use constella::schema::{GpuInfo, NodeHardware, Snapshot};
use futures_util::{SinkExt, StreamExt};
use serde_json::Value;
use tokio_tungstenite::tungstenite::Message;

fn config() -> AgentConfig {
    AgentConfig {
        node_id: "node-a".to_string(),
        manager_url: "ws://127.0.0.1:8765/api/agents/ws".to_string(),
        token: "secret".to_string(),
        refresh_interval: 1.0,
        process_interval: 3.0,
        state_file: "agent-state.json".into(),
        heartbeat_seconds: 10.0,
    }
}

fn snapshot() -> Snapshot {
    Snapshot {
        ok: true,
        source: "nvidia-smi".to_string(),
        hostname: "worker".to_string(),
        timestamp: 10.0,
        elapsed_ms: 2.0,
        gpus: vec![GpuInfo {
            index: 0,
            uuid: "GPU-1".to_string(),
            utilization_gpu: 40,
            memory_total_mb: 100,
            memory_used_mb: 25,
            ..Default::default()
        }],
        driver_version: None,
        cuda_driver_version: None,
        nvml_version: None,
        error: None,
        seq: 7,
        refresh_interval: 1.0,
        history: [(
            "0".to_string(),
            [("gpu".to_string(), vec![10.0, 40.0])]
                .into_iter()
                .collect(),
        )]
        .into_iter()
        .collect(),
    }
}

#[test]
fn agent_hello_matches_manager_contract() {
    let message = agent_hello(&config(), Some(NodeHardware::default()));

    assert_eq!(message["type"], "hello");
    assert_eq!(message["schema_version"], 1);
    assert_eq!(message["node_id"], "node-a");
    assert_eq!(message["agent_version"], env!("CARGO_PKG_VERSION"));
    assert_eq!(message["capabilities"]["nvidia_smi_fallback"], true);
    assert!(message.get("hardware").is_some());
}

#[test]
fn hardware_inventory_uses_snapshot_gpu_identity() {
    let hardware = hardware_from_snapshot(&snapshot());

    assert_eq!(hardware.gpus.len(), 1);
    assert_eq!(hardware.gpus[0].index, 0);
    assert_eq!(hardware.gpus[0].uuid, "GPU-1");
    assert_eq!(hardware.gpus[0].name, "unknown");
}

#[test]
fn agent_config_reads_trimmed_token_file() {
    let dir = tempfile::tempdir().unwrap();
    let token_file = dir.path().join("agent-token");
    std::fs::write(&token_file, " token-value\n").unwrap();

    let config = AgentConfig::from_env(
        Some("node-a".to_string()),
        Some("ws://127.0.0.1:8765/api/agents/ws".to_string()),
        None,
        Some(token_file),
        Some(1.0),
        Some(3.0),
        Some(dir.path().join("state.json")),
    )
    .unwrap();

    assert_eq!(config.token, "token-value");
    assert_eq!(config.node_id, "node-a");
    assert_eq!(config.refresh_interval, 1.0);
}

#[test]
fn agent_config_requires_token() {
    let error = AgentConfig::from_env(
        Some("node-a".to_string()),
        Some("ws://127.0.0.1:8765/api/agents/ws".to_string()),
        None,
        None,
        Some(1.0),
        Some(3.0),
        Some("state.json".into()),
    )
    .unwrap_err();

    assert!(matches!(error, AgentError::MissingToken));
}

#[test]
fn agent_sample_omits_frontend_history_payload() {
    let message = agent_sample(&config(), 3, &snapshot(), 5.0);

    assert_eq!(message["type"], "sample");
    assert_eq!(message["node_id"], "node-a");
    assert_eq!(message["seq"], 3);
    assert_eq!(message["sampled_at"], 10.0);
    assert_eq!(message["process_interval"], 5.0);
    assert_eq!(message["snapshot"]["gpus"][0]["uuid"], "GPU-1");
    assert!(message["snapshot"].get("history").is_none());
}

#[test]
fn snapshot_to_agent_payload_removes_history_only() {
    let payload = snapshot_to_agent_payload(&snapshot());

    assert_eq!(payload["hostname"], "worker");
    assert_eq!(payload["seq"], 7);
    assert!(payload.get("history").is_none());
}

#[test]
fn heartbeat_includes_node_and_sequence() {
    let message = agent_heartbeat(&config(), 9);

    assert_eq!(message["type"], "heartbeat");
    assert_eq!(message["schema_version"], 1);
    assert_eq!(message["node_id"], "node-a");
    assert_eq!(message["seq"], 9);
    assert!(message["timestamp"].as_f64().unwrap() > 0.0);
}

#[test]
fn manager_config_updates_collector_intervals() {
    let mut collector = SnapshotCollector::new(1.0, 3.0, 120).unwrap();

    apply_manager_message(
        &mut collector,
        r#"{"type":"config","refresh_interval":2.0,"process_interval":6.0}"#,
    );

    assert_eq!(collector.refresh_interval, 2.0);
    assert_eq!(collector.process_interval(), 6.0);
}

#[test]
fn manager_config_ignores_invalid_refresh() {
    let mut collector = SnapshotCollector::new(1.0, 3.0, 120).unwrap();

    apply_manager_message(
        &mut collector,
        r#"{"type":"config","refresh_interval":3.0,"process_interval":0.1}"#,
    );

    assert_eq!(collector.refresh_interval, 1.0);
    assert_eq!(collector.process_interval(), 1.0);
}

#[test]
fn write_state_file_is_atomic_json() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("run").join("agent-state.json");
    let status = AgentStatus {
        node_id: "node-a".to_string(),
        pid: 42,
        status: "online".to_string(),
        last_sample_at: Some(1.5),
        last_sent_at: Some(2.5),
        last_error: None,
    };

    write_state_file(&path, &status).unwrap();

    let value: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    assert_eq!(value["node_id"], "node-a");
    assert_eq!(value["status"], "online");
    assert_eq!(value["last_sample_at"], 1.5);
}

#[test]
fn reconnect_delay_caps_at_largest_delay() {
    assert_eq!(reconnect_delay(0), 1.0);
    assert_eq!(reconnect_delay(2), 5.0);
    assert_eq!(reconnect_delay(99), 30.0);
}

#[tokio::test]
async fn agent_connection_sends_hello_and_sample() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut websocket = tokio_tungstenite::accept_async(stream).await.unwrap();
        let hello = websocket
            .next()
            .await
            .unwrap()
            .unwrap()
            .into_text()
            .unwrap();
        let hello: Value = serde_json::from_str(&hello).unwrap();
        assert_eq!(hello["type"], "hello");
        assert_eq!(hello["node_id"], "node-a");

        websocket
            .send(Message::Text(
                r#"{"type":"config","refresh_interval":0.5,"process_interval":1.0}"#.to_string(),
            ))
            .await
            .unwrap();
        let mut sample = None;
        for _ in 0..4 {
            let raw = websocket
                .next()
                .await
                .unwrap()
                .unwrap()
                .into_text()
                .unwrap();
            let value: Value = serde_json::from_str(&raw).unwrap();
            if value["type"] == "sample" {
                sample = Some(value);
                break;
            }
        }
        let sample = sample.expect("agent should send a sample");
        assert_eq!(sample["type"], "sample");
        assert_eq!(sample["node_id"], "node-a");
        assert!(sample["snapshot"].is_object());
    });
    let dir = tempfile::tempdir().unwrap();
    let mut config = config();
    config.manager_url = format!("ws://{addr}");
    config.state_file = dir.path().join("agent-state.json");
    config.refresh_interval = 0.5;
    config.heartbeat_seconds = 5.0;
    let mut collector = SnapshotCollector::new(0.5, 1.0, 120).unwrap();
    let mut status = AgentStatus::starting(config.node_id.clone());

    let result = tokio::time::timeout(
        std::time::Duration::from_secs(5),
        run_connection(&config, &mut collector, &mut status, None),
    )
    .await;

    assert!(result.is_ok());
    let _ = server.await.unwrap();
    assert_eq!(status.status, "online");
    assert!(config.state_file.exists());
}
