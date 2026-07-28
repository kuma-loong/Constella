from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .cluster_control import ClusterController, load_cluster_config
from .collector import validate_device_type
from .paths import default_build_root, default_project_root


PopenFactory = Callable[..., subprocess.Popen[bytes]]


@dataclass(slots=True)
class ServiceConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    refresh: float = 1.0
    process_refresh: float = 5.0
    graceful_timeout: float = 10.0
    log_level: str = "info"
    run_dir: Path = Path("run")
    log_dir: Path = Path("logs")
    local_agent: bool = True
    local_agent_node_id: str | None = None
    local_agent_manager_url: str | None = None
    local_agent_device: str = "nvidia"
    manager_hostname: str | None = None
    agent_token_file: Path | None = None
    db_path: Path | None = Path("run/constella.db")
    db_queue_size: int = 1024
    raw_snapshot_seconds: float = 0.0
    frontend_dir: Path | None = None
    highres_sidecar: bool = False
    highres_host: str = "127.0.0.1"
    highres_port: int = 8766
    highres_token_file: Path | None = None
    highres_manager_stream_url: str | None = None
    highres_retention_seconds: float | None = None
    cluster_nodes: Path | None = None
    cluster_no_sync: bool = False
    wait_timeout: float = 10.0

    @property
    def manager_pid_file(self) -> Path:
        return self.run_dir / "constella.pid"

    @property
    def local_agent_pid_file(self) -> Path:
        return self.run_dir / "local-agent.pid"

    @property
    def highres_pid_file(self) -> Path:
        return self.run_dir / "highres-sidecar.pid"

    @property
    def local_agent_state_file(self) -> Path:
        return self.run_dir / "local-agent-state.json"

    @property
    def manager_log_file(self) -> Path:
        return self.log_dir / "constella.log"

    @property
    def local_agent_log_file(self) -> Path:
        return self.log_dir / "local-agent.log"

    @property
    def highres_log_file(self) -> Path:
        return self.log_dir / "highres-sidecar.log"

    def resolved_agent_token_file(self) -> Path:
        return self.agent_token_file or self.run_dir / "agent-token"

    def resolved_highres_token_file(self) -> Path:
        return self.highres_token_file or self.run_dir / "highres-token"

    def resolved_local_agent_manager_url(self) -> str:
        return self.local_agent_manager_url or f"ws://127.0.0.1:{self.port}/api/agents/ws"

    def resolved_highres_manager_stream_url(self) -> str:
        return self.highres_manager_stream_url or f"ws://127.0.0.1:{self.port}/api/highres/stream"


@dataclass(slots=True)
class ServiceProcess:
    label: str
    pid_file: Path
    log_file: Path
    command: list[str]


@dataclass(slots=True)
class ServiceResult:
    label: str
    action: str
    ok: bool
    message: str


def start_service(
    config: ServiceConfig,
    *,
    popen_factory: PopenFactory = subprocess.Popen,
) -> list[ServiceResult]:
    validate_start_config(config)
    config.run_dir.mkdir(parents=True, exist_ok=True)
    config.log_dir.mkdir(parents=True, exist_ok=True)

    if config.local_agent or config.cluster_nodes is not None:
        ensure_private_token(config.resolved_agent_token_file())
    if config.highres_sidecar:
        ensure_private_token(config.resolved_highres_token_file())

    env = service_environment(config)
    results: list[ServiceResult] = []
    manager = manager_process(config)
    results.append(start_process(manager, env=env, popen_factory=popen_factory))
    if config.wait_timeout > 0:
        wait_for_manager(config)

    if config.highres_sidecar:
        results.append(start_process(highres_process(config), env=env, popen_factory=popen_factory))
    if config.local_agent:
        results.append(start_process(local_agent_process(config), env=env, popen_factory=popen_factory))
    if config.cluster_nodes is not None:
        results.extend(start_cluster(config))
    return results


