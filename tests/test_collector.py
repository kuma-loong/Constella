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


def test_collector_falls_back_to_ascend_npu(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = Snapshot(
        ok=True,
        source="npu-smi",
        hostname="npu-node",
        timestamp=1.0,
        elapsed_ms=1.0,
        gpus=[],
    )

    class FailingDcmi:
        def __init__(self, **_: object) -> None:
            raise RuntimeError("no DCMI")

    class FakeNpu:
        def __init__(self) -> None:
            pass

        def sample(self) -> Snapshot:
            return expected

    monkeypatch.setattr("constella.collector.DCMISampler", FailingDcmi)
    monkeypatch.setattr("constella.collector.NPUSampler", FakeNpu)

    assert SnapshotCollector(device_type="ascend")._sample_once() is expected


def test_nvidia_collector_does_not_fall_back_to_npu(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingNvml:
        def __init__(self, **_: object) -> None:
            raise RuntimeError("no NVML")

    def fail_nvidia_smi(**_: object) -> Snapshot:
        raise RuntimeError("no nvidia-smi")

    class UnexpectedNpu:
        def __init__(self) -> None:
            raise AssertionError("NPU backend must not run for an NVIDIA collector")

    monkeypatch.setattr("constella.collector.NVMLSampler", FailingNvml)
    monkeypatch.setattr("constella.collector.nvidia_smi.sample", fail_nvidia_smi)
    monkeypatch.setattr("constella.collector.NPUSampler", UnexpectedNpu)

    snapshot = SnapshotCollector(device_type="nvidia")._sample_once()
    assert not snapshot.ok
    assert "npu-smi" not in (snapshot.error or "")
