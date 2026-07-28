from __future__ import annotations

import argparse
import asyncio
import math
import os
from typing import Any

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Label, ListItem, ListView, Static

from .client import ClusterClient, ClusterConnectionError
from .model import gpu_rows, memory, node_label, percent, process_rows


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("?", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Container(id="help-dialog"):
            yield Static("KEYBOARD", classes="section-title")
            yield Static(
                "[b]Tab / Shift+Tab[/b]  move focus\n"
                "[b]j / k or arrows[/b]  move through rows\n"
                "[b]r[/b]                reconnect now\n"
                "[b]?[/b]                show this help\n"
                "[b]q[/b]                quit Constella",
                id="help-copy",
            )
            yield Label("Press ? or Escape to close", id="help-close")

    def action_dismiss(self) -> None:
        self.dismiss(None)


class NodeItem(ListItem):
    def __init__(self, node_id: str, label: str, status: str) -> None:
        super().__init__(Label(label), classes=f"status-{status}")
        self.node_id = node_id
        self.status = status

    def update_status(self, status: str) -> None:
        self.remove_class(f"status-{self.status}")
        self.status = status
        self.add_class(f"status-{status}")


class ConstellaTui(App[None]):
    """Keyboard-first terminal monitor backed by Constella's manager stream."""

    CSS_PATH = "theme.tcss"
    TITLE = "Constella"
    SUB_TITLE = "cluster monitor"
    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("r", "reconnect", "Reconnect"),
        Binding("?", "help", "Help"),
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
        self._connect_generation = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static(
                "[bold #a7e36d]CONSTELLA[/]  |  GPU CLUSTER OBSERVATORY",
                id="brand",
            )
            yield Static("CONNECTING", id="connection-status", classes="connecting")
        yield Horizontal(
            Static("NODES\n[bold]-[/bold]", id="metric-nodes", classes="metric"),
            Static("ACCELERATORS\n[bold]-[/bold]", id="metric-gpus", classes="metric"),
            Static("GPU UTIL\n[bold]-[/bold]", id="metric-util", classes="metric"),
            Static("MEMORY\n[bold]-[/bold]", id="metric-memory", classes="metric"),
            Static("PROCESSES\n[bold]-[/bold]", id="metric-processes", classes="metric"),
            id="summary",
        )
        with Horizontal(id="workspace"):
            with Vertical(id="node-pane"):
                yield Static("NODES", classes="section-title")
                yield ListView(id="nodes")
            with Vertical(id="detail-pane"):
                yield Static("WAITING FOR CLUSTER DATA", id="state-message")
                yield Static("ACCELERATORS", classes="section-title data-content")
                yield DataTable(
                    id="gpus",
                    zebra_stripes=True,
                    cursor_type="row",
                    cell_padding=2,
                    classes="data-content",
                )
                yield Static("PROCESSES", classes="section-title data-content")
                yield DataTable(
                    id="processes",
                    zebra_stripes=True,
                    cursor_type="row",
                    cell_padding=2,
                    classes="data-content",
                )
        yield Static(f"manager  {escape(self.client.websocket_url)}", id="endpoint")
        yield Footer()

    def on_mount(self) -> None:
        gpu_table = self.query_one("#gpus", DataTable)
        gpu_table.add_columns("GPU", "MODEL", "UTILIZATION", "MEMORY", "MEM %", "TEMP", "POWER")
        process_table = self.query_one("#processes", DataTable)
        process_table.add_columns("GPU", "PID", "USER", "TASK", "GPU MEM", "RUNTIME", "COMMAND")
        self._show_state("Connecting to the Constella manager...")
        self.connect_stream()

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

    def action_reconnect(self) -> None:
        self._connect_generation += 1
        self.snapshot = None
        self._clear_tables()
        self._show_state("Reconnecting to the Constella manager...")
        self.connect_stream()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_cursor_down(self) -> None:
        focused = self.focused
        action = getattr(focused, "action_cursor_down", None)
        if action is not None:
            action()

    def action_cursor_up(self) -> None:
        focused = self.focused
        action = getattr(focused, "action_cursor_up", None)
        if action is not None:
            action()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        node_id = getattr(event.item, "node_id", None)
        if isinstance(node_id, str):
            self.selected_node_id = node_id
            self._render_detail()

    def _set_connection(self, label: str, state_class: str) -> None:
        status = self.query_one("#connection-status", Static)
        status.update(label)
        status.set_classes(state_class)

    def _show_state(self, message: str) -> None:
        self.query_one("#state-message", Static).update(escape(message))
        self.query_one("#state-message").display = True
        for widget in self.query(".data-content"):
            widget.display = False

    def _show_data(self) -> None:
        self.query_one("#state-message").display = False
        for widget in self.query(".data-content"):
            widget.display = True

    def _render_snapshot(self) -> None:
        if self.snapshot is None:
            return
        nodes = [node for node in self.snapshot.get("nodes", []) if isinstance(node, dict)]
        totals = self.snapshot.get("totals")
        totals = totals if isinstance(totals, dict) else {}

        self._update_metric("#metric-nodes", "NODES", f"{int(totals.get('online_node_count', 0))}/{int(totals.get('node_count', len(nodes)))}")
        self._update_metric("#metric-gpus", "ACCELERATORS", str(int(totals.get("accelerator_count") or totals.get("gpu_count", 0))))
        self._update_metric("#metric-util", "GPU UTIL", percent(totals.get("avg_gpu_utilization")))
        self._update_metric("#metric-memory", "MEMORY", f"{memory(totals.get('memory_used_mb'))}\n{percent(totals.get('avg_memory_utilization'))}")
        self._update_metric("#metric-processes", "PROCESSES", str(int(totals.get("active_processes", 0))))

        node_list = self.query_one("#nodes", ListView)
        previous = self.selected_node_id
        ids = [str(node.get("node_id") or node.get("hostname") or "unknown") for node in nodes]
        mounted_ids = [item.node_id for item in node_list.children if isinstance(item, NodeItem)]
        if mounted_ids == ids:
            for item, node in zip(node_list.children, nodes, strict=True):
                if not isinstance(item, NodeItem):
                    continue
                item.query_one(Label).update(node_label(node))
                item.update_status(str(node.get("status") or "offline"))
        else:
            node_list.clear()
            node_list.extend(
                [
                    NodeItem(
                        node_id,
                        node_label(node),
                        str(node.get("status") or "offline"),
                    )
                    for node_id, node in zip(ids, nodes, strict=True)
                ]
            )

        self.selected_node_id = previous if previous in ids else (ids[0] if ids else None)
        if self.selected_node_id is None:
            self._clear_tables()
            self._show_state("No nodes are reporting\n\nStart an agent or check the manager endpoint")
            return
        if self.selected_node_id in ids:
            node_list.index = ids.index(self.selected_node_id)
        self._render_detail()

    def _render_detail(self) -> None:
        node = self._selected_node()
        if node is None:
            return
        self._show_data()
        gpu_table = self.query_one("#gpus", DataTable)
        gpu_table.clear()
        raw_gpus = [gpu for gpu in node.get("gpus", []) if isinstance(gpu, dict)]
        for row, gpu in zip(gpu_rows(node), raw_gpus, strict=True):
            gpu_table.add_row(*self._styled_gpu_cells(row.cells, gpu), key=row.key)

        process_table = self.query_one("#processes", DataTable)
        process_table.clear()
        processes = process_rows(node)
        for row in processes:
            cells: list[str | Text] = list(row.cells)
            cells[1] = Text(row.cells[1], style="bold #d9e5dc")
            cells[3] = Text(row.cells[3], style="bold #a7e36d")
            cells[4] = Text(row.cells[4], style="#f1c66b")
            cells[6] = Text(row.cells[6], style="#9bac9f")
            process_table.add_row(*cells, key=row.key)
        if not processes:
            process_table.add_row("-", "-", "-", "No active processes", "-", "-", "-")

    def _selected_node(self) -> dict[str, Any] | None:
        if self.snapshot is None:
            return None
        for node in self.snapshot.get("nodes", []):
            if isinstance(node, dict) and str(node.get("node_id") or node.get("hostname")) == self.selected_node_id:
                return node
        return None

    def _clear_tables(self) -> None:
        self.query_one("#nodes", ListView).clear()
        self.query_one("#gpus", DataTable).clear()
        self.query_one("#processes", DataTable).clear()

    def _update_metric(self, selector: str, label: str, value: str) -> None:
        self.query_one(selector, Static).update(
            f"[#809486]{label}[/]\n[bold #c9f7a8]{escape(value)}[/]"
        )

    @staticmethod
    def _styled_gpu_cells(
        cells: tuple[str, ...], gpu: dict[str, Any]
    ) -> tuple[str | Text, ...]:
        utilization = float(gpu.get("utilization_gpu") or 0)
        total_memory = float(gpu.get("memory_total_mb") or 0)
        used_memory = float(gpu.get("memory_used_mb") or 0)
        memory_load = used_memory / total_memory * 100 if total_memory else 0
        temperature = float(gpu.get("temperature_c") or 0)

        utilization_style = "#6f806f" if utilization < 10 else "bold #a7e36d"
        memory_style = ConstellaTui._threshold_style(memory_load, warning=80, danger=94)
        temperature_style = ConstellaTui._threshold_style(temperature, warning=75, danger=85)

        meter_text, _, utilization_text = cells[2].partition(" ")
        filled = meter_text.count("█")
        utilization_cell = Text()
        utilization_cell.append(meter_text[:filled], style=utilization_style)
        utilization_cell.append(meter_text[filled:], style="#334238")
        utilization_cell.append(f"  {utilization_text.strip()}", style=utilization_style)

        return (
            Text(cells[0], style="bold #d9e5dc"),
            Text(cells[1], style="#d9e5dc"),
            utilization_cell,
            Text(cells[3], style=memory_style),
            Text(cells[4], style=memory_style),
            Text(cells[5], style=temperature_style),
            Text(cells[6], style="#9bac9f"),
        )

    @staticmethod
    def _threshold_style(value: float, *, warning: float, danger: float) -> str:
        if value >= danger:
            return "bold #ff7777"
        if value >= warning:
            return "bold #f1c66b"
        return "#a7e36d"


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
