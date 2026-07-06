from __future__ import annotations

import time

from fastapi.testclient import TestClient

from constella.analytics import gpu_weight, job_key, node_analytics, overlap_seconds, overview_analytics
from constella.app import create_app
from constella.cluster import ClusterState
from constella.db import AsyncDBSink, ROLLUP_20S, SQLiteSinkConfig, SQLiteStore
from constella.schema import GpuInfo, GpuProcess, NodeSnapshot, node_totals_from_gpus


def make_snapshot(
    sampled_at: float,
    *,
    user: str = "alice",
    pid: int = 111,
    gpu_memory_mb: int = 24 * 1024,
    gpu_util: int = 4,
) -> NodeSnapshot:
    process = GpuProcess(
        pid=pid,
        name="python",
        task_name="train.py",
        user=user,
        cmdline_hash="cmdhash",
        gpu_memory_mb=gpu_memory_mb,
        ppid=10,
        process_start_time=100.0,
        parent_start_time=80.0,
    )
    gpus = [
        GpuInfo(
            index=0,
            node_id="node-a",
            gpu_id="node-a:GPU-0",
            uuid="GPU-0",
            name="NVIDIA H100 80GB HBM3",
            utilization_gpu=gpu_util,
            memory_total_mb=80 * 1024,
            memory_used_mb=gpu_memory_mb,
            power_watts=120,
            power_limit_watts=700,
            temperature_c=43,
            processes=[process],
        ),
        GpuInfo(
            index=1,
            node_id="node-a",
            gpu_id="node-a:GPU-1",
            uuid="GPU-1",
            name="NVIDIA RTX PRO 6000",
            utilization_gpu=gpu_util + 1,
            memory_total_mb=96 * 1024,
            memory_used_mb=gpu_memory_mb,
            power_watts=130,
            power_limit_watts=600,
            temperature_c=44,
            processes=[process],
        ),
    ]
    return NodeSnapshot(
        node_id="node-a",
        hostname="node-a-host",
        seq=int(sampled_at),
        sampled_at=sampled_at,
        received_at=sampled_at + 0.1,
        refresh_interval=1.0,
        process_interval=3.0,
        status="online",
        source="test",
        gpus=gpus,
        totals=node_totals_from_gpus(gpus),
    )


def seed_rollups(store: SQLiteStore, *, base: float = 0.0) -> None:
    rows = []
    for bucket_start in (base + 2000.0, base + 4000.0, base + 6000.0, base + 8000.0):
        for gpu_uuid, util in (("GPU-0", 3.0), ("GPU-1", 4.0)):
            rows.append(
                {
                    "bucket_start": bucket_start,
                    "bucket_seconds": ROLLUP_20S,
                    "node_id": "node-a",
                    "gpu_uuid": gpu_uuid,
                    "avg_gpu_utilization": util,
                    "max_gpu_utilization": util + 2,
                    "avg_memory_used_mb": 24 * 1024.0,
                    "max_memory_used_mb": 24 * 1024,
                    "avg_power_watts": 120.0,
                    "max_power_watts": 140.0,
                    "avg_temperature_c": 43.0,
                    "max_temperature_c": 45,
                    "sample_count": 3,
                }
            )
    store.upsert_gpu_metric_rollups(rows)


def test_overlap_gpu_weight_and_job_key_helpers() -> None:
    assert overlap_seconds(10, 30, 20, 40) == 10
    assert overlap_seconds(10, 30, 40, 50) == 0
    assert gpu_weight("NVIDIA RTX PRO 6000 Ada") == 0.9
    assert gpu_weight("NVIDIA H100") == 1.0
    row = {
        "node_id": "node-a",
        "user": "alice",
        "parent_start_time": 80.0,
        "process_start_time": 100.0,
        "first_seen_at": 200.0,
        "ppid": 10,
        "pid": 111,
    }
    assert job_key(row) == "node-a:alice:80.0:10"
    row["parent_start_time"] = -1000.0
    assert job_key(row) == "node-a:alice:100.0:111"


def test_overview_analytics_aggregates_users_jobs_and_anomalies(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "constella.db")
    store.open()
    try:
        store.write_node_snapshot(make_snapshot(1000.0))
        store.write_node_snapshot(make_snapshot(9000.0))
        seed_rollups(store)

        payload = overview_analytics(store, range_name="7d", now=10_000.0)

        assert payload["enabled"] is True
        assert payload["timezone"] == "Asia/Shanghai"
        user = payload["user_gpu_hours"][0]
        assert user["user"] == "alice"
        assert user["task_count"] == 1
        assert user["job_count"] == 1
        assert round(user["gpu_hours"], 2) == 4.44
        assert round(user["weighted_gpu_hours"], 2) == 4.22
        assert payload["job_rankings"][0]["gpu_count"] == 2
        assert payload["anomalies"][0]["user"] == "alice"
        assert payload["anomalies"][0]["gpu_indices"] == [0, 1]
        assert payload["anomalies"][0]["pids"] == [111]
        assert payload["anomalies"][0]["recent_avg_gpu_utilization"] < 5
    finally:
        store.close()


def test_node_analytics_returns_series_and_heatmap(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "constella.db")
    store.open()
    try:
        store.write_node_snapshot(make_snapshot(1000.0))
        seed_rollups(store)

        payload = node_analytics(store, node_id="node-a", range_name="24h", now=10_000.0)

        assert payload["enabled"] is True
        assert payload["bucket_seconds"] >= 20
        assert payload["gpus"][0]["uuid"] == "GPU-0"
        assert payload["series"][0]["gpu_uuid"] == "GPU-0"
        assert payload["series"][0]["points"][0]["avg_gpu_utilization"] == 3.0
        assert payload["heatmap_bucket_seconds"] == 3600
        assert payload["heatmap"][0]["buckets"]
    finally:
        store.close()


def test_analytics_api_disabled_without_db() -> None:
    client = TestClient(create_app(cluster_state=ClusterState(local_node_id="local")))

    assert client.get("/api/analytics/overview").json() == {"enabled": False}
    assert client.get("/api/analytics/node/node-a").json() == {"enabled": False}


def test_analytics_api_reads_optional_db_sink(tmp_path) -> None:
    now = time.time()
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
    sink.store.open()
    sink.store.write_node_snapshot(make_snapshot(now - 2000))
    sink.store.write_node_snapshot(make_snapshot(now - 1000))
    seed_rollups(sink.store, base=now - 9000)
    client = TestClient(
        create_app(
            cluster_state=ClusterState(local_node_id="local"),
            db_sink=sink,
        )
    )

    overview = client.get("/api/analytics/overview?range=7d").json()
    node = client.get("/api/analytics/node/node-a?range=24h").json()

    assert overview["enabled"] is True
    assert overview["user_gpu_hours"][0]["user"] == "alice"
    assert node["enabled"] is True
    assert node["node_id"] == "node-a"
    sink.store.close()
