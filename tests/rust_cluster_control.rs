use constella::cluster_config::{ClusterConfig, ClusterNode};
use constella::cluster_control::{
    format_results, remote_join, remote_mkdir_command, render_agent_env, render_start_script,
    render_status_command, render_stop_command, shell_path, shell_quote, ssh_command,
    NodeCommandResult,
};

fn node() -> ClusterNode {
    ClusterNode {
        id: "gpu-node-01".to_string(),
        host: "10.0.0.5".to_string(),
        user: Some("alice".to_string()),
        port: Some(2222),
    }
}

fn config() -> ClusterConfig {
    ClusterConfig {
        manager_hostname: Some("manager".to_string()),
        manager_url: "ws://manager:8765/api/agents/ws".to_string(),
        agent_token_file: "run/agent-token".into(),
        refresh_interval: 1.0,
        process_interval: 3.0,
        remote_base: "$HOME/.constella".to_string(),
        nodes: vec![node()],
    }
}

#[test]
fn render_agent_env_writes_token_to_env_file_only() {
    let env = render_agent_env(&config(), &node(), "tok'en");

    assert!(env.contains("CONSTELLA_NODE_ID='gpu-node-01'"));
    assert!(env.contains("CONSTELLA_MANAGER_URL='ws://manager:8765/api/agents/ws'"));
    assert!(env.contains("CONSTELLA_AGENT_TOKEN='tok'\"'\"'en'"));
    assert!(env.contains("CONSTELLA_AGENT_STATE_FILE=$HOME/.constella/run/agent-state.json"));
}

#[test]
fn render_start_script_runs_rust_agent_binary() {
    let script = render_start_script("$HOME/.constella");

    assert!(script.contains("BIN=\"$BASE/agent/bin/constella\""));
    assert!(script.contains("nohup \"$BIN\" agent"));
    assert!(!script.contains("python"));
}

#[test]
fn render_status_and_stop_commands_use_remote_state_paths() {
    let status = render_status_command("$HOME/.constella");
    let stop = render_stop_command("$HOME/.constella");

    assert!(status.contains("$HOME/.constella/run/agent.pid"));
    assert!(status.contains("$HOME/.constella/run/agent-state.json"));
    assert!(stop.contains("kill \"$pid\""));
    assert!(stop.contains("rm -f $HOME/.constella/run/agent.pid"));
}

#[test]
fn ssh_command_includes_user_host_and_port() {
    assert_eq!(
        ssh_command(&node(), "echo ok"),
        vec!["ssh", "-p", "2222", "alice@10.0.0.5", "echo ok"]
    );
}

#[test]
fn shell_helpers_preserve_home_expansion_and_quote_other_paths() {
    assert_eq!(
        remote_join("$HOME/.constella", &["run", "agent.pid"]),
        "$HOME/.constella/run/agent.pid"
    );
    assert_eq!(shell_path("$HOME/.constella/run"), "$HOME/.constella/run");
    assert_eq!(shell_quote("a'b"), "'a'\"'\"'b'");
    assert_eq!(shell_path("/tmp/a b"), "'/tmp/a b'");
}

#[test]
fn remote_mkdir_includes_binary_directory() {
    let command = remote_mkdir_command("$HOME/.constella");

    assert!(command.contains("$HOME/.constella/agent/bin"));
    assert!(command.contains("$HOME/.constella/logs"));
}

#[test]
fn format_results_matches_python_table_shape() {
    let output = format_results(&[
        NodeCommandResult {
            node_id: "a".to_string(),
            ok: true,
            action: "status".to_string(),
            output: "running".to_string(),
            error: None,
        },
        NodeCommandResult {
            node_id: "b".to_string(),
            ok: false,
            action: "status".to_string(),
            output: String::new(),
            error: Some("ssh failed".to_string()),
        },
    ]);

    assert_eq!(
        output,
        "a\tstatus\tok\trunning\nb\tstatus\tfailed\tssh failed"
    );
}
