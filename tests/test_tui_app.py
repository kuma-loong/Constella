from __future__ import annotations

import asyncio
from copy import deepcopy

from textual.widgets import ContentSwitcher, DataTable, ListView, Static

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
    http_url = "http://test"

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


class AnalyticsClient(FakeClient):
    async def get_json(self, path: str, *, params=None, timeout=10.0):
        if path == "/api/analytics/overview":
            return {
                "enabled": True,
                "user_gpu_hours": [
                    {
                        "user": "alice",
                        "gpu_hours": 12.5,
                        "weighted_gpu_hours": 11.8,
                        "job_count": 3,
                        "task_count": 4,
                        "last_seen_at": 1000,
                    }
                ],
                "job_rankings": [
                    {
                        "task_name": "train.py",
                        "user": "alice",
                        "node_id": "gpu-a",
                        "gpu_hours": 12.5,
                        "gpu_count": 1,
                        "duration_seconds": 7200,
                        "status": "running",
                    }
                ],
                "anomalies": [],
            }
        if path.startswith("/api/analytics/node/"):
            return {
                "enabled": True,
                "node_id": "gpu-a",
                "series": [
                    {
                        "gpu_uuid": "GPU-0",
                        "gpu_index": 0,
                        "points": [
                            {"avg_gpu_utilization": 10, "avg_memory_used_mb": 1024},
                            {"avg_gpu_utilization": 70, "avg_memory_used_mb": 2048},
                        ],
                    }
                ],
                "heatmap": [
                    {
                        "gpu_uuid": "GPU-0",
                        "gpu_index": 0,
                        "buckets": [
                            {"avg_gpu_utilization": 10, "sample_count": 2},
                            {"avg_gpu_utilization": 90, "sample_count": 2},
                        ],
                    }
                ],
            }
        raise AssertionError(path)


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


async def exercise_gpu_selection_persistence() -> None:
    snapshot = deepcopy(SNAPSHOT)
    snapshot["nodes"][0]["gpus"][0]["uuid"] = "GPU-0"
    second = deepcopy(snapshot["nodes"][0]["gpus"][0])
    second.update(index=1, uuid="GPU-1", utilization_gpu=91)
    snapshot["nodes"][0]["gpus"].append(second)

    app = ConstellaTui("http://127.0.0.1:8765")
    app.client = FakeClient()  # type: ignore[assignment]
    app.client.snapshots = lambda: _single_snapshot(snapshot)  # type: ignore[method-assign]
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)
        table = app.query_one("#gpus", DataTable)
        table.move_cursor(row=1)
        await pilot.pause(0.1)
        assert app.selected_gpu_key == "GPU-1"

        refreshed = deepcopy(snapshot)
        refreshed["nodes"][0]["gpus"][0]["utilization_gpu"] = 35
        app.snapshot = refreshed
        app._render_snapshot()
        await pilot.pause(0.1)

        assert app.selected_gpu_key == "GPU-1"
        assert table.cursor_row == 1
        assert table.get_cell("GPU-1", "gpu").plain.startswith("▸")

        await pilot.click("#gpus", offset=(4, 1))
        await pilot.pause()
        assert app.selected_gpu_key == "GPU-0"
        assert table.cursor_row == 0
        assert table.get_cell("GPU-0", "gpu").plain.startswith("▸")
        assert not table.get_cell("GPU-1", "gpu").plain.startswith("▸")

        await pilot.press("down")
        await pilot.pause()
        assert app.selected_gpu_key == "GPU-1"
        assert table.get_cell("GPU-1", "gpu").plain.startswith("▸")


async def _single_snapshot(snapshot):
    yield snapshot
    await asyncio.sleep(60)


def test_tui_preserves_selected_gpu_across_refresh() -> None:
    asyncio.run(exercise_gpu_selection_persistence())


async def exercise_node_arrow_selection_persistence() -> None:
    snapshot = deepcopy(SNAPSHOT)
    second_node = deepcopy(snapshot["nodes"][0])
    second_node.update(node_id="gpu-b", hostname="gpu-b")
    snapshot["nodes"].append(second_node)

    app = ConstellaTui("http://127.0.0.1:8765")
    app.client = FakeClient()  # type: ignore[assignment]
    app.client.snapshots = lambda: _single_snapshot(snapshot)  # type: ignore[method-assign]
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)
        node_list = app.query_one("#nodes", ListView)
        node_list.focus()
        await pilot.press("down")
        await pilot.pause()

        assert node_list.index == 1
        assert app.selected_node_id == "gpu-b"

        for utilization in (35, 66, 91):
            refreshed = deepcopy(snapshot)
            refreshed["nodes"][1]["gpus"][0]["utilization_gpu"] = utilization
            app.snapshot = refreshed
            app._render_snapshot()
            await pilot.pause()
            assert node_list.index == 1
            assert app.selected_node_id == "gpu-b"


