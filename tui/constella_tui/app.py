from __future__ import annotations

import argparse
import asyncio
import math
import os
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

from .charts import aligned_heatmap_rows, braille_chart, heatmap_text
from .client import ClusterAPIError, ClusterClient, ClusterConnectionError
from .model import duration, gpu_rows, memory, node_label, percent, process_rows


VIEW_LABELS = {
    "overview": "OVERVIEW",
    "cluster": "CLUSTER",
    "rankings": "RANKINGS",
    "history": "HISTORY",
}
RANKING_RANGES = ("24h", "7d", "30d")
HISTORY_RANGES = ("1h", "24h", "7d", "30d")


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("?", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Container(id="help-dialog"):
            yield Static("KEYBOARD", classes="section-title")
            yield Static(
                "[b]1-4[/b]              switch views\n"
                "[b]Tab / Shift+Tab[/b]  move focus\n"
                "[b]j / k or arrows[/b]  move through rows\n"
                "[b]n / g[/b]            next node / GPU\n"
                "[b][ / ][/b]            change analytics range\n"
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
        self._connect_generation = 0
        self._suspend_table_events = False

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
                                    cursor_type="row",
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
                    cursor_type="row",
                    cell_padding=1,
                    cursor_foreground_priority="renderable",
                    cursor_background_priority="css",
                )
            with Container(id="rankings-view", classes="view"):
                yield Static(id="rankings-status", classes="view-status")
                with Horizontal(id="ranking-grid"):
                    with Vertical():
                        yield Static("USER GPU HOURS", classes="section-title")
                        yield DataTable(id="user-rankings", zebra_stripes=True, cursor_type="row")
                    with Vertical():
                        yield Static("JOB RANKINGS", classes="section-title")
                        yield DataTable(id="job-rankings", zebra_stripes=True, cursor_type="row")
                yield Static("ANOMALIES", classes="section-title")
                yield DataTable(id="anomalies", zebra_stripes=True, cursor_type="row")
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
        yield Footer()

    def on_mount(self) -> None:
        self._configure_tables()
        self._render_navigation()
        self._show_state("Connecting to the Constella manager...")
        self.connect_stream()

    def on_resize(self, event: Resize) -> None:
        if self.is_mounted:
            self.call_after_refresh(self._render_visible_charts)

    def _configure_tables(self) -> None:
        self.query_one("#gpus", DataTable).add_columns(
            "GPU", "MODEL", "UTILIZATION", "MEMORY", "MEM %", "TEMP", "POWER"
        )
        self.query_one("#processes", DataTable).add_columns(
            "PID", "USER", "TASK", "GPU MEM", "RUNTIME", "COMMAND"
        )
        self.query_one("#cluster-nodes", DataTable).add_columns(
            "NODE", "STATUS", "HOST", "GPUS", "UTIL", "MEMORY", "TEMP", "POWER", "PROCS", "SOURCE"
        )
        self.query_one("#node-hardware", DataTable).add_columns(
            "GPU", "MODEL", "TYPE", "UUID", "PCI", "PSTATE", "COMPUTE", "ECC"
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

    def action_refresh(self) -> None:
        if self.active_view == "rankings":
            self.ranking_payload = None
            self.load_rankings(force=True)
        elif self.active_view == "history":
            self.history_payload = None
            self.load_history(force=True)
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
        elif view == "rankings":
            self.load_rankings()
        elif view == "history":
            self.load_history()
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

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        node_id = getattr(event.item, "node_id", None)
        if not isinstance(node_id, str):
            return
        self._select_node(node_id)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._suspend_table_events or event.row_key is None:
            return
        key = str(event.row_key.value)
        if event.data_table.id == "gpus":
            self.selected_gpu_key = key
            self._render_selected_gpu_panel()
        elif event.data_table.id == "cluster-nodes":
            self._select_node(key, render_overview=False)
            self._render_node_hardware()

    def _select_node(self, node_id: str, *, render_overview: bool = True) -> None:
        if node_id == self.selected_node_id:
            return
        self.selected_node_id = node_id
        self.selected_gpu_key = None
        self.history_payload = None
        self._sync_node_list_cursor()
        if render_overview:
            self._render_overview()
        if self.active_view == "history":
            self.load_history(force=True)

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
        summary = Text()
        summary.append(self._node_id(node), style="bold #E2E8F0")
        summary.append(f"  {str(node.get('status') or 'offline').upper()}", style="#38BDF8")
        summary.append(f"  ·  {int(totals.get('accelerator_count') or totals.get('gpu_count') or 0)} GPU", style="#00E5FF")
        summary.append(f"  ·  {percent(totals.get('avg_gpu_utilization'))} util", style=self._utilization_style(float(totals.get("avg_gpu_utilization") or 0)))
        summary.append(f"  ·  {memory(totals.get('memory_used_mb'))} used", style="#8A99AD")
        summary.append(f"  ·  {int(totals.get('active_processes') or 0)} proc", style="#8A99AD")
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
        selected_row = keys.index(self.selected_gpu_key) if self.selected_gpu_key in keys else 0

        self._suspend_table_events = True
        table.clear()
        for row, gpu in zip(rows, raw_gpus, strict=True):
            table.add_row(*self._styled_gpu_cells(row.cells, gpu), key=row.key)
        if rows:
            table.move_cursor(row=selected_row, column=0, animate=False, scroll=False)
        self.call_after_refresh(self._resume_table_events)

    def _resume_table_events(self) -> None:
        self._suspend_table_events = False

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
        width = max(12, chart_widget.size.width - 7)
        height = max(3, chart_widget.size.height - 1)
        chart_widget.update(
            Text(
                braille_chart(values, width=width, height=height),
                style=self._utilization_style(float(values[-1] if values else 0)),
            )
        )
        self._render_process_table(gpu)
        self._render_gpu_facts(gpu)

    def _render_process_table(self, gpu: dict[str, Any]) -> None:
        table = self.query_one("#processes", DataTable)
        table.clear()
        node = {"gpus": [gpu]}
        rows = process_rows(node)
        for row in rows:
            source = row.cells
            cells: tuple[str | Text, ...] = (
                Text(source[1], style="bold #E2E8F0"),
                Text(source[2], style="#E2E8F0"),
                Text(source[3], style="bold #00E5FF"),
                Text(source[4], style="#E2E8F0"),
                Text(source[5], style="#8A99AD"),
                Text(source[6], style="#8A99AD"),
            )
            table.add_row(*cells, key=row.key)
        if not rows:
            table.add_row("-", "-", "No active processes", "-", "-", "-")

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
        selected_row = keys.index(self.selected_node_id) if self.selected_node_id in keys else 0
        self._suspend_table_events = True
        table.clear()
        for node in nodes:
            totals = node.get("totals") if isinstance(node.get("totals"), dict) else {}
            status = str(node.get("status") or "offline")
            status_style = "#38BDF8" if status == "online" else "bold #FF2A5F"
            table.add_row(
                Text(self._node_id(node), style="bold #E2E8F0"),
                Text(status, style=status_style),
                str(node.get("hostname") or "unknown"),
                str(int(totals.get("accelerator_count") or totals.get("gpu_count") or 0)),
                Text(percent(totals.get("avg_gpu_utilization")), style=self._utilization_style(float(totals.get("avg_gpu_utilization") or 0))),
                f"{memory(totals.get('memory_used_mb'))} / {memory(totals.get('memory_total_mb'))}",
                self._temperature_text(float(totals.get("max_temperature_c") or 0)),
                f"{float(totals.get('power_watts') or 0):.0f} W",
                str(int(totals.get("active_processes") or 0)),
                str(node.get("source") or "unknown"),
                key=self._node_id(node),
            )
        if nodes:
            table.move_cursor(row=selected_row, column=0, animate=False, scroll=False)
        self.call_after_refresh(self._resume_table_events)
        self._render_node_hardware()

    def _render_node_hardware(self) -> None:
        node = self._selected_node()
        table = self.query_one("#node-hardware", DataTable)
        table.clear()
        if node is None:
            return
        self.query_one("#hardware-title", Static).update(
            f"{self._node_id(node)} HARDWARE  ·  driver {node.get('driver_version') or 'unknown'}  ·  agent {node.get('agent_version') or 'unknown'}"
        )
        live_gpus = [gpu for gpu in node.get("gpus", []) if isinstance(gpu, dict)]
        hardware = node.get("hardware") if isinstance(node.get("hardware"), dict) else {}
        known_gpus = [gpu for gpu in hardware.get("gpus", []) if isinstance(gpu, dict)]
        for gpu in live_gpus or known_gpus:
            if not isinstance(gpu, dict):
                continue
            table.add_row(
                str(int(gpu.get("index") or 0)),
                str(gpu.get("name") or "unknown"),
                str(gpu.get("device_type") or "unknown"),
                str(gpu.get("uuid") or "unknown"),
                str(gpu.get("pci_bus_id") or "-"),
                str(gpu.get("pstate") or "-"),
                str(gpu.get("compute_mode") or "-"),
                str(gpu.get("ecc_mode") or "-"),
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
        selected = self._history_series(series)
        status.update(
            f"NODE {self.selected_node_id or 'unknown'}  ·  RANGE {self.history_range}  ·  {len(series)} GPU series  ·  n/g select  ·  [ / ] range"
        )
        if selected is None:
            gpu_curve.update("No history points for the selected GPU")
            memory_curve.update("")
        else:
            points = self._dict_items(selected.get("points"))
            gpu_values = [float(point.get("avg_gpu_utilization") or 0) for point in points]
            memory_values = [float(point.get("avg_memory_used_mb") or 0) / 1024 for point in points]
            memory_max = max(memory_values, default=1.0)
            gpu_curve.update(
                Text(
                    braille_chart(
                        gpu_values,
                        width=max(12, gpu_curve.size.width - 7),
                        height=max(3, gpu_curve.size.height - 1),
                    ),
                    style="#00E5FF",
                )
            )
            memory_curve.update(
                Text(
                    braille_chart(
                        memory_values,
                        width=max(12, memory_curve.size.width - 7),
                        height=max(3, memory_curve.size.height - 1),
                        maximum=max(1.0, memory_max),
                    ),
                    style="#A855F7",
                )
            )
            label = f"GPU {selected.get('gpu_index') if selected.get('gpu_index') is not None else '?'}"
            self.query_one("#history-gpu-title", Static).update(f"{label} UTILIZATION")
            self.query_one("#history-memory-title", Static).update(f"{label} MEMORY GiB")
        heat_rows = aligned_heatmap_rows(self._dict_items(payload.get("heatmap")))
        heatmap.update(heatmap_text(heat_rows, max_columns=max(12, heatmap.size.width - 15)))

    def _render_visible_charts(self) -> None:
        if self.active_view == "overview" and self.snapshot is not None:
            self._render_selected_gpu_panel()
        elif self.active_view == "history" and self.history_payload is not None:
            self._render_history()

    def _history_series(self, series: list[dict[str, Any]]) -> dict[str, Any] | None:
        gpu = self._selected_gpu()
        selected_uuid = str(gpu.get("uuid")) if gpu else None
        for item in series:
            if selected_uuid and str(item.get("gpu_uuid")) == selected_uuid:
                return item
        return series[0] if series else None

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
        text = Text()
        text.append(f"{self.active_view.upper()}  ", style="bold #00E5FF")
        text.append(f"{int(totals.get('online_node_count') or 0)}/{int(totals.get('node_count') or 0)} nodes", style="#E2E8F0")
        text.append(f"  ·  {int(totals.get('accelerator_count') or totals.get('gpu_count') or 0)} GPU", style="#8A99AD")
        text.append(f"  ·  {percent(totals.get('avg_gpu_utilization'))} util", style=self._utilization_style(float(totals.get("avg_gpu_utilization") or 0)))
        text.append(f"  ·  {memory(totals.get('memory_used_mb'))} used", style="#8A99AD")
        text.append(f"  ·  seq {int(self.snapshot.get('seq') or 0)}", style="#59677A")
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
        cells: tuple[str, ...], gpu: dict[str, Any]
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
            Text(cells[0], style="bold #E2E8F0"),
            Text(cells[1], style="#E2E8F0"),
            utilization_cell,
            Text(cells[3], style=memory_style),
            Text(cells[4], style=memory_style),
            Text(cells[5], style=temperature_style),
            Text(cells[6], style="#8A99AD"),
        )

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
