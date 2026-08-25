from __future__ import annotations

import asyncio
import colorsys
from copy import deepcopy
from itertools import combinations

import pytest
from textual.containers import Container, Vertical
from textual.widgets import ContentSwitcher, DataTable, ListView, Static

from constella_tui.app import (
    HISTORY_GPU_STYLES,
    LIVE_CHART_STYLE,
    ConstellaTui,
    build_parser,
)
from constella_tui.client import ClusterConnectionError
from constella_tui.performance import PERFORMANCE_METRICS


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


def test_tui_parser_reports_package_version(capsys) -> None:
    with pytest.raises(SystemExit, match="0"):
        build_parser().parse_args(["--version"])

    assert capsys.readouterr().out == "0.1.3rc1\n"


def test_history_uses_eight_visually_distinct_gpu_colors() -> None:
    assert len(HISTORY_GPU_STYLES) == 8
    assert len(set(HISTORY_GPU_STYLES)) == 8
    hues = []
    for color in HISTORY_GPU_STYLES:
        red, green, blue = (
            int(color[offset : offset + 2], 16) / 255 for offset in (1, 3, 5)
        )
        hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
        assert saturation >= 0.65
        assert value >= 0.9
        hues.append(hue * 360)
    for first, second in combinations(hues, 2):
        distance = abs(first - second)
        assert min(distance, 360 - distance) >= 35


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
                            {
                                "bucket_start": 1_700_000_000,
                                "avg_gpu_utilization": 10,
                                "avg_memory_used_mb": 1024,
                            },
                            {
                                "bucket_start": 1_700_003_600,
                                "avg_gpu_utilization": 70,
                                "avg_memory_used_mb": 2048,
                            },
                        ],
                    },
                    {
                        "gpu_uuid": "GPU-1",
                        "gpu_index": 1,
                        "points": [
                            {
                                "bucket_start": 1_700_000_000,
                                "avg_gpu_utilization": 80,
                                "avg_memory_used_mb": 4096,
                            },
                            {
                                "bucket_start": 1_700_003_600,
                                "avg_gpu_utilization": 30,
                                "avg_memory_used_mb": 3072,
                            },
                        ],
                    },
                ],
                "heatmap": [
                    {
                        "gpu_uuid": "GPU-0",
                        "gpu_index": 0,
                        "buckets": [
                            {
                                "bucket_start": 1_700_000_000,
                                "avg_gpu_utilization": 10,
                                "sample_count": 2,
                            },
                            {
                                "bucket_start": 1_700_003_600,
                                "avg_gpu_utilization": 90,
                                "sample_count": 2,
                            },
                        ],
                    },
                    {
                        "gpu_uuid": "GPU-1",
                        "gpu_index": 1,
                        "buckets": [
                            {
                                "bucket_start": 1_700_000_000,
                                "avg_gpu_utilization": 80,
                                "sample_count": 2,
                            },
                            {
                                "bucket_start": 1_700_003_600,
                                "avg_gpu_utilization": 30,
                                "sample_count": 2,
                            },
                        ],
                    },
                ],
            }
        raise AssertionError(path)


class PerformanceClient(FakeClient):
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.requests: list[tuple[str, dict[str, str]]] = []

    async def get_json(self, path: str, *, params=None, timeout=10.0):
        assert path == "/api/highres/performance"
        assert isinstance(params, dict)
        self.requests.append((path, params))
        if not self.enabled:
            return {
                "enabled": False,
                "profile": "nvidia.gpm.v1",
                "series": [],
            }
        metrics = str(params["metrics"]).split(",")
        gpu_uuid = str(params["gpu_uuid"])
        gpu_index = int(gpu_uuid.rsplit("-", 1)[-1])
        return {
            "enabled": True,
            "profile": "nvidia.gpm.v1",
            "series": [
                {
                    "node_id": params["node_id"],
                    "gpu_uuid": gpu_uuid,
                    "gpu_index": gpu_index,
                    "name": "NVIDIA Test",
                    "status": "available",
                    "metrics": {
                        metric: {
                            "points": [
                                [1_700_000_000, 10.0],
                                [1_700_000_001, None],
                                [1_700_000_002, 70.0],
                            ],
                            "summary": {
                                "avg": 40.0,
                                "min": 10.0,
                                "max": 70.0,
                                "p95": 70.0,
                                "coverage": 66.7,
                            },
                        }
                        for metric in metrics
                    },
                }
            ],
        }


