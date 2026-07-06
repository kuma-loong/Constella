use std::io::Write;
use std::process::{Command, Output, Stdio};
use std::sync::Arc;
use std::thread;

use crate::cluster_config::{ClusterConfig, ClusterNode};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeCommandResult {
    pub node_id: String,
    pub ok: bool,
    pub action: String,
    pub output: String,
    pub error: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ClusterController {
    config: ClusterConfig,
    project_root: std::path::PathBuf,
    sync_binary: bool,
}

impl ClusterController {
    pub fn new(config: ClusterConfig, project_root: impl Into<std::path::PathBuf>) -> Self {
        Self {
            config,
            project_root: project_root.into(),
            sync_binary: true,
        }
    }

    pub fn with_sync_binary(mut self, sync_binary: bool) -> Self {
        self.sync_binary = sync_binary;
        self
    }

    pub fn start_all(&self) -> Vec<NodeCommandResult> {
        let token = match std::fs::read_to_string(&self.config.agent_token_file) {
            Ok(token) => Arc::new(token.trim().to_string()),
            Err(error) => return failed_for_all(&self.config, "start", error.to_string()),
        };
        if token.is_empty() {
            return failed_for_all(
                &self.config,
                "start",
                "agent token file is empty".to_string(),
            );
        }
        let binary = if self.sync_binary {
            match std::fs::read(self.project_root.join("target/release/constella")) {
                Ok(binary) => Some(Arc::new(binary)),
                Err(error) => return failed_for_all(&self.config, "start", error.to_string()),
            }
        } else {
            None
        };
        let config = Arc::new(self.config.clone());
        parallel_nodes(config.nodes.clone(), "start", move |node| {
            start_node(&config, &node, &token, binary.as_deref().map(Vec::as_slice))
        })
    }

    pub fn status_all(&self) -> Vec<NodeCommandResult> {
        let config = Arc::new(self.config.clone());
        parallel_nodes(config.nodes.clone(), "status", move |node| {
            status_node(&config, &node)
        })
    }

    pub fn stop_all(&self) -> Vec<NodeCommandResult> {
        let config = Arc::new(self.config.clone());
        parallel_nodes(config.nodes.clone(), "stop", move |node| {
            stop_node(&config, &node)
        })
    }
}

pub fn start_node(
    config: &ClusterConfig,
    node: &ClusterNode,
    token: &str,
    binary: Option<&[u8]>,
) -> NodeCommandResult {
    let action = "start";
    let result = (|| -> Result<Output, String> {
        run_ssh(node, &remote_mkdir_command(&config.remote_base), None)?;
        if let Some(binary) = binary {
            write_remote_file(
                node,
                &remote_join(&config.remote_base, &["agent", "bin", "constella"]),
                binary,
                "700",
            )?;
        }
        write_remote_file(
            node,
            &remote_join(&config.remote_base, &["run", "agent.env"]),
            render_agent_env(config, node, token).as_bytes(),
            "600",
        )?;
        write_remote_file(
            node,
            &remote_join(&config.remote_base, &["agent", "start_agent.sh"]),
            render_start_script(&config.remote_base).as_bytes(),
            "700",
        )?;
        run_ssh(
            node,
            &format!(
                "bash {}",
                shell_path(&remote_join(
                    &config.remote_base,
                    &["agent", "start_agent.sh"]
                ))
            ),
            None,
        )
    })();
    result_from_output(node, action, result)
}

pub fn status_node(config: &ClusterConfig, node: &ClusterNode) -> NodeCommandResult {
    result_from_output(
        node,
        "status",
        run_ssh(node, &render_status_command(&config.remote_base), None),
    )
}

pub fn stop_node(config: &ClusterConfig, node: &ClusterNode) -> NodeCommandResult {
    result_from_output(
        node,
        "stop",
        run_ssh(node, &render_stop_command(&config.remote_base), None),
    )
}

pub fn render_agent_env(config: &ClusterConfig, node: &ClusterNode, token: &str) -> String {
    let values = [
        ("CONSTELLA_NODE_ID", shell_quote(&node.id)),
        ("CONSTELLA_MANAGER_URL", shell_quote(&config.manager_url)),
        ("CONSTELLA_AGENT_TOKEN", shell_quote(token)),
        (
            "CONSTELLA_REFRESH_SECONDS",
            shell_quote(&config.refresh_interval.to_string()),
        ),
        (
            "CONSTELLA_PROCESS_SECONDS",
            shell_quote(&config.process_interval.to_string()),
        ),
        (
            "CONSTELLA_AGENT_STATE_FILE",
            shell_path(&remote_join(
                &config.remote_base,
                &["run", "agent-state.json"],
            )),
        ),
    ];
    values
        .into_iter()
        .map(|(key, value)| format!("{key}={value}"))
        .collect::<Vec<_>>()
        .join("\n")
        + "\n"
}

pub fn render_start_script(remote_base: &str) -> String {
    format!(
        r#"#!/usr/bin/env bash
set -euo pipefail

BASE={base}
PID="$BASE/run/agent.pid"
LOG="$BASE/logs/agent.log"
ENV_FILE="$BASE/run/agent.env"
BIN="$BASE/agent/bin/constella"

if [ -s "$PID" ]; then
  old_pid="$(cat "$PID" || true)"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    echo "running $old_pid"
    exit 0
  fi
  rm -f "$PID"
fi

if [ ! -x "$BIN" ]; then
  echo "missing agent binary: $BIN" >&2
  exit 1
fi

set -a
. "$ENV_FILE"
set +a

nohup "$BIN" agent >> "$LOG" 2>&1 &
echo $! > "$PID"
sleep 0.2
if kill -0 "$(cat "$PID")" 2>/dev/null; then
  echo "started $(cat "$PID")"
else
  echo "failed to start" >&2
  exit 1
fi
"#,
        base = shell_path(remote_base)
    )
}

pub fn render_status_command(remote_base: &str) -> String {
    let state_path = remote_join(remote_base, &["run", "agent-state.json"]);
    let pid_path = remote_join(remote_base, &["run", "agent.pid"]);
    format!(
        "if [ -s {pid} ] && kill -0 \"$(cat {pid})\" 2>/dev/null; then echo running; else echo stopped; fi; if [ -f {state} ]; then cat {state}; fi",
        pid = shell_path(&pid_path),
        state = shell_path(&state_path)
    )
}

pub fn render_stop_command(remote_base: &str) -> String {
    let pid_path = remote_join(remote_base, &["run", "agent.pid"]);
    format!(
        "if [ -s {pid} ]; then pid=\"$(cat {pid})\"; if [ -n \"$pid\" ] && kill -0 \"$pid\" 2>/dev/null; then kill \"$pid\"; echo stopped \"$pid\"; else echo not-running; fi; rm -f {pid}; else echo not-running; fi",
        pid = shell_path(&pid_path)
    )
}

pub fn remote_mkdir_command(remote_base: &str) -> String {
    let paths = ["agent", "agent/bin", "run", "logs"]
        .into_iter()
        .map(|path| shell_path(&remote_join(remote_base, &[path])))
        .collect::<Vec<_>>()
        .join(" ");
    format!("mkdir -p {paths}")
}

pub fn remote_join(base: &str, parts: &[&str]) -> String {
    let mut output = base.trim_end_matches('/').to_string();
    for part in parts {
        output.push('/');
        output.push_str(part.trim_matches('/'));
    }
    output
}

pub fn shell_path(value: &str) -> String {
    if value == "$HOME" || value.starts_with("$HOME/") {
        value.to_string()
    } else {
        shell_quote(value)
    }
}

pub fn shell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\"'\"'"))
}

