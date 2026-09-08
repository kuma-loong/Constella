from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from constella.db import AsyncDBSink, SQLiteSinkConfig
from constella.history_reader import HistoryReader
from constella_lab.auth import LabAuthMiddleware


def test_maintenance_failure_backs_off_without_reporting_false_recovery(tmp_path, monkeypatch):
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "test.db"))
    calls = []
    now = [100.0]
    monkeypatch.setattr("constella.db.time.monotonic", lambda: now[0])

    def maintenance(*, now):
        calls.append(now)
        raise OSError("disk full")

    monkeypatch.setattr(sink, "_run_scheduled_maintenance", maintenance)
    sink._idle_maintenance()
    assert sink.status()["write_errors"] == 1
    for _ in range(20):
        sink._idle_maintenance()
    assert len(calls) == 1
    assert sink.status()["consecutive_errors"] == 1
    now[0] += 10
    sink._idle_maintenance()
    assert len(calls) == 2
    assert sink._maintenance_retry_at == 130
    now[0] = 130
    monkeypatch.setattr(sink, "_run_scheduled_maintenance", lambda **_: None)
    sink._idle_maintenance()
    assert sink.status()["consecutive_errors"] == 0


def test_slow_database_work_does_not_block_event_loop_and_cancellation_waits(tmp_path):
    async def exercise():
        sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "test.db"))
        started, release, finished = threading.Event(), threading.Event(), threading.Event()

        def slow_work():
            started.set()
            release.wait(3)
            finished.set()

        task = asyncio.create_task(sink._in_thread(slow_work))
        while not started.is_set():
            await asyncio.sleep(0.001)
        try:
            await asyncio.wait_for(asyncio.sleep(0.01), 0.5)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(exercise())


def test_history_reader_uses_readonly_connection_separate_from_writer(tmp_path):
    async def exercise():
        sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "test.db"))
        sink.store.open()
        writer = sink.store.connection
        reader = HistoryReader(sink.store.path)

        def query(store):
            assert store.connection is not writer
            assert store.connection.execute("PRAGMA query_only").fetchone()[0] == 1
            return store.query_users()

        try:
            assert await reader.query(query) == []
        finally:
            sink.store.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("expired, code", [(True, 4401), (False, 4000)])
def test_websocket_producer_stops_before_close_on_expiry_or_rotation(expired, code):
    async def exercise():
        events = []
        started = asyncio.Event()

        async def app(scope, receive, send):
            started.set()
            try:
                await asyncio.Future()
            finally:
                events.append("producer_stopped")

        middleware = LabAuthMiddleware(app, config=None, store=None, verifier=None)
        # Advance the wait deadline without a fifteen-minute wall-clock sleep.
        from unittest.mock import patch

        real_wait = asyncio.wait

        async def advance_wait(tasks, **kwargs):
            await started.wait()
            if expired:
                return await real_wait(tasks, timeout=0)
            return set(), set(tasks)

        clock = iter([0.0, 0.0, 901.0])
        async def send(message):
            events.append(message["code"])

        middleware.store = SimpleNamespace(user_with_bindings=lambda _: {"status": "active"})
        with patch("constella_lab.auth.time", SimpleNamespace(
            time=time.time, monotonic=lambda: next(clock),
        )), patch("constella_lab.auth.asyncio.wait", advance_wait):
            await middleware._run_websocket(
                {}, None, send, send, user_id="test",
                expires_at=time.time() + (0.001 if expired else 3600),
            )
        assert events == ["producer_stopped", code]

    asyncio.run(exercise())


def test_cluster_disconnect_during_send_interval_does_not_send_again(monkeypatch):
    from constella.app import create_app

    async def exercise():
        disconnected = asyncio.Event()
        sent = []
        real_sleep = asyncio.sleep

        class State:
            seq = 0

            def snapshot(self):
                self.seq += 1
                return SimpleNamespace(seq=self.seq, to_dict=lambda: {"seq": self.seq})

            async def wait_for_update(self, *args, **kwargs):
                return self.seq + 1

        class Socket:
            async def accept(self):
                pass

            async def receive(self):
                await disconnected.wait()
                return {"type": "websocket.disconnect"}

            async def send_json(self, data):
                assert not disconnected.is_set(), "sent after client disconnected"
                sent.append(data)

        async def disconnect_during_interval(_delay):
            disconnected.set()
            await real_sleep(0)

        app = create_app(cluster_state=State())
        endpoint = next(route.endpoint for route in app.routes if route.path == "/ws/cluster")
        monkeypatch.setattr("constella.app.asyncio.sleep", disconnect_during_interval)
        await endpoint(Socket())
        assert len(sent) == 1

    asyncio.run(exercise())
