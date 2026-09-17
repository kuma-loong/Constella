from __future__ import annotations

import pytest

from constella.collector import PREFERRED_RETRY_SECONDS, SnapshotCollector
from constella.schema import AcceleratorPerformance, GpuInfo, Snapshot


def reading(
    source: str = "nvml",
    *,
    gpus: list[GpuInfo] | None = None,
    ok: bool = True,
) -> Snapshot:
    return Snapshot(
        ok=ok,
        source=source,
        hostname="test",
        timestamp=1,
        elapsed_ms=1,
        gpus=[GpuInfo(index=0)] if gpus is None else gpus,
    )


@pytest.mark.parametrize(
    "device,factory,source",
    [
        ("nvidia", "NVMLSampler", "nvml"),
        ("ascend", "DCMISampler", "dcmi"),
    ],
)
def test_fallback_retries_on_deadline_and_returns_to_preferred(
    monkeypatch, device, factory, source
):
    now = [100.0]
    attempts = []
    samples = []
    monkeypatch.setattr("constella.collector.time.monotonic", lambda: now[0])

    class Preferred:
        def __init__(self, **kwargs):
            attempts.append(now[0])
            if len(attempts) < 3:
                raise RuntimeError("driver unavailable")

        def sample(self):
            samples.append(now[0])
            return reading(source)

        def set_process_interval(self, _):
            pass

        def close(self):
            pass

    class Fallback:
        def sample(self):
            return reading("fallback")

    monkeypatch.setattr(f"constella.collector.{factory}", Preferred)
    monkeypatch.setattr("constella.collector.NPUSampler", Fallback)
    monkeypatch.setattr("constella.collector.nvidia_smi.sample", lambda **_: reading("fallback"))
    collector = SnapshotCollector(device_type=device)
    assert collector.sample_once().source == "fallback"
    for t in (101, 110, 100 + PREFERRED_RETRY_SECONDS - 1):
        now[0] = t
        assert collector.sample_once().source == "fallback"
    assert attempts == [100]
    now[0] = 100 + PREFERRED_RETRY_SECONDS
    assert collector.sample_once().source == "fallback"
    now[0] += PREFERRED_RETRY_SECONDS
    assert collector.sample_once().source == source
    now[0] += 100
    assert collector.sample_once().source == source
    assert len(attempts) == 3  # healthy sampling never reinitializes
    assert len(samples) == 2


@pytest.mark.parametrize("issue", ["device", "metric", "performance"])
def test_partial_errors_rebuild_after_deadline_without_discarding_siblings(monkeypatch, issue):
    now = [100.0]
    created, closed = [], []
    monkeypatch.setattr("constella.collector.time.monotonic", lambda: now[0])
    gpu = GpuInfo(index=0)
    if issue == "device":
        gpu.error = "GPU requires reset"
    elif issue == "metric":
        gpu.telemetry_errors = {"power_watts": "query failed"}
    else:
        gpu.performance = AcceleratorPerformance(
            profile="nvidia.gpm.v1",
            status="available",
            sampled_at=1,
            metrics={"nvidia.gpm.sm_active": float("nan")},
        )
    degraded = reading(gpus=[gpu, GpuInfo(index=1, utilization_gpu=42)])

    class Preferred:
        def __init__(self, **kwargs):
            created.append(self)

        def sample(self):
            return degraded if self is created[0] else reading()

        def set_process_interval(self, _):
            pass

        def close(self):
            closed.append(self)

    monkeypatch.setattr("constella.collector.NVMLSampler", Preferred)
    collector = SnapshotCollector()
    for t in (100, 101, 100 + PREFERRED_RETRY_SECONDS - 1):
        now[0] = t
        snapshot = collector.sample_once()
        assert snapshot is degraded
        assert snapshot.gpus[1].utilization_gpu == 42
    assert not closed
    now[0] = 100 + PREFERRED_RETRY_SECONDS
    assert not collector.sample_once().gpus[0].error
    assert closed == [created[0]]
    assert len(created) == 2
    now[0] += PREFERRED_RETRY_SECONDS + 1
    collector.sample_once()
    assert len(created) == 2


def test_transient_error_cancels_reinitialization(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("constella.collector.time.monotonic", lambda: now[0])

    class Preferred:
        def __init__(self, **kwargs):
            pass

        def sample(self):
            return reading(gpus=[GpuInfo(index=0, error="temporary" if now[0] == 100 else None)])

        def set_process_interval(self, _):
            pass

        def close(self):
            pytest.fail("recovered sampler should not be closed")

    monkeypatch.setattr("constella.collector.NVMLSampler", Preferred)
    collector = SnapshotCollector()
    collector.sample_once()
    now[0] = 101
    collector.sample_once()
    now[0] = 100 + PREFERRED_RETRY_SECONDS + 1
    collector.sample_once()
    assert collector._preferred_retry_at == 0


@pytest.mark.parametrize("failure", ["exception", "empty", "not_ok"])
def test_runtime_failure_and_cleanup_failure_do_not_stop_recovery(monkeypatch, failure):
    now = [100.0]
    monkeypatch.setattr("constella.collector.time.monotonic", lambda: now[0])

    class Preferred:
        def __init__(self, **kwargs):
            pass

        def sample(self):
            if now[0] >= 100 + PREFERRED_RETRY_SECONDS:
                return reading()
            if failure == "exception":
                raise RuntimeError("driver lost")
            return reading(gpus=[] if failure == "empty" else None, ok=failure != "not_ok")

        def close(self):
            raise RuntimeError("driver cleanup failed")

    monkeypatch.setattr("constella.collector.NVMLSampler", Preferred)
    monkeypatch.setattr("constella.collector.nvidia_smi.sample", lambda **_: reading("nvidia-smi"))
    collector = SnapshotCollector()
    assert collector.sample_once().source == "nvidia-smi"
    now[0] = 100 + PREFERRED_RETRY_SECONDS
    assert collector.sample_once().source == "nvml"
