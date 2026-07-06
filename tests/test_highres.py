from __future__ import annotations

import time

from fastapi.testclient import TestClient

from constella.app import create_app
from constella.cluster import ClusterState
from constella.db import AsyncDBSink, SQLiteSinkConfig
from constella.highres import (
    HIGHRES_JOB_LOOKBACK_SECONDS,
    GpuSampleRing,
    HighresGpuCache,
    query_jobs,
)
from constella.highres_sidecar import HighresSidecarConfig, create_highres_sidecar_app
from constella.schema import GpuInfo, GpuProcess, NodeSnapshot, node_totals_from_gpus


def make_node_snapshot(
    sampled_at: float,
    *,
    gpu_util: int = 50,
    pid: int = 1234,
    process_start_time: float = 90.0,
    ppid: int = 4321,
    parent_start_time: float = 80.0,
) -> NodeSnapshot:
    process = GpuProcess(
        pid=pid,
        name="python",
        task_name="train.py",
        user="alice",
        cmdline="python train.py",
        cmdline_hash="hash",
        gpu_memory_mb=2048,
        ppid=ppid,
        process_start_time=process_start_time,
        parent_start_time=parent_start_time,
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


def test_query_jobs_does_not_merge_short_tasks_from_long_lived_parent(tmp_path) -> None:
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
    sink.store.open()
    try:
        sink.store.write_node_snapshot(
            make_node_snapshot(100.0, pid=1234, process_start_time=95.0, ppid=4321, parent_start_time=-1000.0)
        )
        sink.store.write_node_snapshot(
            make_node_snapshot(140.0, pid=5678, process_start_time=135.0, ppid=4321, parent_start_time=-1000.0)
        )

        jobs = query_jobs(sink.store, now=160.0)

        assert len(jobs) == 2
        assert [job["pids"] for job in jobs] == [[5678], [1234]]
    finally:
        sink.store.close()


def test_query_jobs_defaults_to_seven_days_and_includes_long_jobs(tmp_path) -> None:
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
    sink.store.open()
    try:
        sink.store.write_node_snapshot(make_node_snapshot(100.0))
        sink.store.write_node_snapshot(make_node_snapshot(4100.0))

        recent_jobs = query_jobs(sink.store, now=4200.0)
        expired_jobs = query_jobs(sink.store, now=4100.0 + HIGHRES_JOB_LOOKBACK_SECONDS + 1)

        assert len(recent_jobs) == 1
        assert recent_jobs[0]["duration_seconds"] == 4000.0
        assert expired_jobs == []
    finally:
        sink.store.close()


def test_highres_job_curve_api_returns_memory_series(tmp_path) -> None:
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
    sink.store.open()
    cache = HighresGpuCache(retention_seconds=120.0, min_interval_seconds=1.0)
    try:
        base = time.time()
        for offset in range(-30, 31):
            sampled_at = base + offset
            cache.add_snapshot(make_node_snapshot(sampled_at, gpu_util=int(sampled_at) % 100))
        sink.store.write_node_snapshot(make_node_snapshot(base, gpu_util=50))
        sink.store.write_node_snapshot(make_node_snapshot(base + 10.0, gpu_util=60))
        job = query_jobs(sink.store, now=base + 20.0)[0]
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
        assert payload["series"][0]["points"][0]["sampled_at"] == base - 20.0
    finally:
        sink.store.close()


def test_highres_job_curve_padding_gap_does_not_force_rollup(tmp_path) -> None:
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
    sink.store.open()
    cache = HighresGpuCache(retention_seconds=120.0, min_interval_seconds=1.0)
    try:
        base = time.time()
        for offset in range(0, 11):
            sampled_at = base + offset
            cache.add_snapshot(make_node_snapshot(sampled_at, gpu_util=int(sampled_at) % 100))
        sink.store.write_node_snapshot(make_node_snapshot(base, gpu_util=50))
        sink.store.write_node_snapshot(make_node_snapshot(base + 10.0, gpu_util=60))
        job = query_jobs(sink.store, now=base + 11.0)[0]
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
        assert payload["source"] == "high_res_memory"
        assert payload["warnings"] == []
        assert payload["series"][0]["points"][0]["sampled_at"] == base
        assert payload["series"][0]["points"][-1]["sampled_at"] == base + 10.0
    finally:
        sink.store.close()


def test_manager_highres_stream_emits_light_gpu_sample() -> None:
    client = TestClient(
        create_app(
            cluster_state=ClusterState(local_node_id="local"),
            agent_token="secret",
        )
    )

    with client.websocket_connect("/api/highres/stream") as stream:
        assert stream.receive_json()["type"] == "hello"
        with client.websocket_connect(
            "/api/agents/ws",
            headers={"authorization": "Bearer secret"},
        ) as agent:
            agent.send_json(
                {
                    "type": "hello",
                    "schema_version": 1,
                    "node_id": "node-a",
                    "hostname": "node-a-host",
                    "agent_version": "0.2.0",
                }
            )
            assert agent.receive_json()["type"] == "config"
            agent.send_json(
                {
                    "type": "sample",
                    "schema_version": 1,
                    "node_id": "node-a",
                    "seq": 1,
                    "sampled_at": 10.0,
                    "refresh_interval": 0.5,
                    "process_interval": 3.0,
                    "snapshot": {
                        "ok": True,
                        "source": "test",
                        "hostname": "node-a-host",
                        "timestamp": 10.0,
                        "gpus": [
                            {
                                "index": 0,
                                "uuid": "GPU-a",
                                "name": "NVIDIA Test",
                                "memory_total_mb": 100,
                                "memory_used_mb": 20,
                                "utilization_gpu": 42,
                            }
                        ],
                    },
                }
            )
            assert agent.receive_json()["accepted"] is True
        message = stream.receive_json()

    assert message["type"] == "gpu_sample"
    assert message["node_id"] == "node-a"
    assert message["refresh_interval"] == 0.5
    assert message["gpus"] == [
        {
            "uuid": "GPU-a",
            "gpu_index": 0,
            "name": "NVIDIA Test",
            "utilization_gpu": 42,
            "utilization_mem": 0,
            "memory_used_mb": 20,
            "memory_total_mb": 100,
            "power_watts": 0.0,
            "temperature_c": 0,
        }
    ]


class IdleStreamClient:
    async def run_forever(self) -> None:
        import asyncio

        await asyncio.Event().wait()

    def status(self) -> dict[str, object]:
        return {"connected": False}


def test_highres_sidecar_serves_jobs_from_sqlite_and_memory_cache(tmp_path) -> None:
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "constella.db"))
    sink.store.open()
    cache = HighresGpuCache(retention_seconds=120.0, min_interval_seconds=1.0)
    try:
        base = time.time()
        for offset in range(-30, 31):
            sampled_at = base + offset
            cache.add_snapshot(make_node_snapshot(sampled_at, gpu_util=int(sampled_at) % 100))
        sink.store.write_node_snapshot(make_node_snapshot(base, gpu_util=50))
        sink.store.write_node_snapshot(make_node_snapshot(base + 10.0, gpu_util=60))
        app = create_highres_sidecar_app(
            HighresSidecarConfig(db_path=tmp_path / "constella.db"),
            store=sink.store,
            cache=cache,
            stream_client=IdleStreamClient(),
        )
        client = TestClient(app)

        jobs_response = client.get("/api/highres/jobs?q=alice")
        job = jobs_response.json()["items"][0]
        curve_response = client.get(f"/api/highres/jobs/{job['job_key']}/gpu?padding_seconds=20")

        assert jobs_response.status_code == 200
        assert curve_response.status_code == 200
        assert curve_response.json()["source"] == "high_res_memory"
    finally:
        sink.store.close()