pub fn ssh_command(node: &ClusterNode, command: &str) -> Vec<String> {
    let mut args = vec!["ssh".to_string()];
    if let Some(port) = node.port {
        args.extend(["-p".to_string(), port.to_string()]);
    }
    args.extend([node_target(node), command.to_string()]);
    args
}

pub fn node_target(node: &ClusterNode) -> String {
    node.user
        .as_ref()
        .map(|user| format!("{user}@{}", node.host))
        .unwrap_or_else(|| node.host.clone())
}

pub fn format_results(results: &[NodeCommandResult]) -> String {
    results
        .iter()
        .map(|result| {
            let state = if result.ok { "ok" } else { "failed" };
            let detail = if result.output.is_empty() {
                result.error.as_deref().unwrap_or("")
            } else {
                result.output.as_str()
            };
            format!(
                "{}\t{}\t{}\t{}",
                result.node_id, result.action, state, detail
            )
            .trim_end()
            .to_string()
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn parallel_nodes<F>(nodes: Vec<ClusterNode>, action: &'static str, f: F) -> Vec<NodeCommandResult>
where
    F: Fn(ClusterNode) -> NodeCommandResult + Send + Sync + 'static,
{
    let f = Arc::new(f);
    let mut handles = Vec::with_capacity(nodes.len());
    for node in nodes {
        let f = f.clone();
        handles.push(thread::spawn(move || f(node)));
    }
    let mut results = handles
        .into_iter()
        .map(|handle| {
            handle.join().unwrap_or_else(|_| NodeCommandResult {
                node_id: "?".to_string(),
                ok: false,
                action: action.to_string(),
                output: String::new(),
                error: Some("worker thread panicked".to_string()),
            })
        })
        .collect::<Vec<_>>();
    results.sort_by(|left, right| left.node_id.cmp(&right.node_id));
    results
}

fn failed_for_all(config: &ClusterConfig, action: &str, error: String) -> Vec<NodeCommandResult> {
    config
        .nodes
        .iter()
        .map(|node| NodeCommandResult {
            node_id: node.id.clone(),
            ok: false,
            action: action.to_string(),
            output: String::new(),
            error: Some(error.clone()),
        })
        .collect()
}

fn write_remote_file(
    node: &ClusterNode,
    remote_path: &str,
    content: &[u8],
    mode: &str,
) -> Result<Output, String> {
    run_ssh(
        node,
        &format!(
            "umask 077; cat > {path}; chmod {mode} {path}",
            path = shell_path(remote_path),
            mode = shell_quote(mode)
        ),
        Some(content),
    )
}

fn run_ssh(
    node: &ClusterNode,
    remote_command: &str,
    input: Option<&[u8]>,
) -> Result<Output, String> {
    let args = ssh_command(node, remote_command);
    let mut command = Command::new(&args[0]);
    command.args(&args[1..]);
    command.stdout(Stdio::piped()).stderr(Stdio::piped());
    if input.is_some() {
        command.stdin(Stdio::piped());
    }
    let mut child = command.spawn().map_err(|error| error.to_string())?;
    if let Some(input) = input {
        let stdin = child
            .stdin
            .as_mut()
            .ok_or_else(|| "failed to open ssh stdin".to_string())?;
        stdin.write_all(input).map_err(|error| error.to_string())?;
    }
    child.wait_with_output().map_err(|error| error.to_string())
}

fn result_from_output(
    node: &ClusterNode,
    action: &str,
    result: Result<Output, String>,
) -> NodeCommandResult {
    match result {
        Ok(output) => {
            let stdout = String::from_utf8_lossy(&output.stdout);
            let stderr = String::from_utf8_lossy(&output.stderr);
            let text = format!("{stdout}{stderr}").trim().to_string();
            NodeCommandResult {
                node_id: node.id.clone(),
                ok: output.status.success(),
                action: action.to_string(),
                output: text,
                error: if output.status.success() {
                    None
                } else {
                    Some(stderr.trim().to_string())
                },
            }
        }
        Err(error) => NodeCommandResult {
            node_id: node.id.clone(),
            ok: false,
            action: action.to_string(),
            output: String::new(),
            error: Some(error),
        },
    }
}
