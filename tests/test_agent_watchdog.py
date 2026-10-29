from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/service/agent_watchdog.py"
spec = importlib.util.spec_from_file_location("agent_watchdog", SCRIPT)
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


@pytest.fixture
def config(tmp_path):
    nodes = tmp_path / "nodes.yaml"
    nodes.write_text("nodes:\n  - id: A\n    host: host-a\n  - id: B\n    host: host-b\n")
    token = tmp_path / "token"
    token.write_text("test-token")
    return SimpleNamespace(nodes=nodes, token_file=token, manager_url="http://preview",
                           observe_seconds=0.1, ssh_timeout=1, dry_run=False)


def test_unhealthy_manager_does_not_observe_or_start(monkeypatch, config):
    observe, start = AsyncMock(), AsyncMock()
    monkeypatch.setattr(watchdog, "manager_healthy", lambda _: False)
    monkeypatch.setattr(watchdog, "observe_nodes", observe)
    monkeypatch.setattr(watchdog, "start_remote", start)
    assert asyncio.run(watchdog.check_once(config))["status"] == "manager_unhealthy"
    observe.assert_not_called()
    start.assert_not_called()


def test_only_missing_nodes_receive_one_start_attempt(monkeypatch, config):
    start = AsyncMock(return_value={"outcome": "started", "pid": 123})
    monkeypatch.setattr(watchdog, "manager_healthy", lambda _: True)
    monkeypatch.setattr(watchdog, "observe_nodes", AsyncMock(return_value={"A"}))
    monkeypatch.setattr(watchdog, "start_remote", start)
    result = asyncio.run(watchdog.check_once(config))
    assert result["nodes"]["A"] == {"outcome": "connected"}
    assert result["nodes"]["B"]["outcome"] == "started"
    assert start.await_count == 1
    assert start.await_args.args[0]["id"] == "B"


def test_failed_observation_never_starts_any_node(monkeypatch, config):
    start = AsyncMock()
    monkeypatch.setattr(watchdog, "manager_healthy", lambda _: True)
    monkeypatch.setattr(watchdog, "observe_nodes", AsyncMock(side_effect=OSError("unauthorized stream")))
    monkeypatch.setattr(watchdog, "start_remote", start)
    with pytest.raises(OSError, match="unauthorized"):
        asyncio.run(watchdog.check_once(config))
    start.assert_not_called()


def test_manager_must_still_be_healthy_before_recovery(monkeypatch, config):
    replies = iter([True, False])
    start = AsyncMock()
    monkeypatch.setattr(watchdog, "manager_healthy", lambda _: next(replies))
    monkeypatch.setattr(watchdog, "observe_nodes", AsyncMock(return_value=set()))
    monkeypatch.setattr(watchdog, "start_remote", start)
    assert asyncio.run(watchdog.check_once(config))["status"] == "manager_unhealthy"
    start.assert_not_called()


def test_dry_run_and_config_reload(monkeypatch, config):
    observe = AsyncMock(return_value=set())
    start = AsyncMock()
    monkeypatch.setattr(watchdog, "manager_healthy", lambda _: True)
    monkeypatch.setattr(watchdog, "observe_nodes", observe)
    monkeypatch.setattr(watchdog, "start_remote", start)
    config.dry_run = True
    assert set(asyncio.run(watchdog.check_once(config))["nodes"]) == {"A", "B"}
    config.nodes.write_text("nodes:\n  - id: C\n    host: host-c\n")
    assert asyncio.run(watchdog.check_once(config))["nodes"] == {"C": {"outcome": "would_start"}}
    start.assert_not_called()
    assert observe.await_args.args[2] == {"C"}


@pytest.fixture
def remote(tmp_path):
    base = tmp_path / "agent home"
    runtime = base / "agent/runtime"
    module = runtime / "constella"
    module.mkdir(parents=True)
    (base / "run").mkdir()
    (module / "__init__.py").write_text("")
    (module / "agent_main.py").write_text("import time\ntime.sleep(60)\n")
    starter = base / "agent/start_agent.sh"
    # Emulate the existing deployment's nohup launcher without GPU or network access.
    starter.write_text(f'''#!/bin/bash
cd "$(dirname "$0")/runtime"
nohup {sys.executable} -m constella.agent_main </dev/null >/dev/null 2>&1 &
echo $! > ../..//run/agent.pid
sleep 0.2
''')
    yield base, runtime
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.joinpath("cwd").resolve() == runtime and b"constella.agent_main" in proc.joinpath("cmdline").read_bytes():
                os.kill(int(proc.name), 15)
        except OSError:
            pass


def remote_check(base):
    result = subprocess.run([sys.executable, "-", str(base)], input=watchdog.REMOTE_START,
                            text=True, capture_output=True, timeout=5, check=True)
    return json.loads(result.stdout)


def test_remote_start_is_idempotent_even_without_pid_file(remote):
    base, _ = remote
    first = remote_check(base)
    assert first["outcome"] == "started"
    assert remote_check(base) == {"outcome": "already_running", "pid": first["pid"]}
    (base / "run/agent.pid").unlink()
    assert remote_check(base) == {"outcome": "already_running", "pid": first["pid"]}


def test_remote_refuses_a_reused_pid(remote):
    base, _ = remote
    (base / "run/agent.pid").write_text(str(os.getpid()))
    assert remote_check(base)["outcome"] == "refused"
    os.kill(os.getpid(), 0)


def test_remote_does_not_provision_missing_runtime(tmp_path):
    assert remote_check(tmp_path)["outcome"] == "not_provisioned"
    assert list(tmp_path.iterdir()) == []


def test_ssh_timeout_kills_only_its_child(monkeypatch):
    process = SimpleNamespace(returncode=None, communicate=AsyncMock(side_effect=asyncio.TimeoutError),
                              kill=lambda: killed.append(True), wait=AsyncMock())
    killed = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(watchdog.start_remote({"id": "A", "host": "test"}, "$HOME/.constella", .1))
    assert killed == [True]
    process.wait.assert_awaited_once()


def test_daemon_singleton_start_and_verified_stop(config, tmp_path):
    state = tmp_path / "state"
    args = ["--nodes", str(config.nodes), "--state-dir", str(state), "--token-file", str(config.token_file),
            "--log-file", str(tmp_path / "watchdog.log"), "--manager-url", "http://127.0.0.1:1", "--interval", "300"]
    def cli(command):
        return subprocess.run([sys.executable, str(SCRIPT), command, *args], capture_output=True, text=True, timeout=10)
    try:
        assert cli("start").returncode == 0
        pid = watchdog.process_pid(state)
        assert pid
        assert cli("start").returncode == 0
        assert watchdog.process_pid(state) == pid
        for _ in range(30):
            if (state / "agent-watchdog-state.json").exists():
                break
            time.sleep(.1)
        report = json.loads((state / "agent-watchdog-state.json").read_text())
        assert report["status"] == "probe_failed"
    finally:
        result = cli("stop")
    assert result.returncode == 0, result.stderr
    assert watchdog.process_pid(state) is None
    assert not (state / "agent-watchdog.pid").exists()


def test_local_stop_refuses_unrelated_process(tmp_path):
    (tmp_path / "agent-watchdog.pid").write_text(str(os.getpid()))
    with pytest.raises(RuntimeError, match="different process"):
        watchdog.stop_daemon(SimpleNamespace(state_dir=tmp_path))
    os.kill(os.getpid(), 0)