async def exercise_app() -> None:
    app = ConstellaTui("http://127.0.0.1:8765")
    app.client = FakeClient()  # type: ignore[assignment]
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)
        assert len(app.query_one("#nodes", ListView).children) == 1
        assert app.query_one("#gpus", DataTable).row_count == 1
        assert app.query_one("#processes", DataTable).row_count == 1
        assert app.query_one("#processes", DataTable).cursor_type == "none"
        assert app.query_one("#node-hardware", DataTable).cursor_type == "none"
        assert app.query_one("#user-rankings", DataTable).cursor_type == "none"
        assert app.query_one("#job-rankings", DataTable).cursor_type == "none"
        assert app.query_one("#anomalies", DataTable).cursor_type == "none"
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
        mounted_rows = tuple(id(row) for row in table.rows.values())
        table.move_cursor(row=1)
        await pilot.pause(0.1)
        assert app.selected_gpu_key == "GPU-1"
        process_table = app.query_one("#processes", DataTable)
        mounted_process_rows = tuple(id(row) for row in process_table.rows.values())

        refreshed = deepcopy(snapshot)
        refreshed["nodes"][0]["gpus"][0]["utilization_gpu"] = 35
        app.snapshot = refreshed
        app._render_snapshot()
        await pilot.pause(0.1)

        assert app.selected_gpu_key == "GPU-1"
        assert table.cursor_row == 1
        assert tuple(id(row) for row in table.rows.values()) == mounted_rows
        assert tuple(id(row) for row in process_table.rows.values()) == mounted_process_rows
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


async def exercise_live_chart_uses_fixed_points_and_color() -> None:
    snapshot = deepcopy(SNAPSHOT)
    gpu = snapshot["nodes"][0]["gpus"][0]
    gpu.update(uuid="GPU-0", gpu_id="gpu-a:GPU-0")
    snapshot["nodes"][0]["history"] = {"gpu-a:GPU-0": {"gpu": list(range(121))}}

    app = ConstellaTui("http://127.0.0.1:8765")
    app.client = FakeClient()  # type: ignore[assignment]
    app.client.snapshots = lambda: _single_snapshot(snapshot)  # type: ignore[method-assign]
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)
        curve = app.query_one("#realtime-curve", Static)

        assert curve.content.style == LIVE_CHART_STYLE
        assert app.query_one("#realtime-pane").size.width <= 68
        assert all(len(line) <= 64 for line in curve.content.plain.splitlines())


def test_tui_live_chart_uses_fixed_points_and_color() -> None:
    asyncio.run(exercise_live_chart_uses_fixed_points_and_color())


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


async def exercise_cluster_keyboard_mouse_and_refresh_stability() -> None:
    snapshot = deepcopy(SNAPSHOT)
    second_node = deepcopy(snapshot["nodes"][0])
    second_node.update(node_id="gpu-b", hostname="gpu-b")
    snapshot["nodes"].append(second_node)

    app = ConstellaTui("http://127.0.0.1:8765")
    app.client = FakeClient()  # type: ignore[assignment]
    app.client.snapshots = lambda: _single_snapshot(snapshot)  # type: ignore[method-assign]
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("2")
        await pilot.pause()
        table = app.query_one("#cluster-nodes", DataTable)
        assert table.has_focus

        await pilot.press("down")
        await pilot.pause()
        assert app.selected_node_id == "gpu-b"
        assert table.cursor_row == 1
        assert table.get_cell("gpu-b", "node").plain.startswith("▸")
        mounted_rows = tuple(id(row) for row in table.rows.values())
        hardware_table = app.query_one("#node-hardware", DataTable)
        mounted_hardware_rows = tuple(id(row) for row in hardware_table.rows.values())

        for utilization in (35, 66, 91):
            refreshed = deepcopy(snapshot)
            refreshed["nodes"][1]["totals"]["avg_gpu_utilization"] = utilization
            app.snapshot = refreshed
            app._render_snapshot()
            await pilot.pause()
            assert app.selected_node_id == "gpu-b"
            assert table.cursor_row == 1
            assert table.get_cell("gpu-b", "node").plain.startswith("▸")
            assert tuple(id(row) for row in table.rows.values()) == mounted_rows
            assert (
                tuple(id(row) for row in hardware_table.rows.values())
                == mounted_hardware_rows
            )

        await pilot.click("#cluster-nodes", offset=(4, 1))
        await pilot.pause()
        assert app.selected_node_id == "gpu-a"
        assert table.cursor_row == 0
        assert table.get_cell("gpu-a", "node").plain.startswith("▸")
        assert not table.get_cell("gpu-b", "node").plain.startswith("▸")


def test_cluster_selection_is_stable_for_keyboard_mouse_and_refresh() -> None:
    asyncio.run(exercise_cluster_keyboard_mouse_and_refresh_stability())


