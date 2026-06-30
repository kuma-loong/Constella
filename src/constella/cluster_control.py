from __future__ import annotations

import concurrent.futures
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


@dataclass(slots=True)
class ClusterNode:
    id: str
    host: str
    user: str | None = None
    port: int | None = None

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host


@dataclass(slots=True)
class ClusterConfig:
    manager_url: str
    agent_token_file: Path
    nodes: list[ClusterNode]
    refresh_interval: float = 1.0
    process_interval: float = 3.0
    remote_base: str = "$HOME/.constella"


@dataclass(slots=True)
class NodeCommandResult:
    node_id: str
    ok: bool
    action: str
    output: str = ""
    error: str | None = None


class CommandRunner:
    def run(
        self,
        cmd: list[str],
        *,
        input_text: str | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            input=input_text,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def pipe(
        self,
        left_cmd: list[str],
        right_cmd: list[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        left = subprocess.Popen(left_cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert left.stdout is not None
        right = subprocess.run(
            right_cmd,
            stdin=left.stdout,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        left.stdout.close()
        left_stderr = left.stderr.read().decode("utf-8", errors="replace") if left.stderr else ""
        left_rc = left.wait()
        if left_rc != 0:
            return subprocess.CompletedProcess(
                left_cmd,
                left_rc,
                stdout="",
                stderr=left_stderr,
            )
        return right


class ClusterController:
    def __init__(
        self,
        config: ClusterConfig,
        *,
        project_root: Path,
        runner: CommandRunner | None = None,
        sync_source: bool = True,
    ):
        self.config = config
        self.project_root = project_root
        self.runner = runner or CommandRunner()
        self.sync_source = sync_source

    def start_all(self) -> list[NodeCommandResult]:
        token = self.config.agent_token_file.read_text(encoding="utf-8").strip()
        return self._parallel("start", lambda node: self.start_node(node, token))

    def status_all(self) -> list[NodeCommandResult]:
        return self._parallel("status", self.status_node)

    def stop_all(self) -> list[NodeCommandResult]:
        return self._parallel("stop", self.stop_node)

    def start_node(self, node: ClusterNode, token: str) -> NodeCommandResult:
        try:
            self._ssh(
                node,
                remote_mkdir_command(self.config.remote_base),
            )
            if self.sync_source:
                self._sync_source(node)
            self._write_remote_file(
                node,
                remote_join(self.config.remote_base, "run", "agent.env"),
                render_agent_env(self.config, node, token),
                mode="600",
            )
            self._write_remote_file(
                node,
                remote_join(self.config.remote_base, "agent", "start_agent.sh"),
                render_start_script(self.config.remote_base),
                mode="700",
            )
            result = self._ssh(
                node,
                f"bash {shell_path(remote_join(self.config.remote_base, 'agent', 'start_agent.sh'))}",
            )
            return NodeCommandResult(
                node_id=node.id,
                ok=result.returncode == 0,
                action="start",
                output=(result.stdout + result.stderr).strip(),
                error=None if result.returncode == 0 else result.stderr.strip(),
            )
        except Exception as exc:
            return NodeCommandResult(node_id=node.id, ok=False, action="start", error=str(exc))

    def status_node(self, node: ClusterNode) -> NodeCommandResult:
        command = render_status_command(self.config.remote_base)
        result = self._ssh(node, command)
        return NodeCommandResult(
            node_id=node.id,
            ok=result.returncode == 0,
            action="status",
            output=(result.stdout + result.stderr).strip(),
            error=None if result.returncode == 0 else result.stderr.strip(),
        )

    def stop_node(self, node: ClusterNode) -> NodeCommandResult:
        result = self._ssh(node, render_stop_command(self.config.remote_base))
        return NodeCommandResult(
            node_id=node.id,
            ok=result.returncode == 0,
            action="stop",
            output=(result.stdout + result.stderr).strip(),
            error=None if result.returncode == 0 else result.stderr.strip(),
        )

    def _parallel(
        self,
        action: str,
        func: Any,
    ) -> list[NodeCommandResult]:
        results: list[NodeCommandResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(self.config.nodes) or 1)) as executor:
            future_to_node = {executor.submit(func, node): node for node in self.config.nodes}
            for future in concurrent.futures.as_completed(future_to_node):
                node = future_to_node[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(NodeCommandResult(node_id=node.id, ok=False, action=action, error=str(exc)))
        return sorted(results, key=lambda item: item.node_id)

    def _ssh(
        self,
        node: ClusterNode,
        command: str,
        *,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self.runner.run(ssh_command(node, command), input_text=input_text)

    def _write_remote_file(self, node: ClusterNode, remote_path: str, content: str, *, mode: str) -> None:
        command = (
            "umask 077; "
            f"cat > {shell_path(remote_path)}; "
            f"chmod {shlex.quote(mode)} {shell_path(remote_path)}"
        )
        result = self._ssh(node, command, input_text=content)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"failed to write {remote_path}")

    def _sync_source(self, node: ClusterNode) -> None:
        remote_src = remote_join(self.config.remote_base, "agent", "src")
        self._ssh(node, f"rm -rf {shell_path(remote_src)} && mkdir -p {shell_path(remote_src)}")
        tar_cmd = [
            "tar",
            "--exclude=.git",
            "--exclude=.venv",
            "--exclude=frontend/node_modules",
            "--exclude=frontend/dist",
            "--exclude=run",
            "-czf",
            "-",
            ".",
        ]
        result = self.runner.pipe(
            tar_cmd,
            ssh_command(node, f"tar -xzf - -C {shell_path(remote_src)}"),
            cwd=self.project_root,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"failed to sync source to {node.id}")


def load_cluster_config(path: Path) -> ClusterConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("nodes config must be a mapping")
    nodes_raw = raw.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise ValueError("nodes config must contain at least one node")
    token_file = Path(str(raw.get("agent_token_file") or "run/agent-token"))
    if not token_file.is_absolute():
        token_file = (path.parent / token_file).resolve()
    return ClusterConfig(
        manager_url=str(raw["manager_url"]),
        agent_token_file=token_file,
        nodes=[parse_node(item) for item in nodes_raw],
        refresh_interval=float(raw.get("refresh_interval", 1.0)),
        process_interval=float(raw.get("process_interval", 3.0)),
        remote_base=str(raw.get("remote_base", "$HOME/.constella")),
    )


def parse_node(item: Any) -> ClusterNode:
    if not isinstance(item, dict):
        raise ValueError("node entry must be a mapping")
    node_id = str(item.get("id") or "").strip()
    host = str(item.get("host") or "").strip()
    if not node_id or not host:
        raise ValueError("node entry requires id and host")
    port = item.get("port")
    return ClusterNode(
        id=node_id,
        host=host,
        user=item.get("user"),
        port=int(port) if port is not None else None,
    )


def render_agent_env(config: ClusterConfig, node: ClusterNode, token: str) -> str:
    values = {
        "CONSTELLA_NODE_ID": node.id,
        "CONSTELLA_MANAGER_URL": config.manager_url,
        "CONSTELLA_AGENT_TOKEN": token,
        "CONSTELLA_REFRESH_SECONDS": str(config.refresh_interval),
        "CONSTELLA_PROCESS_SECONDS": str(config.process_interval),
        "CONSTELLA_AGENT_STATE_FILE": remote_join(config.remote_base, "run", "agent-state.json"),
    }
    lines = [
        f"CONSTELLA_NODE_ID={shlex.quote(values['CONSTELLA_NODE_ID'])}",
        f"CONSTELLA_MANAGER_URL={shlex.quote(values['CONSTELLA_MANAGER_URL'])}",
        f"CONSTELLA_AGENT_TOKEN={shlex.quote(values['CONSTELLA_AGENT_TOKEN'])}",
        f"CONSTELLA_REFRESH_SECONDS={shlex.quote(values['CONSTELLA_REFRESH_SECONDS'])}",
        f"CONSTELLA_PROCESS_SECONDS={shlex.quote(values['CONSTELLA_PROCESS_SECONDS'])}",
        f"CONSTELLA_AGENT_STATE_FILE={shell_path(values['CONSTELLA_AGENT_STATE_FILE'])}",
    ]
    return "\n".join(lines) + "\n"


def render_start_script(remote_base: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

BASE={shell_path(remote_base)}
PID="$BASE/run/agent.pid"
LOG="$BASE/logs/agent.log"
ENV_FILE="$BASE/run/agent.env"
SRC="$BASE/agent/src"

if [ -s "$PID" ]; then
  old_pid="$(cat "$PID" || true)"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    echo "running $old_pid"
    exit 0
  fi
  rm -f "$PID"
fi

set -a
. "$ENV_FILE"
set +a

cd "$SRC"
nohup uv run constella agent >> "$LOG" 2>&1 &
echo $! > "$PID"
sleep 0.2
if kill -0 "$(cat "$PID")" 2>/dev/null; then
  echo "started $(cat "$PID")"
else
  echo "failed to start" >&2
  exit 1
fi
"""


def render_status_command(remote_base: str) -> str:
    state_path = remote_join(remote_base, "run", "agent-state.json")
    pid_path = remote_join(remote_base, "run", "agent.pid")
    return (
        f"if [ -s {shell_path(pid_path)} ] && kill -0 \"$(cat {shell_path(pid_path)})\" 2>/dev/null; "
        "then echo running; else echo stopped; fi; "
        f"if [ -f {shell_path(state_path)} ]; then cat {shell_path(state_path)}; fi"
    )


def render_stop_command(remote_base: str) -> str:
    pid_path = remote_join(remote_base, "run", "agent.pid")
    return (
        f"if [ -s {shell_path(pid_path)} ]; then "
        f"pid=\"$(cat {shell_path(pid_path)})\"; "
        "if [ -n \"$pid\" ] && kill -0 \"$pid\" 2>/dev/null; then kill \"$pid\"; echo stopped \"$pid\"; "
        "else echo not-running; fi; "
        f"rm -f {shell_path(pid_path)}; "
        "else echo not-running; fi"
    )


def remote_mkdir_command(remote_base: str) -> str:
    paths = [
        remote_join(remote_base, "agent"),
        remote_join(remote_base, "run"),
        remote_join(remote_base, "logs"),
    ]
    return "mkdir -p " + " ".join(shell_path(path) for path in paths)


def remote_join(base: str, *parts: str) -> str:
    return "/".join([base.rstrip("/"), *(part.strip("/") for part in parts)])


def shell_path(value: str) -> str:
    if value.startswith("$HOME/"):
        return value
    return shlex.quote(value)


def ssh_command(node: ClusterNode, command: str) -> list[str]:
    cmd = ["ssh"]
    if node.port is not None:
        cmd.extend(["-p", str(node.port)])
    cmd.extend([node.target, command])
    return cmd


def format_results(results: Iterable[NodeCommandResult]) -> str:
    lines: list[str] = []
    for result in results:
        state = "ok" if result.ok else "failed"
        detail = result.output or result.error or ""
        lines.append(f"{result.node_id}\t{result.action}\t{state}\t{detail}".rstrip())
    return "\n".join(lines)
