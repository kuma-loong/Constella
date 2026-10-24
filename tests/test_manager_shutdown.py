from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import time

import httpx
import pytest
import websockets


@pytest.mark.parametrize("edition", ["core", "lab"])
def test_sigterm_with_idle_stream_drains_database_and_completes_lifespan(tmp_path, edition):
    code = '''
import sys
from pathlib import Path
import uvicorn
from constella.app import create_app
from constella.db import AsyncDBSink, SQLiteSinkConfig
from constella.schema import GpuInfo, NodeSnapshot
root = Path(sys.argv[2])
sink = AsyncDBSink(SQLiteSinkConfig(path=root / "telemetry.sqlite3"))
if sys.argv[3] == "lab":
    from constella_lab.app import create_lab_app
    from constella_lab.config import LabConfig
    config = LabConfig(db_path=root / "identity.sqlite3",
        access_team_domain="https://test.cloudflareaccess.com", access_audience="preview",
        bootstrap_admin_email="admin@example.com", public_origin="https://preview.example.com")
    app = create_lab_app(config=config, db_sink=sink, agent_token="preview")
else:
    app = create_app(db_sink=sink, agent_token="preview")
original = app.router.lifespan_context
from contextlib import asynccontextmanager
@asynccontextmanager
async def lifespan(app):
    async with original(app):
        yield
        # Queue a write just before the core lifespan drains and closes SQLite.
        sink.submit_node_snapshot(NodeSnapshot(node_id="drained", hostname="preview",
            seq=1, sampled_at=1, received_at=1, refresh_interval=1, process_interval=5,
            status="online", source="test", gpus=[GpuInfo(index=0, uuid="preview-gpu")]))
    assert sink.store.connection is None
    (root / "shutdown-complete").write_text("closed")
app.router.lifespan_context = lifespan
# No timeout: prove the idle ASGI task exits rather than being forcibly cancelled.
uvicorn.run(app, fd=int(sys.argv[1]), lifespan="on")
'''
    async def run():
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        env = {k: v for k, v in os.environ.items() if not k.startswith("CONSTELLA_")}
        env["PYTHONPATH"] = os.pathsep.join(sys.path)
        command = [sys.executable, "-c", code, str(listener.fileno()), str(tmp_path), edition]
        with (tmp_path / "server.log").open("w") as log:
            process = subprocess.Popen(command, env=env, pass_fds=(listener.fileno(),),
                                       stdout=log, stderr=log)
            listener.close()
            try:
                async with httpx.AsyncClient(timeout=0.25) as client:
                    for _ in range(100):
                        try:
                            if (await client.get(f"http://127.0.0.1:{port}/api/health")).is_success:
                                break
                        except httpx.TransportError:
                            pass
                        assert process.poll() is None, (tmp_path / "server.log").read_text()
                        await asyncio.sleep(0.05)
                    else:
                        pytest.fail("isolated manager did not start")
                async with websockets.connect(f"ws://127.0.0.1:{port}/api/highres/stream") as ws:
                    assert '"type":"hello"' in await ws.recv()
                    assert Path(f"/proc/{process.pid}/cmdline").read_text().split("\0")[:-1] == command
                    started = time.monotonic()
                    process.send_signal(signal.SIGTERM)
                    await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=4)
                    assert time.monotonic() - started < 4
                assert (tmp_path / "shutdown-complete").read_text() == "closed"
                contents = (tmp_path / "server.log").read_text()
                assert "Application shutdown complete" in contents
                assert "Cancel" not in contents and "ERROR" not in contents
                with sqlite3.connect(f"file:{tmp_path / 'telemetry.sqlite3'}?mode=ro", uri=True) as db:
                    assert db.execute("SELECT node_id FROM nodes").fetchone()[0] == "drained"
            finally:
                if process.poll() is None:
                    assert Path(f"/proc/{process.pid}/cmdline").read_text().split("\0")[:-1] == command
                    process.kill()
                    process.wait(timeout=3)

    asyncio.run(run())
