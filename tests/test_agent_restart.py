from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest
import websockets

from constella import agent


class RestartSocket:
    def __init__(self, message):
        self.messages = [json.dumps(message)]
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop()

    async def send(self, data):
        self.sent.append(json.loads(data))


@pytest.mark.parametrize("node,request_id,accepted", [
    ("local", "request-1", True), ("other", "request-1", False), ("local", "", False),
])
def test_restart_acknowledges_before_unwinding(node, request_id, accepted):
    async def run():
        socket = RestartSocket({"type": "agent_restart_request", "node_id": node,
                                "request_id": request_id})
        config = agent.AgentConfig(node_id="local", manager_url="ws://preview", token="preview")
        receiver = agent._receiver_loop(socket, None, agent.SupportedMetricsDelta(),
                                        config, asyncio.Lock())
        if accepted:
            with pytest.raises(agent.AgentRestartRequested):
                await receiver
        else:
            await receiver
        assert socket.sent == [{"type": "agent_restart_response", "node_id": "local",
                                "request_id": request_id, "ok": accepted}]
    asyncio.run(run())


@pytest.mark.parametrize("entry", ["module", "console"])
def test_agent_reexecutes_and_reconnects_with_same_pid(tmp_path, entry):
    # Patch only the subprocess's sampler: no GPU driver or production agent is used.
    (tmp_path / "sitecustomize.py").write_text('''
import os
import time
from pathlib import Path
import constella.agent as agent
from constella.schema import Snapshot
class PreviewCollector(agent.SnapshotCollector):
    def _sample_once(self):
        return Snapshot(ok=True, source="preview", hostname="preview", timestamp=time.time(),
                        elapsed_ms=0, gpus=[])
    def close(self):
        with Path(os.environ["PREVIEW_CLEANUP_MARKER"]).open("a") as f:
            f.write("closed\\n")
        super().close()
agent.SnapshotCollector = PreviewCollector
agent.sample_nvml_hardware_inventory = lambda: None
''')

    async def run():
        connected = asyncio.get_running_loop().create_future()
        instances = []
        acknowledged = []

        async def manager(ws):
            hello = json.loads(await ws.recv())
            instances.append(hello["capabilities"]["agent_instance_id"])
            if len(instances) == 2:
                connected.set_result(True)
                await ws.wait_closed()
                return
            await ws.send(json.dumps({"type": "agent_restart_request", "node_id": "preview",
                                      "request_id": "restart-1"}))
            async for raw in ws:
                message = json.loads(raw)
                if message["type"] == "agent_restart_response":
                    acknowledged.append(message["ok"])

        async with websockets.serve(manager, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            env = {k: v for k, v in os.environ.items() if not k.startswith("CONSTELLA_")}
            env.update({"CONSTELLA_MANAGER_URL": f"ws://127.0.0.1:{port}",
                        "CONSTELLA_AGENT_TOKEN": "preview", "CONSTELLA_NODE_ID": "preview",
                        "CONSTELLA_AGENT_STATE_FILE": str(tmp_path / "state.json"),
                        "PYTHONPATH": os.pathsep.join([str(tmp_path), *sys.path]),
                        "PREVIEW_CLEANUP_MARKER": str(tmp_path / "cleanup")})
            command = [sys.executable]
            if entry == "module":
                command += ["-m", "constella.agent_main"]
            else:
                import shutil
                command += [shutil.which("constella"), "agent"]
            with (tmp_path / "agent.log").open("w") as log:
                process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
                try:
                    await asyncio.wait_for(connected, timeout=8)
                    assert acknowledged == [True]
                    assert len(set(instances)) == 2
                    assert process.poll() is None
                    assert Path(f"/proc/{process.pid}/cmdline").read_text().split("\0")[:-1] == command
                    assert (tmp_path / "cleanup").read_text().splitlines() == ["closed"]
                    process.send_signal(signal.SIGINT)
                    await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=4)
                    assert (tmp_path / "cleanup").read_text().splitlines() == ["closed", "closed"]
                finally:
                    if process.poll() is None:
                        assert Path(f"/proc/{process.pid}/cmdline").read_text().split("\0")[:-1] == command
                        process.kill()
                        process.wait(timeout=3)

    asyncio.run(run())


def test_restart_unwinds_collector_and_loop_before_exec(tmp_path, monkeypatch):
    events = []

    class Collector:
        async def start(self):
            events.append("started")

        async def stop(self):
            events.append("stopped")

    async def restart(*args):
        raise agent.AgentRestartRequested()

    def execv(executable, args):
        events.append("exec")
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        assert executable == sys.executable
        assert args == [sys.executable, "-m", "constella.agent_main"]
        assert json.loads(config.state_file.read_text())["status"] == "stopped"

    monkeypatch.setattr(agent, "SnapshotCollector", lambda **kwargs: Collector())
    monkeypatch.setattr(agent, "sample_nvml_hardware_inventory", lambda: None)
    monkeypatch.setattr(agent, "_connection_loop", restart)
    monkeypatch.setattr(agent.os, "execv", execv)
    monkeypatch.setattr(sys, "orig_argv", ["python3", "-m", "constella.agent_main"])
    config = agent.AgentConfig(node_id="local", manager_url="ws://preview", token="preview",
                               state_file=tmp_path / "agent-state.json")
    agent.run_agent_service(config)
    assert events == ["started", "stopped", "exec"]


def test_collector_stop_waits_for_sample_before_closing_handles(monkeypatch):
    import threading

    from constella.collector import SnapshotCollector

    async def run():
        started = threading.Event()
        release = threading.Event()
        events = []
        collector = SnapshotCollector()

        def sample():
            started.set()
            assert release.wait(timeout=2)
            events.append("sample finished")

        monkeypatch.setattr(collector, "_sample_once", sample)
        monkeypatch.setattr(collector, "close", lambda: events.append("handles closed"))
        await collector.start()
        assert await asyncio.to_thread(started.wait, 2)
        stop = asyncio.create_task(collector.stop())
        await asyncio.sleep(0.01)
        assert not stop.done()
        assert not events
        release.set()
        await asyncio.wait_for(stop, timeout=2)
        assert events == ["sample finished", "handles closed"]

    asyncio.run(run())


def test_acknowledged_restart_wins_over_concurrent_sender_failure(monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def connect(*args, **kwargs):
        yield object()

    async def send(*args):
        pass

    async def receiver(*args):
        raise agent.AgentRestartRequested()

    async def sender(*args):
        raise OSError("concurrent send failure")

    monkeypatch.setattr(agent.websockets, "connect", connect)
    monkeypatch.setattr(agent, "_send_json", send)
    monkeypatch.setattr(agent, "_receiver_loop", receiver)
    monkeypatch.setattr(agent, "_sender_loop", sender)
    config = agent.AgentConfig(node_id="preview", manager_url="ws://preview", token="preview")
    with pytest.raises(agent.AgentRestartRequested):
        asyncio.run(agent._run_connection(config, None, agent.AgentStatus("preview", 1), None))