def test_tui_preserves_arrow_selected_node_across_refresh() -> None:
    asyncio.run(exercise_node_arrow_selection_persistence())


async def exercise_realtime_metric_slots_remain_fixed() -> None:
    snapshot = deepcopy(SNAPSHOT)
    snapshot["seq"] = 9
    snapshot["nodes"][0]["totals"].update(
        accelerator_count=9,
        avg_gpu_utilization=9,
        memory_used_mb=9 * 1024,
        active_processes=9,
    )
    snapshot["totals"].update(
        online_node_count=9,
        node_count=9,
        accelerator_count=9,
        avg_gpu_utilization=9,
        memory_used_mb=9 * 1024,
    )

    app = ConstellaTui("http://127.0.0.1:8765")
    app.client = FakeClient()  # type: ignore[assignment]
    app.client.snapshots = lambda: _single_snapshot(snapshot)  # type: ignore[method-assign]
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)
        context_before = app.query_one("#context-bar", Static).content.plain
        summary_before = app.query_one("#node-summary", Static).content.plain

        refreshed = deepcopy(snapshot)
        refreshed["seq"] = 10
        refreshed["nodes"][0]["totals"].update(
            accelerator_count=10,
            avg_gpu_utilization=10,
            memory_used_mb=10 * 1024,
            active_processes=10,
        )
        refreshed["totals"].update(
            online_node_count=10,
            node_count=10,
            accelerator_count=10,
            avg_gpu_utilization=10,
            memory_used_mb=10 * 1024,
        )
        app.snapshot = refreshed
        app._render_snapshot()
        await pilot.pause()

        context_after = app.query_one("#context-bar", Static).content.plain
        summary_after = app.query_one("#node-summary", Static).content.plain
        assert _separator_positions(context_before) == _separator_positions(context_after)
        assert _separator_positions(summary_before) == _separator_positions(summary_after)


def _separator_positions(value: str) -> list[int]:
    return [index for index, character in enumerate(value) if character == "·"]


def test_tui_reserves_space_for_growing_realtime_metrics() -> None:
    asyncio.run(exercise_realtime_metric_slots_remain_fixed())


async def exercise_multi_view_navigation() -> None:
    snapshot = deepcopy(SNAPSHOT)
    snapshot["nodes"][0]["gpus"][0]["uuid"] = "GPU-0"
    app = ConstellaTui("http://127.0.0.1:8765")
    client = AnalyticsClient()
    client.snapshots = lambda: _single_snapshot(snapshot)  # type: ignore[method-assign]
    app.client = client  # type: ignore[assignment]
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)

        await pilot.press("2")
        await pilot.pause()
        assert app.query_one("#views", ContentSwitcher).current == "cluster-view"
        assert app.query_one("#cluster-nodes", DataTable).row_count == 1

        await pilot.press("3")
        await pilot.pause(0.2)
        assert app.query_one("#user-rankings", DataTable).row_count == 1
        assert app.query_one("#job-rankings", DataTable).row_count == 1

        await pilot.press("4")
        await pilot.pause(0.2)
        assert "GPU 0" in str(app.query_one("#history-heatmap", Static).content)
        history_status = str(app.query_one("#history-status", Static).content)
        assert "N[/] next node" in history_status
        assert "G[/] next GPU" in history_status
        assert "time range" in history_status


def test_tui_supports_cluster_rankings_and_history_views() -> None:
    asyncio.run(exercise_multi_view_navigation())


async def exercise_compact_terminal_and_cycle_actions() -> None:
    snapshot = deepcopy(SNAPSHOT)
    snapshot["nodes"][0]["gpus"][0]["uuid"] = "GPU-0"
    second_gpu = deepcopy(snapshot["nodes"][0]["gpus"][0])
    second_gpu.update(index=1, uuid="GPU-1")
    snapshot["nodes"][0]["gpus"].append(second_gpu)
    second_node = deepcopy(snapshot["nodes"][0])
    second_node.update(node_id="gpu-b", hostname="gpu-b")
    snapshot["nodes"].append(second_node)

    app = ConstellaTui("http://127.0.0.1:8765")
    client = AnalyticsClient()
    client.snapshots = lambda: _single_snapshot(snapshot)  # type: ignore[method-assign]
    app.client = client  # type: ignore[assignment]
    async with app.run_test(size=(90, 28)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("g")
        assert app.selected_gpu_key == "GPU-1"
        await pilot.press("n")
        assert app.selected_node_id == "gpu-b"
        await pilot.press("4")
        await pilot.pause(0.2)
        assert app.query_one("#views", ContentSwitcher).current == "history-view"


def test_tui_remains_usable_in_compact_terminal() -> None:
    asyncio.run(exercise_compact_terminal_and_cycle_actions())