def stop_service(config: ServiceConfig, *, stop_cluster: bool = False) -> list[ServiceResult]:
    results: list[ServiceResult] = []
    if stop_cluster and config.cluster_nodes is not None:
        results.extend(stop_cluster_agents(config))
    results.append(stop_process("local agent", config.local_agent_pid_file))
    results.append(stop_process("highres sidecar", config.highres_pid_file))
    results.append(stop_process("manager", config.manager_pid_file))
    return results


def status_service(config: ServiceConfig, *, include_cluster: bool = False) -> list[ServiceResult]:
    results = [
        process_status("manager", config.manager_pid_file),
        process_status("highres sidecar", config.highres_pid_file),
        process_status("local agent", config.local_agent_pid_file),
    ]
    if include_cluster and config.cluster_nodes is not None:
        results.extend(status_cluster(config))
    return results


def validate_start_config(config: ServiceConfig) -> None:
    if config.highres_sidecar and config.db_path is None:
        raise ValueError("service start --highres-sidecar requires the default database or --db-path")
    validate_device_type(config.local_agent_device)


def command_prefix() -> list[str]:
    return [sys.executable, "-m", "constella.cli"]


def manager_process(config: ServiceConfig) -> ServiceProcess:
    command = [
        *command_prefix(),
        "serve",
        "--host",
        config.host,
        "--port",
        str(config.port),
        "--refresh",
        str(config.refresh),
        "--process-refresh",
        str(config.process_refresh),
        "--graceful-timeout",
        str(config.graceful_timeout),
        "--log-level",
        config.log_level,
    ]
    if config.local_agent or config.cluster_nodes is not None:
        command.extend(["--agent-token-file", str(config.resolved_agent_token_file())])
    if config.highres_sidecar:
        command.extend(["--highres-token-file", str(config.resolved_highres_token_file())])
    if config.db_path is not None:
        command.extend(
            [
                "--db-path",
                str(config.db_path),
                "--db-queue-size",
                str(config.db_queue_size),
                "--raw-snapshot-seconds",
                str(config.raw_snapshot_seconds),
            ]
        )
    if config.frontend_dir is not None:
        command.extend(["--frontend-dir", str(config.frontend_dir)])
    return ServiceProcess("manager", config.manager_pid_file, config.manager_log_file, command)


def local_agent_process(config: ServiceConfig) -> ServiceProcess:
    node_id = config.local_agent_node_id or config.manager_hostname
    command = [
        *command_prefix(),
        "agent",
        "--manager-url",
        config.resolved_local_agent_manager_url(),
        "--token-file",
        str(config.resolved_agent_token_file()),
        "--refresh",
        str(config.refresh),
        "--process-refresh",
        str(config.process_refresh),
        "--device",
        config.local_agent_device,
        "--state-file",
        str(config.local_agent_state_file),
    ]
    if node_id:
        command.extend(["--node-id", node_id])
    return ServiceProcess(
        "local agent",
        config.local_agent_pid_file,
        config.local_agent_log_file,
        command,
    )


def highres_process(config: ServiceConfig) -> ServiceProcess:
    assert config.db_path is not None
    command = [
        *command_prefix(),
        "highres-sidecar",
        "--host",
        config.highres_host,
        "--port",
        str(config.highres_port),
        "--db-path",
        str(config.db_path),
        "--manager-stream-url",
        config.resolved_highres_manager_stream_url(),
        "--token-file",
        str(config.resolved_highres_token_file()),
        "--graceful-timeout",
        str(config.graceful_timeout),
        "--log-level",
        config.log_level,
    ]
    if config.highres_retention_seconds is not None:
        command.extend(["--retention-seconds", str(config.highres_retention_seconds)])
    return ServiceProcess(
        "highres sidecar",
        config.highres_pid_file,
        config.highres_log_file,
        command,
    )


