from __future__ import annotations

import pytest

from constella.collector import ALLOWED_REFRESH_INTERVALS, SnapshotCollector
from constella.schema import Snapshot


def test_collector_accepts_allowed_refresh_intervals() -> None:
    collector = SnapshotCollector(refresh_interval=1.0, process_interval=3.0)

    for interval in ALLOWED_REFRESH_INTERVALS:
        settings = collector.set_refresh_interval(interval)

        assert settings["refresh_interval"] == interval
        assert collector.refresh_interval == interval


def test_collector_rejects_unsupported_refresh_intervals() -> None:
    collector = SnapshotCollector(refresh_interval=1.0, process_interval=3.0)

    for interval in (0.25, 3.0, 10.0):
        with pytest.raises(ValueError):
            collector.set_refresh_interval(interval)


def test_snapshot_uses_runtime_refresh_interval() -> None:
    collector = SnapshotCollector(refresh_interval=1.0, process_interval=3.0)
    collector.set_refresh_interval(2.0)
    snapshot = Snapshot(
        ok=True,
        source="test",
        hostname="node",
        timestamp=1.0,
        elapsed_ms=2.0,
    )

    collector._publish(snapshot)

    assert snapshot.refresh_interval == 2.0
    assert collector.snapshot is snapshot