async def exercise_multi_view_navigation() -> None:
    snapshot = deepcopy(SNAPSHOT)
    snapshot["nodes"][0]["gpus"][0]["uuid"] = "GPU-0"
    second_gpu = deepcopy(snapshot["nodes"][0]["gpus"][0])
    second_gpu.update(index=1, uuid="GPU-1")
    snapshot["nodes"][0]["gpus"].append(second_gpu)
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
        assert "GPU 1" in str(app.query_one("#history-heatmap", Static).content)
        gpu_title = app.query_one("#history-gpu-title", Static).content
        assert "ALL GPU UTILIZATION" in str(gpu_title)
        assert "▸0" in gpu_title.plain
        gpu_chart = app.query_one("#history-gpu-curve", Static).content
        chart_styles = [str(span.style) for span in gpu_chart.spans]
        assert "bold #4DEBFF" in chart_styles
        assert "#FFB84D" in chart_styles
        history_status = str(app.query_one("#history-status", Static).content)
        assert "N[/] next node" in history_status
        assert "2 GPU series" in history_status
        assert "G[/] highlight GPU" in history_status
        assert "time range" in history_status

        await pilot.press("g")
        await pilot.pause()
        assert "▸1" in app.query_one("#history-gpu-title", Static).content.plain


def test_tui_supports_cluster_rankings_and_history_views() -> None:
    asyncio.run(exercise_multi_view_navigation())


