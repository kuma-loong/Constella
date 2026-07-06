from __future__ import annotations

from fastapi.testclient import TestClient

from constella.app import create_app
from constella.cluster import ClusterState
from constella.db import AsyncDBSink, SQLiteSinkConfig
from constella.highres import GpuSampleRing, HighresGpuCache, query_jobs
from constella.schema import GpuInfo, GpuProcess, NodeSnapshot, node_totals_from_gpus


def make_node_snapshot(sampled_at: float, *, gpu_util: int = 50) -> NodeSnapshot:
    process = GpuProcess(
        pid=1234,
        name="python",
        task_name="train.py",
        user="alice",
        cmdline="python train.py",
        cmdline_hash="hash",
        gpu_memory_mb=2048,
        ppid=4321,
        process_start_time=90.0,
        parent_start_time=80.0,
    )
    gpus = [
        GpuInfo(
            index=0,
            node_id="node-a",
            gpu_id="node-a:GPU-0",
            uuid="GPU-0",
            name="NVIDIA Test",
            utilization_gpu=gpu_util,
            utilization_mem=20,
            memory_total_mb=100,
            memory_used_mb=20,
            power_watts=100,
            power_limit_watts=200,
            temperature_c=40,
            processes=[process],
        ),
        GpuInfo(
            index=1,
            node_id="node-a",
            gpu_id="node-a:GPU-1",
            uuid="GPU-1",
            name="NVIDIA Test",
            utilization_gpu=gpu_util + 10,
            utilization_mem=30,
            memory_total_mb=100,
            memory_used_mb=30,
            power_watts=120,
            power_limit_watts=200,
            temperature_c=45,
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
        agent_version="0.2.0",
    )


def test_gpu_sample_ring_wraps_and_returns_chronological_window() -> None:
    ring = GpuSampleRing(capacity=3)
    for sampled_at in (1.0, 2.0, 3.0, 4.0):
        ring.append(sampled_at=sampled_at, gpu=make_node_snapshot(sampled_at).gpus[0])

    points = ring.points(since=2.5, until=4.0)

    assert ring.oldest_at == 2.0
    assert ring.newest_at == 4.0
    assert [point["sampled_at"] for point in points] == [3.0, 4.0]


def test_query_jobs_groups_sessions_by_existing_job_key(tmp_path) -> None:
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
    sink.store.open()
    try:
        sink.store.write_node_snapshot(make_node_snapshot(100.0))
        sink.store.write_node_snapshot(make_node_snapshot(110.0))

        jobs = query_jobs(sink.store, now=120.0)

        assert len(jobs) == 1
        assert jobs[0]["task_name"] == "train.py"
        assert jobs[0]["pids"] == [1234]
        assert jobs[0]["gpu_count"] == 2
        assert jobs[0]["duration_seconds"] == 10.0
    finally:
        sink.store.close()


def test_highres_job_curve_api_returns_memory_series(tmp_path) -> None:
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
    sink.store.open()
    cache = HighresGpuCache(retention_seconds=120.0, min_interval_seconds=1.0)
    try:
        for sampled_at in range(70, 131):
            cache.add_snapshot(make_node_snapshot(float(sampled_at), gpu_util=sampled_at % 100))
        sink.store.write_node_snapshot(make_node_snapshot(100.0, gpu_util=50))
        sink.store.write_node_snapshot(make_node_snapshot(110.0, gpu_util=60))
        job = query_jobs(sink.store, now=120.0)[0]
        client = TestClient(
            create_app(
                cluster_state=ClusterState(local_node_id="local"),
                db_sink=sink,
                highres_cache=cache,
            )
        )

        response = client.get(f"/api/highres/jobs/{job['job_key']}/gpu?padding_seconds=20")

        assert response.status_code == 200
        payload = response.json()
        assert payload["enabled"] is True
        assert payload["source"] == "high_res_memory"
        assert payload["expired"] is False
        assert len(payload["series"]) == 2
        assert payload["series"][0]["points"][0]["sampled_at"] == 80.0
    finally:
        sink.store.close()
