from __future__ import annotations

import argparse
import asyncio
import math
import os
import time
from datetime import datetime
from typing import Any
from urllib.parse import quote

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import ContentSwitcher, DataTable, Footer, Label, ListItem, ListView, Static

from . import __version__
from .charts import (
    aligned_heatmap_rows,
    braille_chart,
    braille_multi_chart,
    heatmap_text,
    heatmap_timestamps,
)
from .client import ClusterAPIError, ClusterClient, ClusterConnectionError
from .model import duration, gpu_rows, memory, node_label, percent, process_rows
from .performance import (
    PERFORMANCE_CHART_POINTS,
    PERFORMANCE_METRICS,
    PERFORMANCE_PAGES,
    PERFORMANCE_PROFILE,
    PERFORMANCE_RANGES,
    chart_maximum,
    format_stat,
    latest_value,
    merge_rolling_points,
    metric_points,
    metric_summary,
    metrics_for_gpu,
    selected_performance_series,
)


VIEW_LABELS = {
    "overview": "OVERVIEW",
    "cluster": "CLUSTER",
    "rankings": "RANK",
    "history": "HISTORY",
    "performance": "PERF",
}
RANKING_RANGES = ("24h", "7d", "30d")
HISTORY_RANGES = ("1h", "24h", "7d", "30d")
LIVE_CHART_POINTS = 120
LIVE_CHART_COLUMNS = LIVE_CHART_POINTS // 2
LIVE_CHART_STYLE = "#00E5FF"
HISTORY_GPU_STYLES = (
    "#4DEBFF",
    "#FFB84D",
    "#A64DFF",
    "#4DFF8B",
    "#FF4DCF",
    "#B8FF4D",
    "#4D6BFF",
    "#FF4D4D",
)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("?", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Container(id="help-dialog"):
            yield Static("KEYBOARD", classes="section-title")
            yield Static(
                "[b]1-5[/b]              switch views\n"
                "[b]Tab / Shift+Tab[/b]  move focus\n"
                "[b]j / k or arrows[/b]  move through rows\n"
                "[b]n / g[/b]            next node / GPU\n"
                "[b][ / ][/b]            change analytics range\n"
                "[b]h / l[/b]            change Performance metric page\n"
                "[b]Space[/b]            pause / resume performance\n"
                "[b]r[/b]                refresh current view\n"
                "[b]?[/b]                show this help\n"
                "[b]q[/b]                quit Constella",
                id="help-copy",
            )
            yield Label("Press ? or Escape to close", id="help-close")

    def action_dismiss(self) -> None:
        self.dismiss(None)


class NodeItem(ListItem):
    def __init__(self, node_id: str, label: str, status: str) -> None:
        super().__init__(Label(self._styled_label(label, status)), classes=f"status-{status}")
        self.node_id = node_id
        self.status = status

    def update_node(self, label: str, status: str) -> None:
        self.query_one(Label).update(self._styled_label(label, status))
        self.update_status(status)

    def update_status(self, status: str) -> None:
        self.remove_class(f"status-{self.status}")
        self.status = status
        self.add_class(f"status-{status}")

    @staticmethod
    def _styled_label(label: str, status: str) -> Text:
        node_name, _, details = label.partition("\n")
        status_label, _, gpu_count = details.partition(" ")
        status_style = "#38BDF8" if status == "online" else "bold #FF2A5F"
        text = Text(node_name)
        text.append("\n")
        text.append(status_label, style=status_style)
        text.append(f"  {gpu_count.strip()}", style="#00E5FF")
        return text


class ConstellaTui(App[None]):
    """Keyboard-first terminal monitor backed by Constella's existing APIs."""

    CSS_PATH = "theme.tcss"
    TITLE = "Constella"
    SUB_TITLE = "cluster monitor"
    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("r", "refresh", "Refresh"),
        Binding("?", "help", "Help"),
        Binding("1", "show_overview", show=False),
        Binding("2", "show_cluster", show=False),
        Binding("3", "show_rankings", show=False),
        Binding("4", "show_history", show=False),
        Binding("5", "show_performance", show=False),
        Binding("space", "toggle_performance_live", show=False),
        Binding("h", "previous_performance_page", show=False),
        Binding("l", "next_performance_page", show=False),
        Binding("left_square_bracket", "previous_range", show=False),
        Binding("right_square_bracket", "next_range", show=False),
        Binding("n", "next_node", show=False),
        Binding("g", "next_gpu", show=False),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
    ]

    def __init__(self, manager_url: str, *, reconnect_delay: float = 2.0) -> None:
        super().__init__()
        self.manager_url = manager_url
        self.client = ClusterClient(manager_url)
        self.reconnect_delay = reconnect_delay
        self.snapshot: dict[str, Any] | None = None
        self.selected_node_id: str | None = None
        self.selected_gpu_key: str | None = None
        self.active_view = "overview"
        self.ranking_range = "7d"
        self.history_range = "24h"
        self.ranking_payload: dict[str, Any] | None = None
        self.ranking_error: str | None = None
        self.history_payload: dict[str, Any] | None = None
        self.history_error: str | None = None
        self.performance_range_index = 1
        self.performance_page_index = 0
        self._performance_header_state: tuple[object, ...] | None = None
        self._performance_chart_contexts: dict[
            str, tuple[str, str, int, int]
        ] = {}
        self._performance_chart_points: dict[
            str, list[tuple[float, float | None]]
        ] = {}
        self.performance_payload: dict[str, Any] | None = None
        self.performance_error: str | None = None
        self.performance_live = True
        self._connect_generation = 0
        self._table_event_generations: dict[str, int] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static("[bold #00E5FF]CONSTELLA[/]", id="brand")
            yield Static(id="view-nav")
            yield Static("CONNECTING", id="connection-status", classes="connecting")
        yield Static(id="context-bar")
        with ContentSwitcher(initial="overview-view", id="views"):
            with Container(id="overview-view", classes="view"):
                with Horizontal(id="overview-workspace"):
                    with Vertical(id="node-pane"):
                        yield Static("NODES", classes="section-title")
                        yield ListView(id="nodes")
                    with Vertical(id="overview-detail"):
                        yield Static(id="node-summary")
                        yield Static("ACCELERATORS", classes="section-title")
                        yield DataTable(
                            id="gpus",
                            zebra_stripes=True,
                            cursor_type="row",
                            cell_padding=2,
                            cursor_foreground_priority="renderable",
                            cursor_background_priority="css",
                        )
                        with Horizontal(id="gpu-lower-pane"):
                            with Vertical(id="realtime-pane"):
                                yield Static("LIVE GPU CURVE", id="realtime-title", classes="section-title")
                                yield Static(id="realtime-curve")
                            with Vertical(id="process-pane"):
                                yield Static("GPU PROCESSES", classes="section-title")
                                yield DataTable(
                                    id="processes",
                                    zebra_stripes=True,
                                    cursor_type="none",
                                    cell_padding=2,
                                    cursor_foreground_priority="renderable",
                                    cursor_background_priority="css",
                                )
                                yield Static("GPU TELEMETRY", classes="section-title")
                                yield Static(id="gpu-facts")
                yield Static("WAITING FOR CLUSTER DATA", id="state-message")
            with Container(id="cluster-view", classes="view"):
                yield Static("LIVE NODE INVENTORY", classes="section-title")
                yield DataTable(
                    id="cluster-nodes",
                    zebra_stripes=True,
                    cursor_type="row",
                    cell_padding=1,
                    cursor_foreground_priority="renderable",
                    cursor_background_priority="css",
                )
                yield Static("SELECTED NODE HARDWARE", id="hardware-title", classes="section-title")
                yield DataTable(
                    id="node-hardware",
                    zebra_stripes=True,
                    cursor_type="none",
                    cell_padding=1,
                    cursor_foreground_priority="renderable",
                    cursor_background_priority="css",
                )
            with Container(id="rankings-view", classes="view"):
                yield Static(id="rankings-status", classes="view-status")
                with Horizontal(id="ranking-grid"):
                    with Vertical():
                        yield Static("USER GPU HOURS", classes="section-title")
                        yield DataTable(
                            id="user-rankings", zebra_stripes=True, cursor_type="none"
                        )
                    with Vertical():
                        yield Static("JOB RANKINGS", classes="section-title")
                        yield DataTable(
                            id="job-rankings", zebra_stripes=True, cursor_type="none"
                        )
                yield Static("ANOMALIES", classes="section-title")
                yield DataTable(id="anomalies", zebra_stripes=True, cursor_type="none")
            with Container(id="history-view", classes="view"):
                yield Static(id="history-status", classes="view-status")
                with Horizontal(id="history-charts"):
                    with Vertical():
                        yield Static("GPU UTILIZATION", id="history-gpu-title", classes="section-title")
                        yield Static(id="history-gpu-curve", classes="history-curve")
                    with Vertical():
                        yield Static("MEMORY", id="history-memory-title", classes="section-title")
                        yield Static(id="history-memory-curve", classes="history-curve")
                yield Static(
                    "UTILIZATION HEATMAP  ·  [#00E5FF]LOW[/]  [#A855F7]MID[/]  [#FF6B00]HIGH[/]",
                    classes="section-title",
                )
                yield Static(id="history-heatmap")
            with Container(id="performance-view", classes="view"):
                yield Static(self._performance_header(), id="performance-status", classes="view-status")
                yield Static(id="performance-notice")
                with ContentSwitcher(initial="performance-page-1", id="performance-pages"):
                    for page_index, (_page_label, metrics) in enumerate(
                        PERFORMANCE_PAGES, start=1
                    ):
                        with Container(
                            id=f"performance-page-{page_index}",
                            classes="performance-grid",
                        ):
                            for metric in metrics:
                                with Vertical(
                                    id=f"performance-card-{metric.slug}",
                                    classes="performance-card",
                                ):
                                    yield Static(
                                        f"{metric.group}  ·  {metric.label}",
                                        id=f"performance-title-{metric.slug}",
                                        classes="performance-title section-title",
                                    )
                                    yield Static(
                                        id=f"performance-chart-{metric.slug}",
                                        classes="performance-chart",
                                    )
                                    yield Static(
                                        id=f"performance-summary-{metric.slug}",
                                        classes="performance-summary",
                                    )
                            yield Static(
                                "No supported metrics for this GPU",
                                id=f"performance-empty-{page_index}",
                                classes="performance-empty",
                            )
        yield Footer()

    def on_mount(self) -> None:
        self._configure_tables()
        self._render_navigation()
        self._show_state("Connecting to the Constella manager...")
        self.connect_stream()
        self.set_interval(2.0, self._refresh_performance_if_visible)

    def on_resize(self, event: Resize) -> None:
        if self.is_mounted:
            self.call_after_refresh(self._render_visible_charts)

    def _configure_tables(self) -> None:
        self.query_one("#gpus", DataTable).add_columns(
            ("GPU", "gpu"),
            ("MODEL", "model"),
            ("UTILIZATION", "utilization"),
            ("MEMORY", "memory"),
            ("MEM %", "memory_percent"),
            ("TEMP", "temperature"),
            ("POWER", "power"),
        )
        self.query_one("#processes", DataTable).add_columns(
            ("PID", "pid"),
            ("USER", "user"),
            ("TASK", "task"),
            ("GPU MEM", "gpu_memory"),
            ("RUNTIME", "runtime"),
            ("COMMAND", "command"),
        )
        self.query_one("#cluster-nodes", DataTable).add_columns(
            ("NODE", "node"),
            ("STATUS", "status"),
            ("HOST", "host"),
            ("GPUS", "gpus"),
            ("UTIL", "util"),
            ("MEMORY", "memory"),
            ("TEMP", "temp"),
            ("POWER", "power"),
            ("PROCS", "procs"),
            ("SOURCE", "source"),
        )
        self.query_one("#node-hardware", DataTable).add_columns(
            ("GPU", "gpu"),
            ("MODEL", "model"),
            ("TYPE", "type"),
            ("UUID", "uuid"),
            ("PCI", "pci"),
            ("PSTATE", "pstate"),
            ("COMPUTE", "compute"),
            ("ECC", "ecc"),
        )
        self.query_one("#user-rankings", DataTable).add_columns(
            "#", "USER", "GPU H", "WEIGHTED", "JOBS", "TASKS", "LAST SEEN"
        )
        self.query_one("#job-rankings", DataTable).add_columns(
            "#", "TASK", "USER", "NODE", "GPU H", "GPUS", "DURATION", "STATUS"
        )
        self.query_one("#anomalies", DataTable).add_columns(
            "USER", "TASK", "NODE", "GPU MEM", "UTIL", "IDLE", "REASON"
        )

    @work(exclusive=True, group="cluster-stream")
    async def connect_stream(self) -> None:
        generation = self._connect_generation
        first_snapshot = True
        while generation == self._connect_generation:
            self._set_connection("CONNECTING", "connecting")
            try:
                async for snapshot in self.client.snapshots():
                    if generation != self._connect_generation:
                        return
                    first_snapshot = False
                    self.snapshot = snapshot
                    self._set_connection("LIVE", "live")
                    self._render_snapshot()
                raise ClusterConnectionError("manager closed the cluster stream")
            except ClusterConnectionError as exc:
                self._set_connection("RETRYING", "error")
                if first_snapshot:
                    self._show_state(f"Manager unavailable\n{exc}\n\nRetrying automatically")
                await asyncio.sleep(self.reconnect_delay)

    @work(exclusive=True, group="ranking-api")
    async def load_rankings(self, *, force: bool = False) -> None:
        if self.ranking_payload is not None and not force:
            self._render_rankings()
            return
        self.ranking_error = None
        self.query_one("#rankings-status", Static).update(
            f"LOADING  range {self.ranking_range}  ·  {escape(self.client.http_url)}"
        )
        try:
            payload = await self.client.get_json(
                "/api/analytics/overview", params={"range": self.ranking_range}
            )
        except ClusterAPIError as exc:
            self.ranking_error = str(exc)
            self.ranking_payload = None
        else:
            self.ranking_payload = payload
        self._render_rankings()

    @work(exclusive=True, group="history-api")
    async def load_history(self, *, force: bool = False) -> None:
        node = self._selected_node()
        if node is None:
            self._render_history()
            return
        if self.history_payload is not None and not force:
            if self.history_payload.get("node_id") == self.selected_node_id:
                self._render_history()
                return
        self.history_error = None
        self.query_one("#history-status", Static).update(
            f"LOADING  node {escape(self.selected_node_id or 'unknown')}  ·  range {self.history_range}"
        )
        try:
            payload = await self.client.get_json(
                f"/api/analytics/node/{quote(self.selected_node_id or '', safe='')}",
                params={"range": self.history_range},
            )
        except ClusterAPIError as exc:
            self.history_error = str(exc)
            self.history_payload = None
        else:
            self.history_payload = payload
        self._render_history()
        self.call_after_refresh(self._render_history)

    @work(exclusive=True, group="performance-api")
    async def load_performance(self) -> None:
        node = self._selected_node()
        gpu = self._ensure_selected_gpu()
        if node is None or gpu is None or not self._performance_supported(node):
            self.performance_payload = None
            self.performance_error = None
            self._render_performance()
            return
        node_id = self._node_id(node)
        gpu_uuid = str(gpu.get("uuid") or "")
        if not gpu_uuid:
            self.performance_payload = None
            self.performance_error = "selected GPU does not report a UUID"
            self._render_performance()
            return
        _range_label, range_seconds = PERFORMANCE_RANGES[self.performance_range_index]
        metrics = metrics_for_gpu(gpu)
        range_end = time.time()
        self.performance_error = None
        self._set_performance_notice("")
        try:
            payload = await self.client.get_json(
                "/api/highres/performance",
                params={
                    "node_id": node_id,
                    "gpu_uuid": gpu_uuid,
                    "metrics": ",".join(metric.key for metric in metrics),
                    "since": str(range_end - range_seconds),
                    "until": str(range_end),
                    "max_points": str(PERFORMANCE_CHART_POINTS),
                },
            )
        except ClusterAPIError as exc:
            self.performance_error = str(exc)
            self.performance_payload = None
        else:
            self.performance_payload = payload
        self._render_performance()
        self.call_after_refresh(self._render_performance)

    def _refresh_performance_if_visible(self) -> None:
        if self.active_view == "performance" and self.performance_live:
            self.load_performance()

    def action_refresh(self) -> None:
        if self.active_view == "rankings":
            self.ranking_payload = None
            self.load_rankings(force=True)
        elif self.active_view == "history":
            self.history_payload = None
            self.load_history(force=True)
        elif self.active_view == "performance":
            self.performance_payload = None
            self._render_performance_header()
            self.load_performance()
        else:
            self._connect_generation += 1
            self.connect_stream()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_show_overview(self) -> None:
        self._switch_view("overview")

    def action_show_cluster(self) -> None:
        self._switch_view("cluster")

    def action_show_rankings(self) -> None:
        self._switch_view("rankings")

    def action_show_history(self) -> None:
        self._switch_view("history")

    def action_show_performance(self) -> None:
        self._switch_view("performance")

    def action_toggle_performance_live(self) -> None:
        if self.active_view != "performance":
            return
        self.performance_live = not self.performance_live
        if self.performance_live:
            self.load_performance()
        else:
            self._render_performance()

    def action_previous_performance_page(self) -> None:
        self._change_performance_page(-1)

    def action_next_performance_page(self) -> None:
        self._change_performance_page(1)

    def action_previous_range(self) -> None:
        self._cycle_range(-1)

    def action_next_range(self) -> None:
        self._cycle_range(1)

    def action_next_node(self) -> None:
        nodes = self._snapshot_nodes()
        ids = [self._node_id(node) for node in nodes]
        if not ids:
            return
        current = ids.index(self.selected_node_id) if self.selected_node_id in ids else -1
        self._select_node(ids[(current + 1) % len(ids)])
        if self.active_view == "cluster":
            self._render_cluster()

    def action_next_gpu(self) -> None:
        node = self._selected_node()
        if node is None:
            return
        gpus = [gpu for gpu in node.get("gpus", []) if isinstance(gpu, dict)]
        keys = [self._gpu_key(gpu) for gpu in gpus]
        if not keys:
            return
        current = keys.index(self.selected_gpu_key) if self.selected_gpu_key in keys else -1
        self.selected_gpu_key = keys[(current + 1) % len(keys)]
        if self.active_view == "overview":
            self._render_overview()
        elif self.active_view == "history":
            self._render_history()
        elif self.active_view == "performance":
            self.performance_payload = None
            self._render_performance_header()
            self.load_performance()

    def action_cursor_down(self) -> None:
        action = getattr(self.focused, "action_cursor_down", None)
        if action is not None:
            action()

    def action_cursor_up(self) -> None:
        action = getattr(self.focused, "action_cursor_up", None)
        if action is not None:
            action()

    def _switch_view(self, view: str) -> None:
        self.active_view = view
        self.query_one("#views", ContentSwitcher).current = f"{view}-view"
        self._render_navigation()
        self._render_context()
        if view == "cluster":
            self._render_cluster()
            self.query_one("#cluster-nodes", DataTable).focus()
        elif view == "rankings":
            self.load_rankings()
        elif view == "history":
            self.load_history()
        elif view == "performance":
            self.set_focus(None)
            self._render_performance_header()
            self.load_performance()
        elif view == "overview":
            self._render_overview()
            self.call_after_refresh(self._render_selected_gpu_panel)

    def _cycle_range(self, direction: int) -> None:
        if self.active_view == "rankings":
            values = RANKING_RANGES
            current = values.index(self.ranking_range)
            self.ranking_range = values[(current + direction) % len(values)]
            self.ranking_payload = None
            self.load_rankings(force=True)
        elif self.active_view == "history":
            values = HISTORY_RANGES
            current = values.index(self.history_range)
            self.history_range = values[(current + direction) % len(values)]
            self.history_payload = None
            self.load_history(force=True)
        elif self.active_view == "performance":
            self.performance_range_index = (self.performance_range_index + direction) % len(
                PERFORMANCE_RANGES
            )
            self.performance_payload = None
            self._render_performance_header()
            self.load_performance()

    def _change_performance_page(self, direction: int) -> None:
        if self.active_view != "performance":
            return
        self.performance_page_index = (self.performance_page_index + direction) % len(
            PERFORMANCE_PAGES
        )
        self.query_one("#performance-pages", ContentSwitcher).current = (
            f"performance-page-{self.performance_page_index + 1}"
        )
        self._render_performance_header()
        self.call_after_refresh(self._render_performance)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._select_highlighted_node(event.item)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self._select_highlighted_node(event.item)

    def _select_highlighted_node(self, item: ListItem | None) -> None:
        if item is None:
            return
        node_id = getattr(item, "node_id", None)
        if not isinstance(node_id, str):
            return
        self._select_node(node_id)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._select_table_row(event.data_table, event.row_key)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._select_table_row(event.data_table, event.row_key)

    def _select_table_row(self, table: DataTable, row_key: object) -> None:
        if table.id in self._table_event_generations or row_key is None:
            return
        key = str(getattr(row_key, "value", row_key))
        if table.id == "gpus":
            self.selected_gpu_key = key
            self._render_gpu_selection_markers()
            self._render_selected_gpu_panel()
        elif table.id == "cluster-nodes":
            self._select_node(key, render_overview=False)
            self._render_cluster_selection_markers()
            self._render_node_hardware()

    def _select_node(self, node_id: str, *, render_overview: bool = True) -> None:
        if node_id == self.selected_node_id:
            return
        self.selected_node_id = node_id
        self.selected_gpu_key = None
        self.history_payload = None
        self.performance_payload = None
        self._sync_node_list_cursor()
        if render_overview:
            self._render_overview()
        if self.active_view == "history":
            self.load_history(force=True)
        elif self.active_view == "performance":
            self._ensure_selected_gpu()
            self._render_performance_header()
            self.load_performance()

    def _set_connection(self, label: str, state_class: str) -> None:
        status = self.query_one("#connection-status", Static)
        status.update(label)
        status.set_classes(state_class)

    def _show_state(self, message: str) -> None:
        state = self.query_one("#state-message", Static)
        state.update(escape(message))
        state.display = True
        self.query_one("#overview-workspace").display = False

    def _show_overview(self) -> None:
        self.query_one("#state-message").display = False
        self.query_one("#overview-workspace").display = True

    def _render_snapshot(self) -> None:
        if self.snapshot is None:
            return
        nodes = self._snapshot_nodes()
        ids = [self._node_id(node) for node in nodes]
        if self.selected_node_id not in ids:
            self.selected_node_id = ids[0] if ids else None
            self.selected_gpu_key = None
            self.history_payload = None
            self.performance_payload = None
        self._render_node_list(nodes)
        self._render_context()
        if not nodes:
            self._clear_realtime_tables()
            self._show_state("No nodes are reporting\n\nStart an agent or check the manager endpoint")
            return
        self._show_overview()
        if self.active_view == "overview":
            self._render_overview()
        elif self.active_view == "cluster":
            self._render_cluster()
        elif self.active_view == "performance":
            self._ensure_selected_gpu()
            self._render_performance()
        self.call_after_refresh(self._render_visible_charts)

    def _render_node_list(self, nodes: list[dict[str, Any]]) -> None:
        node_list = self.query_one("#nodes", ListView)
        ids = [self._node_id(node) for node in nodes]
        mounted_ids = [item.node_id for item in node_list.children if isinstance(item, NodeItem)]
        if mounted_ids == ids:
            for item, node in zip(node_list.children, nodes, strict=True):
                if isinstance(item, NodeItem):
                    item.update_node(node_label(node), str(node.get("status") or "offline"))
        else:
            node_list.clear()
            node_list.extend(
                [
                    NodeItem(node_id, node_label(node), str(node.get("status") or "offline"))
                    for node_id, node in zip(ids, nodes, strict=True)
                ]
            )
        self._sync_node_list_cursor()

    def _sync_node_list_cursor(self) -> None:
        if self.snapshot is None or self.selected_node_id is None:
            return
        ids = [self._node_id(node) for node in self._snapshot_nodes()]
        if self.selected_node_id in ids:
            self.query_one("#nodes", ListView).index = ids.index(self.selected_node_id)

    def _render_overview(self) -> None:
        node = self._selected_node()
        if node is None:
            return
        totals = node.get("totals") if isinstance(node.get("totals"), dict) else {}
        gpu_count = int(totals.get("accelerator_count") or totals.get("gpu_count") or 0)
        utilization = float(totals.get("avg_gpu_utilization") or 0)
        memory_used = memory(totals.get("memory_used_mb"))
        process_count = int(totals.get("active_processes") or 0)
        summary = Text()
        summary.append(self._node_id(node), style="bold #E2E8F0")
        summary.append(
            f"  {str(node.get('status') or 'offline').upper():<7}", style="#38BDF8"
        )
        summary.append(f"  ·  {gpu_count:>3} GPU", style="#00E5FF")
        summary.append(
            f"  ·  {percent(utilization):>4} util", style=self._utilization_style(utilization)
        )
        summary.append(f"  ·  {memory_used:>10} used", style="#8A99AD")
        summary.append(f"  ·  {process_count:>3} proc", style="#8A99AD")
        summary.append(f"  ·  {str(node.get('source') or 'unknown')}", style="#8A99AD")
        self.query_one("#node-summary", Static).update(summary)
        self._render_gpu_table(node)
        self._render_selected_gpu_panel()

    def _render_gpu_table(self, node: dict[str, Any]) -> None:
        table = self.query_one("#gpus", DataTable)
        raw_gpus = [gpu for gpu in node.get("gpus", []) if isinstance(gpu, dict)]
        rows = gpu_rows(node)
        keys = [row.key for row in rows]
        if self.selected_gpu_key not in keys:
            self.selected_gpu_key = keys[0] if keys else None
        rendered_rows = [
            self._styled_gpu_cells(
                row.cells, gpu, selected=row.key == self.selected_gpu_key
            )
            for row, gpu in zip(rows, raw_gpus, strict=True)
        ]
        self._sync_keyed_table(
            table,
            keys=keys,
            rows=rendered_rows,
            column_keys=(
                "gpu",
                "model",
                "utilization",
                "memory",
                "memory_percent",
                "temperature",
                "power",
            ),
            selected_key=self.selected_gpu_key,
        )

    def _render_gpu_selection_markers(self) -> None:
        table = self.query_one("#gpus", DataTable)
        node = self._selected_node()
        if node is None:
            return
        mounted_keys = {str(row_key.value) for row_key in table.rows}
        for gpu in node.get("gpus", []):
            if not isinstance(gpu, dict):
                continue
            key = self._gpu_key(gpu)
            if key not in mounted_keys:
                continue
            table.update_cell(
                key,
                "gpu",
                self._gpu_index_cell(
                    int(gpu.get("index") or 0), selected=key == self.selected_gpu_key
                ),
                update_width=False,
            )

    def _sync_keyed_table(
        self,
        table: DataTable,
        *,
        keys: list[str],
        rows: list[tuple[str | Text, ...]],
        column_keys: tuple[str, ...],
        selected_key: str | None,
    ) -> None:
        """Update live table cells without rebuilding stable rows or moving its cursor."""
        mounted_keys = [str(row_key.value) for row_key in table.rows]
        topology_changed = mounted_keys != keys
        generation: int | None = None
        if topology_changed:
            generation = self._suspend_table(table)
            table.clear()
            for key, values in zip(keys, rows, strict=True):
                table.add_row(*values, key=key)
        else:
            for key, values in zip(keys, rows, strict=True):
                for column_key, value in zip(column_keys, values, strict=True):
                    table.update_cell(key, column_key, value, update_width=False)

        selected_row = keys.index(selected_key) if selected_key in keys else 0
        cursor_key = keys[table.cursor_row] if keys and table.cursor_row < len(keys) else None
        if selected_key is not None and keys and (
            topology_changed or cursor_key != selected_key
        ):
            generation = generation or self._suspend_table(table)
            table.move_cursor(row=selected_row, column=0, animate=False, scroll=False)
        if generation is not None:
            self.call_after_refresh(
                self._resume_table_events, table.id or "", generation
            )

    def _suspend_table(self, table: DataTable) -> int:
        table_id = table.id or ""
        generation = self._table_event_generations.get(table_id, 0) + 1
        self._table_event_generations[table_id] = generation
        return generation

    def _resume_table_events(self, table_id: str, generation: int) -> None:
        if self._table_event_generations.get(table_id) == generation:
            self._table_event_generations.pop(table_id, None)

    def _render_selected_gpu_panel(self) -> None:
        gpu = self._selected_gpu()
        node = self._selected_node()
        if gpu is None or node is None:
            self.query_one("#realtime-curve", Static).update("No GPU selected")
            self.query_one("#processes", DataTable).clear()
            self.query_one("#gpu-facts", Static).update("")
            return
        index = int(gpu.get("index") or 0)
        name = str(gpu.get("name") or "unknown")
        self.query_one("#realtime-title", Static).update(f"GPU {index}  ·  {name}")
        history_key = str(gpu.get("gpu_id") or f"{self._node_id(node)}:{gpu.get('uuid')}")
        history = node.get("history") if isinstance(node.get("history"), dict) else {}
        gpu_history = history.get(history_key) if isinstance(history.get(history_key), dict) else {}
        values = gpu_history.get("gpu") if isinstance(gpu_history.get("gpu"), list) else []
        if not values:
            values = [float(gpu.get("utilization_gpu") or 0)]
        chart_widget = self.query_one("#realtime-curve", Static)
        width = min(LIVE_CHART_COLUMNS, max(4, chart_widget.size.width - 6))
        height = max(3, chart_widget.size.height - 1)
        chart_widget.update(
            Text(
                braille_chart(
                    values[-LIVE_CHART_POINTS:],
                    width=width,
                    height=height,
                    resample=False,
                ),
                style=LIVE_CHART_STYLE,
            )
        )
        self._render_process_table(gpu)
        self._render_gpu_facts(gpu)

    def _render_process_table(self, gpu: dict[str, Any]) -> None:
        table = self.query_one("#processes", DataTable)
        node = {"gpus": [gpu]}
        rows = process_rows(node)
        keys: list[str] = []
        rendered_rows: list[tuple[str | Text, ...]] = []
        for row in rows:
            source = row.cells
            keys.append(row.key)
            rendered_rows.append(
                (
                    Text(source[1], style="bold #E2E8F0"),
                    Text(source[2], style="#E2E8F0"),
                    Text(source[3], style="bold #00E5FF"),
                    Text(source[4], style="#E2E8F0"),
                    Text(source[5], style="#8A99AD"),
                    Text(source[6], style="#8A99AD"),
                )
            )
        if not rows:
            keys = ["__empty__"]
            rendered_rows = [("-", "-", "No active processes", "-", "-", "-")]
        self._sync_keyed_table(
            table,
            keys=keys,
            rows=rendered_rows,
            column_keys=("pid", "user", "task", "gpu_memory", "runtime", "command"),
            selected_key=None,
        )

    def _render_gpu_facts(self, gpu: dict[str, Any]) -> None:
        total_memory = float(gpu.get("memory_total_mb") or 0)
        used_memory = float(gpu.get("memory_used_mb") or 0)
        memory_load = used_memory / total_memory * 100 if total_memory else 0
        power = float(gpu.get("power_watts") or 0)
        power_limit = float(gpu.get("power_limit_watts") or 0)
        temperature = float(gpu.get("temperature_c") or 0)
        facts = Text()

        def field(label: str, value: str, style: str = "#E2E8F0") -> None:
            facts.append(f"{label:<10}", style="#8A99AD")
            facts.append(value, style=style)
            facts.append("\n")

        field(
            "MEMORY",
            f"{memory(used_memory)} / {memory(total_memory)}  {memory_load:.0f}%",
            self._threshold_style(memory_load, warning=80, danger=94),
        )
        field("POWER", f"{power:.0f} / {power_limit:.0f} W" if power_limit else f"{power:.0f} W")
        field(
            "TEMP",
            f"{temperature:.0f} C",
            self._threshold_style(temperature, warning=75, danger=85),
        )
        field(
            "CLOCK",
            f"SM {gpu.get('clock_sm_mhz') or '-'} MHz  MEM {gpu.get('clock_mem_mhz') or '-'} MHz",
        )
        field(
            "STATE",
            f"{gpu.get('pstate') or '-'}  compute {gpu.get('compute_mode') or '-'}  ECC {gpu.get('ecc_mode') or '-'}",
        )
        field("PCI", str(gpu.get("pci_bus_id") or "-"), "#8A99AD")
        field("UUID", str(gpu.get("uuid") or "unknown"), "#8A99AD")
        self.query_one("#gpu-facts", Static).update(facts)

    def _render_cluster(self) -> None:
        if self.snapshot is None:
            return
        table = self.query_one("#cluster-nodes", DataTable)
        nodes = self._snapshot_nodes()
        keys = [self._node_id(node) for node in nodes]
        rendered_rows = [
            self._cluster_node_cells(
                node, selected=self._node_id(node) == self.selected_node_id
            )
            for node in nodes
        ]
        self._sync_keyed_table(
            table,
            keys=keys,
            rows=rendered_rows,
            column_keys=(
                "node",
                "status",
                "host",
                "gpus",
                "util",
                "memory",
                "temp",
                "power",
                "procs",
                "source",
            ),
            selected_key=self.selected_node_id,
        )
        self._render_node_hardware()

    def _render_cluster_selection_markers(self) -> None:
        table = self.query_one("#cluster-nodes", DataTable)
        nodes = self._snapshot_nodes()
        mounted_keys = {str(row_key.value) for row_key in table.rows}
        for node in nodes:
            key = self._node_id(node)
            if key not in mounted_keys:
                continue
            table.update_cell(
                key,
                "node",
                self._cluster_node_label(key, selected=key == self.selected_node_id),
                update_width=False,
            )

    def _cluster_node_cells(
        self, node: dict[str, Any], *, selected: bool
    ) -> tuple[str | Text, ...]:
        totals = node.get("totals") if isinstance(node.get("totals"), dict) else {}
        status = str(node.get("status") or "offline")
        status_style = "#38BDF8" if status == "online" else "bold #FF2A5F"
        utilization = float(totals.get("avg_gpu_utilization") or 0)
        return (
            self._cluster_node_label(self._node_id(node), selected=selected),
            Text(status, style=status_style),
            str(node.get("hostname") or "unknown"),
            str(int(totals.get("accelerator_count") or totals.get("gpu_count") or 0)),
            Text(percent(utilization), style=self._utilization_style(utilization)),
            f"{memory(totals.get('memory_used_mb'))} / {memory(totals.get('memory_total_mb'))}",
            self._temperature_text(float(totals.get("max_temperature_c") or 0)),
            f"{float(totals.get('power_watts') or 0):.0f} W",
            str(int(totals.get("active_processes") or 0)),
            str(node.get("source") or "unknown"),
        )

    @staticmethod
    def _cluster_node_label(node_id: str, *, selected: bool) -> Text:
        label = Text()
        label.append("▸ " if selected else "  ", style="bold #00E5FF")
        label.append(node_id, style="bold #FFFFFF" if selected else "bold #E2E8F0")
        return label

    def _render_node_hardware(self) -> None:
        node = self._selected_node()
        table = self.query_one("#node-hardware", DataTable)
        if node is None:
            table.clear()
            return
        self.query_one("#hardware-title", Static).update(
            f"{self._node_id(node)} HARDWARE  ·  driver {node.get('driver_version') or 'unknown'}  ·  agent {node.get('agent_version') or 'unknown'}"
        )
        live_gpus = [gpu for gpu in node.get("gpus", []) if isinstance(gpu, dict)]
        hardware = node.get("hardware") if isinstance(node.get("hardware"), dict) else {}
        known_gpus = [gpu for gpu in hardware.get("gpus", []) if isinstance(gpu, dict)]
        gpus = live_gpus or known_gpus
        keys = [str(gpu.get("uuid") or gpu.get("index") or 0) for gpu in gpus]
        rendered_rows: list[tuple[str | Text, ...]] = [
            (
                str(int(gpu.get("index") or 0)),
                str(gpu.get("name") or "unknown"),
                str(gpu.get("device_type") or "unknown"),
                str(gpu.get("uuid") or "unknown"),
                str(gpu.get("pci_bus_id") or "-"),
                str(gpu.get("pstate") or "-"),
                str(gpu.get("compute_mode") or "-"),
                str(gpu.get("ecc_mode") or "-"),
            )
            for gpu in gpus
        ]
        self._sync_keyed_table(
            table,
            keys=keys,
            rows=rendered_rows,
            column_keys=(
                "gpu",
                "model",
                "type",
                "uuid",
                "pci",
                "pstate",
                "compute",
                "ecc",
            ),
            selected_key=None,
        )

    def _render_rankings(self) -> None:
        status = self.query_one("#rankings-status", Static)
        users_table = self.query_one("#user-rankings", DataTable)
        jobs_table = self.query_one("#job-rankings", DataTable)
        anomaly_table = self.query_one("#anomalies", DataTable)
        users_table.clear()
        jobs_table.clear()
        anomaly_table.clear()
        if self.ranking_error:
            status.update(f"ERROR  {escape(self.ranking_error)}  ·  press r to retry")
            return
        payload = self.ranking_payload
        if payload is None:
            status.update("LOADING ANALYTICS")
            return
        if payload.get("enabled") is False:
            status.update("HISTORY DISABLED  start Constella with SQLite to enable rankings")
            return
        users = self._dict_items(payload.get("user_gpu_hours"))
        jobs = self._dict_items(payload.get("job_rankings"))
        anomalies = self._dict_items(payload.get("anomalies"))
        status.update(
            f"RANGE {self.ranking_range}  ·  {len(users)} users  ·  {len(jobs)} jobs  ·  [ / ] change range"
        )
        for rank, item in enumerate(users[:20], start=1):
            users_table.add_row(
                str(rank),
                Text(str(item.get("user") or "unknown"), style="bold #00E5FF"),
                f"{float(item.get('gpu_hours') or 0):.1f}",
                f"{float(item.get('weighted_gpu_hours') or 0):.1f}",
                str(int(item.get("job_count") or 0)),
                str(int(item.get("task_count") or 0)),
                self._timestamp(item.get("last_seen_at")),
            )
        if not users:
            users_table.add_row("-", "No usage data", "-", "-", "-", "-", "-")
        for rank, item in enumerate(jobs[:20], start=1):
            jobs_table.add_row(
                str(rank),
                Text(str(item.get("task_name") or "unknown"), style="bold #00E5FF"),
                str(item.get("user") or "unknown"),
                str(item.get("node_id") or "unknown"),
                f"{float(item.get('gpu_hours') or 0):.1f}",
                str(int(item.get("gpu_count") or 0)),
                duration(item.get("duration_seconds")),
                str(item.get("status") or "unknown"),
            )
        if not jobs:
            jobs_table.add_row("-", "No jobs in range", "-", "-", "-", "-", "-", "-")
        for item in anomalies[:12]:
            anomaly_table.add_row(
                str(item.get("user") or "unknown"),
                str(item.get("task_name") or "unknown"),
                str(item.get("node_id") or "unknown"),
                Text(f"{float(item.get('gpu_memory_gb') or 0):.1f} GiB", style="#FF6B00"),
                Text(percent(item.get("recent_avg_gpu_utilization")), style="#A855F7"),
                duration(item.get("idle_tail_seconds")),
                Text(str(item.get("reason") or "unknown"), style="#FF2A5F"),
            )
        if not anomalies:
            anomaly_table.add_row("-", "No anomalies detected", "-", "-", "-", "-", "-")

    def _render_history(self) -> None:
        status = self.query_one("#history-status", Static)
        gpu_curve = self.query_one("#history-gpu-curve", Static)
        memory_curve = self.query_one("#history-memory-curve", Static)
        heatmap = self.query_one("#history-heatmap", Static)
        if self.history_error:
            status.update(f"ERROR  {escape(self.history_error)}  ·  press r to retry")
            gpu_curve.update("")
            memory_curve.update("")
            heatmap.update("")
            return
        payload = self.history_payload
        if payload is None:
            status.update("LOADING NODE HISTORY")
            return
        if payload.get("enabled") is False:
            status.update("HISTORY DISABLED  start Constella with SQLite to enable rollups")
            gpu_curve.update("")
            memory_curve.update("")
            heatmap.update("")
            return
        series = self._dict_items(payload.get("series"))
        series.sort(key=self._history_series_sort_key)
        status.update(
            f"NODE {self.selected_node_id or 'unknown'}  ·  RANGE {self.history_range}  ·  "
            f"{len(series)} GPU series  ·  [bold #00E5FF]N[/] next node  ·  "
            "[bold #00E5FF]G[/] highlight GPU  ·  [bold #00E5FF][ / ][/] time range"
        )
        self.query_one("#history-gpu-title", Static).update(
            self._history_title("ALL GPU UTILIZATION", series)
        )
        self.query_one("#history-memory-title", Static).update(
            self._history_title("ALL GPU MEMORY GiB", series)
        )
        if not series:
            gpu_curve.update("No GPU history points for this node")
            memory_curve.update("")
        else:
            timestamps, gpu_series = self._history_curves(
                series, "avg_gpu_utilization"
            )
            _memory_timestamps, memory_series = self._history_curves(
                series, "avg_memory_used_mb", scale=1 / 1024
            )
            memory_max = max(
                (
                    value
                    for values, _style in memory_series
                    for value in values
                    if value is not None
                ),
                default=1.0,
            )
            gpu_curve.update(
                braille_multi_chart(
                    gpu_series,
                    width=max(12, gpu_curve.size.width - 7),
                    height=max(3, gpu_curve.size.height - 1),
                    timestamps=timestamps,
                )
            )
            memory_curve.update(
                braille_multi_chart(
                    memory_series,
                    width=max(12, memory_curve.size.width - 7),
                    height=max(3, memory_curve.size.height - 1),
                    maximum=max(1.0, memory_max),
                    timestamps=timestamps,
                )
            )
        heatmap_items = self._dict_items(payload.get("heatmap"))
        heat_rows = aligned_heatmap_rows(heatmap_items)
        heatmap.update(
            heatmap_text(
                heat_rows,
                max_columns=max(12, heatmap.size.width - 15),
                timestamps=heatmap_timestamps(heatmap_items),
            )
        )

    def _render_performance(self) -> None:
        node = self._selected_node()
        gpu = self._ensure_selected_gpu()
        self._sync_performance_metric_visibility(gpu)
        self._render_performance_header()
        if node is None:
            self._set_performance_notice("WAITING FOR CLUSTER DATA")
            self._clear_performance_charts("No node selected")
            return
        if not self._performance_supported(node):
            profiles = node.get("performance_profiles")
            profile_label = ", ".join(str(item) for item in profiles) if profiles else "base telemetry"
            self._set_performance_notice(
                f"UNSUPPORTED  node {escape(self._node_id(node))} reports {escape(profile_label)}"
            )
            self._clear_performance_charts("Detailed performance is unavailable")
            return
        if gpu is None:
            self._set_performance_notice(
                f"NO GPU  node {escape(self._node_id(node))} has no accelerator data"
            )
            self._clear_performance_charts("No GPU selected")
            return
        if self.performance_error:
            self._set_performance_notice(
                f"ERROR  {escape(self.performance_error)}  ·  press r to retry"
            )
            self._clear_performance_charts("Performance request failed")
            return
        payload = self.performance_payload
        if payload is None:
            self._set_performance_notice("")
            self._clear_performance_charts("Waiting for samples")
            return
        if payload.get("enabled") is False:
            self._set_performance_notice(
                "PERFORMANCE CACHE DISABLED  enable high-resolution GPM collection"
            )
            self._clear_performance_charts("High-resolution cache disabled")
            return
        gpu_uuid = str(gpu.get("uuid") or "")
        series = selected_performance_series(payload, gpu_uuid)
        if series is None:
            self._set_performance_notice("WARMING UP  no performance samples yet")
            self._clear_performance_charts("Warming up · no samples yet")
            return
        self._set_performance_notice("" if self.performance_live else "LIVE REFRESH PAUSED")
        _range_label, range_seconds = PERFORMANCE_RANGES[self.performance_range_index]
        node_id = self._node_id(node)
        for metric in metrics_for_gpu(gpu):
            chart = self.query_one(f"#performance-chart-{metric.slug}", Static)
            title = self.query_one(f"#performance-title-{metric.slug}", Static)
            summary_widget = self.query_one(f"#performance-summary-{metric.slug}", Static)
            raw_timestamps, raw_values = metric_points(series, metric.key)
            current = latest_value(raw_values)
            maximum = chart_maximum(raw_values, metric.unit)
            axis_width = max(4, len(f"{maximum:.0f}") + 1)
            chart_width = max(4, chart.size.width - axis_width)
            pixel_columns = chart_width * 2
            context = (
                node_id,
                gpu_uuid,
                self.performance_range_index,
                pixel_columns,
            )
            if self._performance_chart_contexts.get(metric.key) != context:
                self._performance_chart_contexts[metric.key] = context
                self._performance_chart_points[metric.key] = []
            rolling_points = merge_rolling_points(
                self._performance_chart_points.get(metric.key, []),
                raw_timestamps,
                raw_values,
                bin_seconds=range_seconds / pixel_columns,
                columns=pixel_columns,
            )
            self._performance_chart_points[metric.key] = rolling_points
            timestamps = [timestamp for timestamp, _value in rolling_points]
            values = [value for _timestamp, value in rolling_points]
            title.update(
                f"{metric.group}  ·  {metric.label}  ·  CURRENT {format_stat(current, metric.unit)}"
            )
            if raw_timestamps:
                chart.update(
                    Text(
                        braille_chart(
                            values,
                            width=chart_width,
                            height=max(2, chart.size.height - 1),
                            maximum=maximum,
                            timestamps=timestamps,
                            resample=False,
                        ),
                        style=metric.style,
                    )
                )
            else:
                chart.update(Text("No valid samples in this range", style="#59677A"))
            summary = metric_summary(series, metric.key)
            summary_text = Text()
            summary_text.append("AVG ", style="#8A99AD")
            summary_text.append(format_stat(summary.get("avg"), metric.unit), style=metric.style)
            summary_text.append("  PEAK ", style="#8A99AD")
            summary_text.append(format_stat(summary.get("max"), metric.unit), style="#E2E8F0")
            summary_text.append("  P95 ", style="#8A99AD")
            summary_text.append(format_stat(summary.get("p95"), metric.unit), style="#E2E8F0")
            summary_text.append("  COVER ", style="#8A99AD")
            summary_text.append(format_stat(summary.get("coverage")), style="#E2E8F0")
            summary_widget.update(summary_text)

    def _performance_header(self) -> Text:
        page_label, _metrics = PERFORMANCE_PAGES[self.performance_page_index]
        node = self._selected_node()
        gpu = self._selected_gpu()
        node_label = self._node_id(node) if node is not None else "—"
        gpu_index = int(gpu.get("index") or 0) if gpu is not None else "—"
        range_label, _range_seconds = PERFORMANCE_RANGES[self.performance_range_index]
        text = Text()
        text.append(
            f"P{self.performance_page_index + 1}/{len(PERFORMANCE_PAGES)}  ·  {page_label}  ·  ",
            style="#8A99AD",
        )
        text.append(str(node_label), style="bold #E2E8F0")
        text.append("  ·  GPU ", style="#8A99AD")
        text.append(str(gpu_index), style="bold #00E5FF")
        text.append(f"  ·  {range_label}  ·  ", style="#8A99AD")
        text.append("H/L", style="bold #00E5FF")
        text.append("  ·  ", style="#8A99AD")
        text.append("N/G", style="bold #00E5FF")
        text.append("  ·  ", style="#8A99AD")
        text.append("[/]", style="bold #00E5FF")
        text.append("  ·  ", style="#8A99AD")
        text.append("Space", style="bold #00E5FF")
        return text

    def _render_performance_header(self) -> None:
        node = self._selected_node()
        gpu = self._selected_gpu()
        state = (
            self.performance_page_index,
            self._node_id(node) if node is not None else None,
            self._gpu_key(gpu) if gpu is not None else None,
            self.performance_range_index,
        )
        if state == self._performance_header_state:
            return
        self._performance_header_state = state
        self.query_one("#performance-status", Static).update(self._performance_header())

    def _set_performance_notice(self, message: str) -> None:
        notice = self.query_one("#performance-notice", Static)
        notice.update(message)
        notice.display = bool(message)

    def _clear_performance_charts(self, message: str) -> None:
        for metric in PERFORMANCE_METRICS:
            self.query_one(f"#performance-title-{metric.slug}", Static).update(
                f"{metric.group}  ·  {metric.label}"
            )
            self.query_one(f"#performance-chart-{metric.slug}", Static).update(
                Text(message, style="#59677A")
            )
            self.query_one(f"#performance-summary-{metric.slug}", Static).update("")

    def _sync_performance_metric_visibility(self, gpu: dict[str, Any] | None) -> None:
        visible_keys = {metric.key for metric in metrics_for_gpu(gpu)}
        for page_index, (_label, metrics) in enumerate(PERFORMANCE_PAGES, start=1):
            visible_count = 0
            for metric in metrics:
                card = self.query_one(f"#performance-card-{metric.slug}", Vertical)
                card.display = metric.key in visible_keys
                visible_count += int(card.display)
            self.query_one(f"#performance-empty-{page_index}", Static).display = not visible_count

    def _render_visible_charts(self) -> None:
        if self.active_view == "overview" and self.snapshot is not None:
            self._render_selected_gpu_panel()
        elif self.active_view == "history" and self.history_payload is not None:
            self._render_history()
        elif self.active_view == "performance":
            self._render_performance()

    def _history_curves(
        self,
        series: list[dict[str, Any]],
        value_key: str,
        *,
        scale: float = 1.0,
    ) -> tuple[list[float], list[tuple[list[float | None], str]]]:
        timestamps = sorted(
            {
                float(point["bucket_start"])
                for item in series
                for point in self._dict_items(item.get("points"))
                if point.get("bucket_start") is not None
            }
        )
        selected = self._selected_gpu()
        selected_uuid = str(selected.get("uuid")) if selected else None
        entries: list[tuple[bool, list[float | None], str]] = []
        for index, item in enumerate(series):
            values_by_time = {
                float(point["bucket_start"]): (
                float(point[value_key]) * scale
                if point.get(value_key) is not None
                else None
                )
                for point in self._dict_items(item.get("points"))
                if point.get("bucket_start") is not None
            }
            is_selected = bool(
                selected_uuid and str(item.get("gpu_uuid")) == selected_uuid
            )
            color = HISTORY_GPU_STYLES[index % len(HISTORY_GPU_STYLES)]
            style = f"bold {color}" if is_selected else color
            entries.append(
                (is_selected, [values_by_time.get(timestamp) for timestamp in timestamps], style)
            )
        entries.sort(key=lambda entry: entry[0])
        return timestamps, [(values, style) for _selected, values, style in entries]

    def _history_title(self, label: str, series: list[dict[str, Any]]) -> Text:
        selected = self._selected_gpu()
        selected_uuid = str(selected.get("uuid")) if selected else None
        title = Text(f"{label}  ·  ")
        for index, item in enumerate(series):
            if index:
                title.append(" ", style="#8A99AD")
            gpu_index = item.get("gpu_index")
            gpu_label = str(gpu_index) if gpu_index is not None else "?"
            is_selected = bool(
                selected_uuid and str(item.get("gpu_uuid")) == selected_uuid
            )
            color = HISTORY_GPU_STYLES[index % len(HISTORY_GPU_STYLES)]
            title.append(f"▸{gpu_label}" if is_selected else gpu_label, style=f"bold {color}")
        return title

    @staticmethod
    def _history_series_sort_key(item: dict[str, Any]) -> tuple[int, str]:
        try:
            gpu_index = int(item.get("gpu_index"))
        except (TypeError, ValueError):
            gpu_index = 2**31 - 1
        return gpu_index, str(item.get("gpu_uuid") or "")

    def _render_navigation(self) -> None:
        parts: list[str] = []
        for index, (key, label) in enumerate(VIEW_LABELS.items(), start=1):
            if key == self.active_view:
                parts.append(f"[bold #00E5FF reverse] {index} {label} [/]")
            else:
                parts.append(f"[#8A99AD] {index} {label} [/]")
        self.query_one("#view-nav", Static).update(" ".join(parts))

    def _render_context(self) -> None:
        context = self.query_one("#context-bar", Static)
        if self.snapshot is None:
            context.update(f"manager  {escape(self.client.websocket_url)}")
            return
        totals = self.snapshot.get("totals")
        totals = totals if isinstance(totals, dict) else {}
        online_nodes = int(totals.get("online_node_count") or 0)
        node_count = int(totals.get("node_count") or 0)
        gpu_count = int(totals.get("accelerator_count") or totals.get("gpu_count") or 0)
        utilization = float(totals.get("avg_gpu_utilization") or 0)
        memory_used = memory(totals.get("memory_used_mb"))
        sequence = int(self.snapshot.get("seq") or 0)
        text = Text()
        text.append(f"{self.active_view.upper()}  ·  ", style="bold #00E5FF")
        text.append(f"{online_nodes:>3}/{node_count:>3} nodes", style="#E2E8F0")
        text.append(f"  ·  {gpu_count:>3} GPU", style="#8A99AD")
        text.append(
            f"  ·  {percent(utilization):>4} util", style=self._utilization_style(utilization)
        )
        text.append(f"  ·  {memory_used:>10} used", style="#8A99AD")
        text.append(f"  ·  seq {sequence:>8}", style="#59677A")
        context.update(text)

    def _selected_node(self) -> dict[str, Any] | None:
        for node in self._snapshot_nodes():
            if self._node_id(node) == self.selected_node_id:
                return node
        return None

    def _selected_gpu(self) -> dict[str, Any] | None:
        node = self._selected_node()
        if node is None:
            return None
        for gpu in node.get("gpus", []):
            if isinstance(gpu, dict) and self._gpu_key(gpu) == self.selected_gpu_key:
                return gpu
        return None

    def _ensure_selected_gpu(self) -> dict[str, Any] | None:
        selected = self._selected_gpu()
        if selected is not None:
            return selected
        node = self._selected_node()
        if node is None:
            return None
        gpus = [gpu for gpu in node.get("gpus", []) if isinstance(gpu, dict)]
        if not gpus:
            return None
        self.selected_gpu_key = self._gpu_key(gpus[0])
        return gpus[0]

    @staticmethod
    def _performance_supported(node: dict[str, Any]) -> bool:
        profiles = node.get("performance_profiles")
        return isinstance(profiles, list) and PERFORMANCE_PROFILE in profiles

    def _snapshot_nodes(self) -> list[dict[str, Any]]:
        if self.snapshot is None:
            return []
        return self._dict_items(self.snapshot.get("nodes"))

    @staticmethod
    def _node_id(node: dict[str, Any]) -> str:
        return str(node.get("node_id") or node.get("hostname") or "unknown")

    @staticmethod
    def _gpu_key(gpu: dict[str, Any]) -> str:
        return str(gpu.get("gpu_id") or gpu.get("uuid") or gpu.get("index") or 0)

    @staticmethod
    def _dict_items(value: object) -> list[dict[str, Any]]:
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def _clear_realtime_tables(self) -> None:
        self.query_one("#nodes", ListView).clear()
        self.query_one("#gpus", DataTable).clear()
        self.query_one("#processes", DataTable).clear()
        self.query_one("#cluster-nodes", DataTable).clear()
        self.query_one("#node-hardware", DataTable).clear()

    @staticmethod
    def _timestamp(value: object) -> str:
        try:
            return datetime.fromtimestamp(float(value)).strftime("%m-%d %H:%M")
        except (TypeError, ValueError, OSError):
            return "-"

    @staticmethod
    def _temperature_text(value: float) -> Text:
        return Text(f"{value:.0f} C", style=ConstellaTui._threshold_style(value, warning=75, danger=85))

    @staticmethod
    def _styled_gpu_cells(
        cells: tuple[str, ...], gpu: dict[str, Any], *, selected: bool = False
    ) -> tuple[str | Text, ...]:
        utilization = float(gpu.get("utilization_gpu") or 0)
        total_memory = float(gpu.get("memory_total_mb") or 0)
        used_memory = float(gpu.get("memory_used_mb") or 0)
        memory_load = used_memory / total_memory * 100 if total_memory else 0
        temperature = float(gpu.get("temperature_c") or 0)
        utilization_style = ConstellaTui._utilization_style(utilization)
        memory_style = ConstellaTui._threshold_style(memory_load, warning=80, danger=94)
        temperature_style = ConstellaTui._threshold_style(temperature, warning=75, danger=85)
        meter_text, _, utilization_text = cells[2].partition(" ")
        filled = meter_text.count("█")
        utilization_cell = Text()
        utilization_cell.append(meter_text[:filled], style=utilization_style)
        utilization_cell.append(meter_text[filled:], style="#121824")
        utilization_cell.append(f"  {utilization_text.strip()}", style=utilization_style)
        return (
            ConstellaTui._gpu_index_cell(int(cells[0]), selected=selected),
            Text(cells[1], style="#E2E8F0"),
            utilization_cell,
            Text(cells[3], style=memory_style),
            Text(cells[4], style=memory_style),
            Text(cells[5], style=temperature_style),
            Text(cells[6], style="#8A99AD"),
        )

    @staticmethod
    def _gpu_index_cell(index: int, *, selected: bool) -> Text:
        cell = Text()
        cell.append("▸ " if selected else "  ", style="bold #00E5FF")
        cell.append(str(index), style="bold #FFFFFF" if selected else "bold #E2E8F0")
        return cell

    @staticmethod
    def _utilization_style(value: float) -> str:
        if value >= 85:
            return "bold #FF6B00"
        if value >= 60:
            return "bold #A855F7"
        return "bold #00E5FF"

    @staticmethod
    def _threshold_style(value: float, *, warning: float, danger: float) -> str:
        if value >= danger:
            return "bold #FF2A5F"
        if value >= warning:
            return "bold #FF6B00"
        return "#00E5FF"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="constella-tui",
        description="Keyboard-first terminal interface for a Constella manager.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--url",
        default=os.environ.get("CONSTELLA_URL", "http://127.0.0.1:8765"),
        help="manager HTTP or WebSocket URL (default: %(default)s)",
    )
    parser.add_argument(
        "--reconnect-delay",
        type=float,
        default=2.0,
        help="seconds between reconnect attempts (default: %(default)s)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not math.isfinite(args.reconnect_delay) or args.reconnect_delay < 0.1:
        parser.error("--reconnect-delay must be a finite value of at least 0.1 seconds")
    try:
        app = ConstellaTui(args.url, reconnect_delay=args.reconnect_delay)
    except ValueError as exc:
        parser.error(str(exc))
    app.run()