def start_process(
    process: ServiceProcess,
    *,
    env: dict[str, str],
    popen_factory: PopenFactory,
) -> ServiceResult:
    pid = read_live_pid(process.pid_file)
    if pid is not None:
        return ServiceResult(
            process.label,
            "start",
            True,
            f"already running: pid={pid} log={process.log_file}",
        )
    process.pid_file.parent.mkdir(parents=True, exist_ok=True)
    process.log_file.parent.mkdir(parents=True, exist_ok=True)
    with process.log_file.open("ab") as log_file:
        child = popen_factory(
            process.command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    process.pid_file.write_text(f"{child.pid}\n", encoding="utf-8")
    return ServiceResult(
        process.label,
        "start",
        True,
        f"started: pid={child.pid} log={process.log_file}",
    )


def stop_process(label: str, pid_file: Path, *, timeout: float = 6.0) -> ServiceResult:
    if not pid_file.exists():
        return ServiceResult(label, "stop", True, "not running")
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        pid_file.unlink(missing_ok=True)
        return ServiceResult(label, "stop", True, "stale pid file removed")

    if not pid_running(pid):
        pid_file.unlink(missing_ok=True)
        return ServiceResult(label, "stop", True, f"stale pid removed: pid={pid}")

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_running(pid):
            pid_file.unlink(missing_ok=True)
            return ServiceResult(label, "stop", True, f"stopped: pid={pid}")
        time.sleep(0.2)
    return ServiceResult(label, "stop", False, f"still stopping: pid={pid}")


def process_status(label: str, pid_file: Path) -> ServiceResult:
    pid = read_live_pid(pid_file)
    if pid is not None:
        return ServiceResult(label, "status", True, f"running: pid={pid}")
    if pid_file.exists():
        return ServiceResult(label, "status", False, "stale pid file")
    return ServiceResult(label, "status", True, "not running")


def read_live_pid(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    return pid if pid_running(pid) else None


def pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def ensure_private_token(path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(token + "\n", encoding="utf-8")
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)


def service_environment(config: ServiceConfig) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("NO_PROXY", "127.0.0.1,localhost")
    env.setdefault("no_proxy", "127.0.0.1,localhost")
    if config.manager_hostname:
        env["CONSTELLA_MANAGER_HOSTNAME"] = config.manager_hostname
    return env


def wait_for_manager(config: ServiceConfig) -> None:
    host = "127.0.0.1" if config.host in {"0.0.0.0", "::"} else config.host
    url = f"http://{host}:{config.port}/api/health"
    deadline = time.monotonic() + config.wait_timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("ok") is True:
                return
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"manager did not become healthy at {url}: {last_error}")


def start_cluster(config: ServiceConfig) -> list[ServiceResult]:
    assert config.cluster_nodes is not None
    results = _cluster_controller(config).start_all()
    return [
        ServiceResult(result.node_id, "cluster start", result.ok, result.output or result.error or "")
        for result in results
    ]


def stop_cluster_agents(config: ServiceConfig) -> list[ServiceResult]:
    assert config.cluster_nodes is not None
    results = _cluster_controller(config).stop_all()
    return [
        ServiceResult(result.node_id, "cluster stop", result.ok, result.output or result.error or "")
        for result in results
    ]


def status_cluster(config: ServiceConfig) -> list[ServiceResult]:
    assert config.cluster_nodes is not None
    results = _cluster_controller(config).status_all()
    return [
        ServiceResult(result.node_id, "cluster status", result.ok, result.output or result.error or "")
        for result in results
    ]


def _cluster_controller(config: ServiceConfig) -> ClusterController:
    assert config.cluster_nodes is not None
    cluster_config = load_cluster_config(config.cluster_nodes)
    return ClusterController(
        cluster_config,
        project_root=default_project_root(),
        build_root=default_build_root(),
        sync_source=not config.cluster_no_sync,
    )


def format_service_results(results: Sequence[ServiceResult]) -> str:
    lines = []
    for result in results:
        state = "ok" if result.ok else "failed"
        lines.append(f"{result.label}\t{result.action}\t{state}\t{result.message}".rstrip())
    return "\n".join(lines)
