from __future__ import annotations

import json
import math

import pytest
from fastapi.testclient import TestClient

from constella.app import create_app
from constella.cluster import ClusterState
from constella.performance_rollup import NvidiaGpmRollupBucket
from constella.schema import AcceleratorPerformance
from constella.telemetry import telemetry_json

DRAM = "nvidia.gpm.dram_bw_active"
SM = "nvidia.gpm.sm_active"


def sample(seq=1, *, invalid=float("nan")):
    return {
        "type": "sample", "node_id": "test", "seq": seq, "sampled_at": 100.0 + seq,
        "snapshot": {"gpus": [
            {"index": 0, "uuid": "bad", "performance": {
                "profile": "nvidia.gpm.v1", "status": "available", "sampled_at": 100.0,
                "metrics": {DRAM: invalid, SM: 25.0}, "supported_metrics": [DRAM, SM],
            }},
            {"index": 1, "uuid": "healthy", "utilization_gpu": 80},
        ]},
    }


def strict_json(raw):
    def invalid(value):
        raise AssertionError(f"Illegal JSON constant: {value}")
    return json.loads(raw, parse_constant=invalid)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf"), "bad", None, True])
def test_bad_metric_isolated_and_recovers(invalid):
    state = ClusterState(local_node_id="test")
    assert state.ingest_sample(sample(invalid=invalid))
    payload = strict_json(telemetry_json(state.snapshot().to_dict()))
    gpu, healthy = payload["nodes"][0]["gpus"]
    assert gpu["performance"]["metrics"] == {SM: 25.0}
    assert gpu["performance"]["invalid_metrics"] == [DRAM]
    assert gpu["performance"]["status"] == "available"
    assert healthy["utilization_gpu"] == 80
    assert state.ingest_sample(sample(2, invalid=12.0))
    assert not state.snapshot().nodes[0].gpus[0].performance.invalid_metrics


def test_basic_reading_and_malformed_gpu_do_not_discard_healthy_gpu():
    message = sample()
    message["snapshot"]["gpus"][0]["utilization_gpu"] = float("inf")
    message["snapshot"]["gpus"].append({"index": "broken", "uuid": "broken"})
    state = ClusterState(local_node_id="test")
    state.ingest_sample(message)
    node = state.snapshot().nodes[0]
    assert "utilization_gpu" in node.gpus[0].telemetry_errors
    assert node.gpus[2].error
    assert node.gpus[1].utilization_gpu == 80
    assert node.totals.avg_gpu_utilization == 80
    assert "test:bad" not in node.history


def test_rollup_omits_invalid_metric_without_losing_valid_counts():
    performance = AcceleratorPerformance(
        profile="nvidia.gpm.v1", status="available", sampled_at=100,
        metrics={SM: 20, DRAM: 10},
    )
    bucket = NvidiaGpmRollupBucket(100, "test", "gpu")
    bucket.add(performance)
    # Also defend aggregation against a mutated/legacy producer bypassing validation.
    performance.metrics[DRAM] = float("nan")
    bucket.add(performance)
    row = bucket.to_row(20)
    assert row["expected_count"] == 2
    assert row["dram_bw_active_count"] == 1
    assert row["avg_dram_bw_active"] == 10
    assert row["sm_active_count"] == 2
    assert all(not isinstance(v, float) or math.isfinite(v) for v in row.values())


def test_websocket_stays_open_for_bad_metric_and_bad_sample_then_recovers():
    state = ClusterState(local_node_id="test")
    with TestClient(create_app(cluster_state=state, agent_token="test")) as client:
        with client.websocket_connect("/api/agents/ws", headers={"authorization": "Bearer test"}) as agent:
            agent.send_json({"type": "hello", "schema_version": 1, "node_id": "test", "hostname": "test"})
            assert agent.receive_json()["type"] == "config"
            broken = sample()
            broken["sampled_at"] = float("inf")
            agent.send_json(broken)
            assert agent.receive_json()["accepted"] is False
            agent.send_json(sample())
            assert agent.receive_json()["accepted"] is True
            with client.websocket_connect("/ws/cluster") as stream:
                first = strict_json(stream.receive_text())
                assert first["nodes"][0]["gpus"][0]["performance"]["invalid_metrics"] == [DRAM]
                assert client.get("/api/health").json()["telemetry"]["invalid_metric_count"] == 1
                agent.send_json(sample(2, invalid=50))
                assert agent.receive_json()["accepted"] is True
                second = strict_json(stream.receive_text())
                assert second["seq"] > first["seq"]
                assert second["nodes"][0]["gpus"][0]["performance"]["metrics"][DRAM] == 50


def test_final_wire_guard_covers_nested_unknown_fields():
    assert strict_json(telemetry_json({"future_field": [float("nan"), {"x": float("inf")}] })) == {
        "future_field": [None, {"x": None}],
    }


def test_invalid_readings_do_not_pollute_caches_or_database(tmp_path):
    from constella.db import AsyncDBSink, SQLiteSinkConfig
    from constella.highres import HighresGpuCache, gpu_sample_message

    state = ClusterState(local_node_id="test")
    message = sample()
    message["snapshot"]["gpus"][0]["utilization_gpu"] = float("nan")
    state.ingest_sample(message)
    snapshot = state.snapshot().nodes[0]
    cache = HighresGpuCache()
    cache.add_snapshot(snapshot)
    assert cache.rings[("test", "bad")].count == 0
    assert cache.rings[("test", "healthy")].count == 1
    sidecar = HighresGpuCache()
    sidecar.add_sample_message(gpu_sample_message(snapshot))
    assert sidecar.rings[("test", "bad")].count == 0
    assert sidecar.rings[("test", "healthy")].count == 1
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "test.db"))
    sink._accumulate_snapshot(snapshot)
    assert all(key[2] != "bad" for key in sink._rollup_20s)
    bucket = next(value for key, value in sink._nvidia_gpm_rollup_20s.items() if key[2] == "bad")
    row = bucket.to_row(20)
    assert row["dram_bw_active_count"] == 0
    assert row["avg_dram_bw_active"] is None
    assert row["sm_active_count"] == 1


def test_nvml_failure_is_visible_and_fresh_reading_can_recover():
    import ctypes
    from types import SimpleNamespace
    from constella.nvml import NVMLSampler, NvmlUtilization
    from constella.schema import GpuInfo

    sampler = object.__new__(NVMLSampler)
    sampler._lib = SimpleNamespace(nvmlDeviceGetUtilizationRates=lambda *_: 16)
    broken = GpuInfo(index=0)
    sampler._fill_utilization(broken, ctypes.c_void_p(1))
    assert broken.error == "GPU requires reset"
    assert "utilization_gpu" in broken.telemetry_errors
    assert not broken.basic_metrics_valid

    def healthy_reading(_handle, ptr):
        util = ctypes.cast(ptr, ctypes.POINTER(NvmlUtilization)).contents
        util.gpu, util.memory = 42, 12
        return 0
    sampler._lib.nvmlDeviceGetUtilizationRates = healthy_reading
    recovered = GpuInfo(index=0)
    sampler._fill_utilization(recovered, ctypes.c_void_p(1))
    assert recovered.basic_metrics_valid
    assert recovered.utilization_gpu == 42
