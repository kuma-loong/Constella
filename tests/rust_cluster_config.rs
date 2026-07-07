use constella::cluster_config::{
    load_cluster_config, load_manager_hostname, ClusterConfigError, ClusterNode,
};

#[test]
fn load_cluster_config_resolves_relative_token_file() {
    let dir = tempfile::tempdir().unwrap();
    let token_file = dir.path().join("run/agent-token");
    std::fs::create_dir_all(token_file.parent().unwrap()).unwrap();
    std::fs::write(&token_file, "secret\n").unwrap();
    let nodes_file = dir.path().join("nodes.yaml");
    std::fs::write(
        &nodes_file,
        r#"
manager_hostname: H100
manager_url: ws://manager:8765/api/agents/ws
agent_token_file: run/agent-token
refresh_interval: 2.0
process_interval: 5.0
nodes:
  - id: gpu-node-01
    host: gpu-node-01
    user: alice
    port: 2222
"#,
    )
    .unwrap();

    let config = load_cluster_config(&nodes_file).unwrap();

    assert_eq!(config.manager_url, "ws://manager:8765/api/agents/ws");
    assert_eq!(config.agent_token_file, token_file.canonicalize().unwrap());
    assert_eq!(config.manager_hostname.as_deref(), Some("H100"));
    assert_eq!(config.refresh_interval, 2.0);
    assert_eq!(config.process_interval, 5.0);
    assert_eq!(
        config.nodes[0],
        ClusterNode {
            id: "gpu-node-01".to_string(),
            host: "gpu-node-01".to_string(),
            user: Some("alice".to_string()),
            port: Some(2222),
        }
    );
    assert_eq!(
        load_manager_hostname(&nodes_file).unwrap().as_deref(),
        Some("H100")
    );
}

#[test]
fn load_cluster_config_uses_defaults_and_rejects_empty_nodes() {
    let dir = tempfile::tempdir().unwrap();
    let nodes_file = dir.path().join("nodes.yaml");
    let token_file = dir.path().join("agent-token");
    std::fs::write(&token_file, "secret\n").unwrap();
    std::fs::write(
        &nodes_file,
        r#"
manager_url: ws://manager:8765/api/agents/ws
agent_token_file: agent-token
nodes:
  - id: gpu-node-01
    host: gpu-node-01
"#,
    )
    .unwrap();

    let config = load_cluster_config(&nodes_file).unwrap();

    assert_eq!(config.refresh_interval, 1.0);
    assert_eq!(config.process_interval, 5.0);

    std::fs::write(
        &nodes_file,
        r#"
manager_url: ws://manager:8765/api/agents/ws
agent_token_file: agent-token
nodes: []
"#,
    )
    .unwrap();

    let error = load_cluster_config(&nodes_file).unwrap_err();

    assert!(matches!(error, ClusterConfigError::EmptyNodes));
}
