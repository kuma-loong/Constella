from __future__ import annotations

import asyncio

from textual.widgets import DataTable, ListView, Static

from constella_tui.app import ConstellaTui
from constella_tui.client import ClusterConnectionError


SNAPSHOT = {
    "nodes": [
        {
            "node_id": "gpu-a",
            "hostname": "gpu-a",
            "status": "online",
            "totals": {"accelerator_count": 1},
            "gpus": [
                {
                    "index": 0,
                    "name": "NVIDIA A100",
                    "utilization_gpu": 72,
                    "memory_used_mb": 10240,
                    "memory_total_mb": 40960,
                    "temperature_c": 61,
                    "power_watts": 210,
                    "power_limit_watts": 400,
                    "processes": [
                        {
                            "pid": 42,
                            "user": "alice",
                            "task_name": "train.py",
                            "gpu_memory_mb": 9000,
                            "runtime_seconds": 93,
                            "cmdline": "python train.py",
                        }
                    ],
                }
            ],
        }
    ],
    "totals": {
        "node_count": 1,
        "online_node_count": 1,
        "accelerator_count": 1,
        "avg_gpu_utilization": 72,
        "avg_memory_utilization": 25,
        "memory_used_mb": 10240,
        "active_processes": 1,
    },
}


class FakeClient:
    websocket_url = "ws://test/ws/cluster"

    async def snapshots(self):
        yield SNAPSHOT
        await asyncio.sleep(60)


class EmptyClient:
    websocket_url = "ws://test/ws/cluster"

    async def snapshots(self):
        yield {"nodes": [], "totals": {"node_count": 0}}
        await asyncio.sleep(60)


class ErrorClient:
    websocket_url = "ws://test/ws/cluster"

    async def snapshots(self):
        raise ClusterConnectionError("connection refused")
        yield  # pragma: no cover


async def exercise_app() -> None:
    app = ConstellaTui("http://127.0.0.1:8765")
    app.client = FakeClient()  # type: ignore[assignment]
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)
        assert len(app.query_one("#nodes", ListView).children) == 1
        assert app.query_one("#gpus", DataTable).row_count == 1
        assert app.query_one("#processes", DataTable).row_count == 1
        assert app.query_one("#state-message", Static).display is False
        assert app.query_one("#connection-status", Static).has_class("live")

        await pilot.press("?")
        await pilot.pause()
        assert app.screen.id != "_default"
        await pilot.press("escape")


def test_tui_renders_live_snapshot_and_help() -> None:
    asyncio.run(exercise_app())


async def exercise_state(client, expected_text: str) -> None:
    app = ConstellaTui("http://127.0.0.1:8765", reconnect_delay=60)
    app.client = client
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause(0.2)
        message = app.query_one("#state-message", Static)
        assert message.display is True
        assert expected_text in str(message.content)


def test_tui_renders_empty_state() -> None:
    asyncio.run(exercise_state(EmptyClient(), "No nodes are reporting"))


def test_tui_renders_connection_error_state() -> None:
    asyncio.run(exercise_state(ErrorClient(), "Manager unavailable"))
