from __future__ import annotations

import argparse
import os
import stat

from constella.cli import service_config_from_args
from constella.service import ServiceConfig, start_service


class FakeProcess:
    def __init__(self, pid: int):
        self.pid = pid


class FakePopen:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.next_pid = 2000

    def __call__(self, command: list[str], **kwargs: object) -> FakeProcess:
        self.commands.append(command)
        self.envs.append(dict(kwargs["env"]))  # type: ignore[arg-type]
        self.next_pid += 1
        return FakeProcess(self.next_pid)


def test_service_config_defaults_to_sqlite_database(tmp_path) -> None:
    run_dir = tmp_path / "run"
    args = argparse.Namespace(
        service_command="start",
        host="127.0.0.1",
        port=18875,
        refresh=1.0,
        process_refresh=5.0,
        log_level="info",
        run_dir=run_dir,
        log_dir=tmp_path / "logs",
        no_local_agent=False,
        local_agent_node_id=None,
        local_agent_manager_url=None,
        manager_hostname=None,
        agent_token_file=None,
        db_path=None,
        no_db=False,
        db_queue_size=1024,
        raw_snapshot_seconds=0.0,
        frontend_dir=None,
        highres_sidecar=False,
        highres_host="127.0.0.1",
        highres_port=18876,
        highres_token_file=None,
        highres_manager_stream_url=None,
        highres_retention_seconds=None,
        cluster_nodes=None,
        cluster_no_sync=False,
        wait_timeout=10.0,
    )

    config = service_config_from_args(args)

    assert config.db_path == run_dir / "constella.db"


def test_service_config_can_disable_default_database(tmp_path) -> None:
    args = argparse.Namespace(
        service_command="start",
        host="127.0.0.1",
        port=18875,
        refresh=1.0,
        process_refresh=5.0,
        log_level="info",
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        no_local_agent=False,
        local_agent_node_id=None,
        local_agent_manager_url=None,
        manager_hostname=None,
        agent_token_file=None,
        db_path=None,
        no_db=True,
        db_queue_size=1024,
        raw_snapshot_seconds=0.0,
        frontend_dir=None,
        highres_sidecar=False,
        highres_host="127.0.0.1",
        highres_port=18876,
        highres_token_file=None,
        highres_manager_stream_url=None,
        highres_retention_seconds=None,
        cluster_nodes=None,
        cluster_no_sync=False,
        wait_timeout=10.0,
    )

    config = service_config_from_args(args)

    assert config.db_path is None


def test_start_service_spawns_manager_and_local_agent_with_database(tmp_path) -> None:
    run_dir = tmp_path / "run"
    log_dir = tmp_path / "logs"
    config = ServiceConfig(
        host="127.0.0.1",
        port=18875,
        graceful_timeout=4.0,
        run_dir=run_dir,
        log_dir=log_dir,
        db_path=run_dir / "constella.db",
        local_agent_node_id="node-a",
        local_agent_device="ascend",
        wait_timeout=0,
    )
    fake_popen = FakePopen()

    results = start_service(config, popen_factory=fake_popen)

    assert [result.label for result in results] == ["manager", "local agent"]
    assert (run_dir / "agent-token").exists()
    assert stat.S_IMODE((run_dir / "agent-token").stat().st_mode) == 0o600
    assert (run_dir / "constella.pid").read_text(encoding="utf-8").strip() == "2001"
    assert (run_dir / "local-agent.pid").read_text(encoding="utf-8").strip() == "2002"
    manager_command = fake_popen.commands[0]
    agent_command = fake_popen.commands[1]
    assert manager_command[:3] == [os.sys.executable, "-m", "constella.cli"]
    assert manager_command[3:5] == ["serve", "--host"]
    assert "--db-path" in manager_command
    assert str(run_dir / "constella.db") in manager_command
    assert "--agent-token-file" in manager_command
    assert str(run_dir / "agent-token") in manager_command
    assert manager_command[manager_command.index("--graceful-timeout") + 1] == "4.0"
    assert agent_command[:4] == [os.sys.executable, "-m", "constella.cli", "agent"]
    assert "ws://127.0.0.1:18875/api/agents/ws" in agent_command
    assert str(run_dir / "local-agent-state.json") in agent_command
    assert agent_command[agent_command.index("--device") + 1] == "ascend"