async def exercise_performance_view_and_controls() -> None:
    snapshot = deepcopy(SNAPSHOT)
    node = snapshot["nodes"][0]
    node["performance_profiles"] = ["nvidia.gpm.v1"]
    node["gpus"][0]["uuid"] = "GPU-0"
    node["gpus"][0]["performance"] = {
        "profile": "nvidia.gpm.v1",
        "status": "available",
        "supported_metrics": [metric.key for metric in PERFORMANCE_METRICS],
    }
    second_gpu = deepcopy(node["gpus"][0])
    second_gpu.update(index=1, uuid="GPU-1")
    node["gpus"].append(second_gpu)
    client = PerformanceClient()
    client.snapshots = lambda: _single_snapshot(snapshot)  # type: ignore[method-assign]
    app = ConstellaTui("http://127.0.0.1:8765")
    app.client = client  # type: ignore[assignment]

    async with app.run_test(size=(90, 28)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("5")
        await pilot.pause(0.2)

        assert app.query_one("#views", ContentSwitcher).current == "performance-view"
        assert client.requests
        _, first_params = client.requests[-1]
        assert first_params["node_id"] == "gpu-a"
        assert first_params["gpu_uuid"] == "GPU-0"
        assert set(first_params["metrics"].split(",")) == {
            metric.key for metric in PERFORMANCE_METRICS
        }
        assert 899 <= float(first_params["until"]) - float(first_params["since"]) <= 901
        chart = str(app.query_one("#performance-chart-sm-active", Static).content)
        assert any("\u2801" <= character <= "\u28ff" for character in chart)
        assert app.query_one("#performance-chart-sm-active", Static).size.height >= 5
        summary = str(app.query_one("#performance-summary-sm-active", Static).content)
        assert "AVG 40.0%" in summary
        assert "COVER 66.7%" in summary
        status = str(app.query_one("#performance-status", Static).content)
        assert "P1/3" in status
        assert "COMPUTE + MEMORY" in status
        assert "gpu-a" in status
        assert "GPU 0" in status
        assert "15m" in status
        assert "LIVE" not in status
        pages = app.query_one("#performance-pages", ContentSwitcher)
        assert pages.current == "performance-page-1"
        first_page = app.query_one("#performance-page-1", Container)
        second_page = app.query_one("#performance-page-2", Container)
        third_page = app.query_one("#performance-page-3", Container)
        assert len(first_page.query(".performance-card")) == 4
        assert len(second_page.query(".performance-card")) == 4
        assert len(third_page.query(".performance-card")) == 3

        first_page_cards = list(first_page.query(".performance-card"))
        first_regions = [card.region for card in first_page_cards]
        assert first_regions[0].y == first_regions[1].y
        assert first_regions[0].x < first_regions[1].x
        assert first_regions[2].y == first_regions[3].y > first_regions[0].y
        assert first_regions[2].x < first_regions[3].x
        assert all(region.height > 0 for region in first_regions)

        await pilot.press("l")
        await pilot.pause()
        assert pages.current == "performance-page-2"
        assert "P2/3" in str(app.query_one("#performance-status", Static).content)
        assert "INTERCONNECT" in str(app.query_one("#performance-status", Static).content)
        bandwidth_summary = str(app.query_one("#performance-summary-pcie-tx", Static).content)
        assert "AVG 40.0 MiB/s" in bandwidth_summary
        await pilot.press("l")
        await pilot.pause()
        assert pages.current == "performance-page-3"
        assert "P3/3" in str(app.query_one("#performance-status", Static).content)
        assert "NON-TENSOR PIPELINES" in str(app.query_one("#performance-status", Static).content)
        await pilot.press("h")
        await pilot.pause()
        assert pages.current == "performance-page-2"
        await pilot.press("h")
        await pilot.pause()
        assert pages.current == "performance-page-1"
        status = str(app.query_one("#performance-status", Static).content)

        await pilot.press("right_square_bracket")
        await pilot.pause(0.2)
        _, range_params = client.requests[-1]
        assert 3599 <= float(range_params["until"]) - float(range_params["since"]) <= 3601
        status = str(app.query_one("#performance-status", Static).content)
        assert "1h" in status

        await pilot.press("r")
        await pilot.pause(0.2)
        assert str(app.query_one("#performance-status", Static).content) == status

        await pilot.press("space")
        await pilot.pause()
        assert str(app.query_one("#performance-status", Static).content) == status
        assert "PAUSED" in str(app.query_one("#performance-notice", Static).content)

        await pilot.press("g")
        await pilot.pause(0.2)
        assert app.selected_gpu_key == "GPU-1"
        assert client.requests[-1][1]["gpu_uuid"] == "GPU-1"
        assert "GPU 1" in str(app.query_one("#performance-status", Static).content)


def test_tui_performance_view_renders_metrics_and_preserves_controls() -> None:
    asyncio.run(exercise_performance_view_and_controls())


async def exercise_performance_capability_and_disabled_states() -> None:
    unsupported_snapshot = deepcopy(SNAPSHOT)
    unsupported_snapshot["nodes"][0]["gpus"][0]["uuid"] = "GPU-0"
    unsupported_client = PerformanceClient()
    unsupported_client.snapshots = lambda: _single_snapshot(  # type: ignore[method-assign]
        unsupported_snapshot
    )
    unsupported_app = ConstellaTui("http://127.0.0.1:8765")
    unsupported_app.client = unsupported_client  # type: ignore[assignment]
    async with unsupported_app.run_test(size=(120, 36)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("5")
        await pilot.pause(0.1)
        assert not unsupported_client.requests
        assert "UNSUPPORTED" in str(
            unsupported_app.query_one("#performance-notice", Static).content
        )

    disabled_snapshot = deepcopy(unsupported_snapshot)
    disabled_snapshot["nodes"][0]["performance_profiles"] = ["nvidia.gpm.v1"]
    disabled_client = PerformanceClient(enabled=False)
    disabled_client.snapshots = lambda: _single_snapshot(  # type: ignore[method-assign]
        disabled_snapshot
    )
    disabled_app = ConstellaTui("http://127.0.0.1:8765")
    disabled_app.client = disabled_client  # type: ignore[assignment]
    async with disabled_app.run_test(size=(120, 36)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("5")
        await pilot.pause(0.2)
        assert "CACHE DISABLED" in str(
            disabled_app.query_one("#performance-notice", Static).content
        )

    pcie_snapshot = deepcopy(unsupported_snapshot)
    pcie_node = pcie_snapshot["nodes"][0]
    pcie_node["performance_profiles"] = ["nvidia.gpm.v1"]
    pcie_node["gpus"][0]["performance"] = {
        "profile": "nvidia.gpm.v1",
        "status": "available",
        "supported_metrics": [
            "nvidia.gpm.pcie_tx_per_second",
            "nvidia.gpm.pcie_rx_per_second",
        ],
    }
    pcie_client = PerformanceClient()
    pcie_client.snapshots = lambda: _single_snapshot(pcie_snapshot)  # type: ignore[method-assign]
    pcie_app = ConstellaTui("http://127.0.0.1:8765")
    pcie_app.client = pcie_client  # type: ignore[assignment]
    async with pcie_app.run_test(size=(120, 36)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("5")
        await pilot.pause(0.2)
        requested = set(pcie_client.requests[-1][1]["metrics"].split(","))
        assert "nvidia.gpm.pcie_tx_per_second" in requested
        assert "nvidia.gpm.nvlink_tx_per_second" not in requested
        assert pcie_app.query_one("#performance-card-pcie-tx", Vertical).display
        assert not pcie_app.query_one("#performance-card-nvlink-tx", Vertical).display


def test_tui_performance_view_handles_capability_and_cache_states() -> None:
    asyncio.run(exercise_performance_capability_and_disabled_states())


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
