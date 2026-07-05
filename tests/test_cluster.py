from __future__ import annotations

from constella.cluster import AgentHello, ClusterState, parse_agent_hello


def sample_message(node_id: str, seq: int, util: int = 50) -> dict[str, object]:
    return {
        "type": "sample",
        "schema_version": 1,
        "node_id": node_id,
        "seq": seq,
        "sampled_at": 100.0 + seq,
        "refresh_interval": 1.0,
        "process_interval": 3.0,
        "snapshot": {
            "ok": True,
            "source": "test",
            "hostname": f"{node_id}-host",
            "timestamp": 100.0 + seq,
            "elapsed_ms": 2.0,
            "gpus": [
                {
                    "index": 0,
                    "uuid": "GPU-abc",
                    "name": "NVIDIA Test",
                    "utilization_gpu": util,
                    "memory_total_mb": 100,
                    "memory_used_mb": 25,
                    "processes": [
                        {
                            "pid": 123,
                            "name": "python",
                            "task_name": "train.py",
                            "gpu_memory_mb": 25,
                            "ppid": 42,
                            "kind": "compute",
                            "process_start_time": 90.0,
                            "parent_start_time": 80.0,
                        }
                    ],
                }
            ],
        },
    }


def test_cluster_state_registers_sample_and_drops_old_seq() -> None:
    state = ClusterState(local_node_id="manager")
    state.register_hello(AgentHello(node_id="node-a", hostname="host-a"), now=10.0)

    assert state.ingest_sample(sample_message("node-a", 2, util=40), received_at=12.0)
    assert not state.ingest_sample(sample_message("node-a", 1, util=99), received_at=13.0)

    cluster = state.snapshot(now=13.0)
    node = cluster.nodes[0]
    assert node.node_id == "node-a"
    assert node.seq == 2
    assert node.status == "online"
    assert node.gpus[0].gpu_id == "node-a:GPU-abc"
    assert node.gpus[0].utilization_gpu == 40
    assert node.gpus[0].processes[0].ppid == 42
    assert node.gpus[0].processes[0].parent_start_time == 80.0
    assert node.history["node-a:GPU-abc"]["gpu"] == [40.0]
    assert node.history["node-a:GPU-abc"]["memory"] == [25.0]


def test_cluster_state_builds_short_history_from_samples_without_agent_history() -> None:
    state = ClusterState(local_node_id="manager", history_size=2)
    state.register_hello(AgentHello(node_id="node-a", hostname="host-a"), now=10.0)

    for seq, util in ((1, 40), (2, 55), (3, 70)):
        message = sample_message("node-a", seq, util=util)
        assert "history" not in message["snapshot"]
        assert state.ingest_sample(message, received_at=10.0 + seq)

    node = state.snapshot(now=14.0).nodes[0]
    history = node.history["node-a:GPU-abc"]
    assert history["gpu"] == [55.0, 70.0]
    assert history["memory"] == [25.0, 25.0]
    assert history["power"] == [0.0, 0.0]
    assert history["temperature"] == [0.0, 0.0]


def test_cluster_state_uses_same_history_path_for_local_and_remote_agents() -> None:
    state = ClusterState(local_node_id="manager")
    state.register_hello(AgentHello(node_id="manager", hostname="manager-host"), now=10.0)
    state.register_hello(AgentHello(node_id="node-a", hostname="node-a-host"), now=10.0)

    assert state.ingest_sample(sample_message("manager", 1, util=35), received_at=11.0)
    assert state.ingest_sample(sample_message("node-a", 1, util=65), received_at=11.0)

    cluster = state.snapshot(now=12.0)
    histories = {node.node_id: node.history for node in cluster.nodes}
    assert histories["manager"]["manager:GPU-abc"]["gpu"] == [35.0]
    assert histories["node-a"]["node-a:GPU-abc"]["gpu"] == [65.0]


def test_cluster_state_marks_stale_offline_and_disconnect() -> None:
    state = ClusterState(local_node_id="manager", stale_after=5.0, offline_after=30.0)
    state.register_hello(AgentHello(node_id="node-a", hostname="host-a"), now=10.0)
    state.ingest_sample(sample_message("node-a", 1), received_at=10.0)

    assert state.snapshot(now=16.0).nodes[0].status == "stale"
    assert state.snapshot(now=41.0).nodes[0].status == "offline"

    state.ingest_heartbeat("node-a", seq=3, now=42.0)
    assert state.snapshot(now=42.1).nodes[0].status == "online"

    state.disconnect("node-a", now=43.0)
    assert state.snapshot(now=43.0).nodes[0].status == "offline"


def test_cluster_state_accepts_samples_after_agent_reconnect_resets_seq() -> None:
    state = ClusterState(local_node_id="manager")
    old_connection = object()
    new_connection = object()
    state.register_hello(
        AgentHello(node_id="node-a", hostname="host-a"),
        now=10.0,
        connection_id=old_connection,
    )
    assert state.ingest_sample(
        sample_message("node-a", 2508, util=40),
        received_at=11.0,
        connection_id=old_connection,
    )

    state.register_hello(
        AgentHello(node_id="node-a", hostname="host-a"),
        now=20.0,
        connection_id=new_connection,
    )
    state.disconnect("node-a", now=21.0, connection_id=old_connection)
    assert not state.ingest_sample(
        sample_message("node-a", 2509, util=99),
        received_at=22.0,
        connection_id=old_connection,
    )
    assert state.ingest_sample(
        sample_message("node-a", 1, util=55),
        received_at=23.0,
        connection_id=new_connection,
    )

    node = state.snapshot(now=23.0).nodes[0]
    assert node.status == "online"
    assert node.seq == 1
    assert node.gpus[0].utilization_gpu == 55


def test_cluster_state_keeps_static_hardware_from_hello() -> None:
    state = ClusterState(local_node_id="manager")
    hello = parse_agent_hello(
        {
            "type": "hello",
            "node_id": "node-a",
            "hostname": "host-a",
            "hardware": {
                "gpus": [
                    {
                        "index": 0,
                        "uuid": "GPU-abc",
                        "name": "NVIDIA H100 80GB HBM3",
                        "architecture": "Hopper",
                    }
                ]
            },
        }
    )

    state.register_hello(hello, now=10.0)
    state.ingest_sample(sample_message("node-a", 1), received_at=11.0)

    node = state.snapshot(now=11.0).nodes[0]
    assert node.hardware is not None
    assert node.hardware.gpus[0].architecture == "Hopper"
    assert not hasattr(node.gpus[0], "architecture")
    assert "hardware" not in node.to_dict()
    assert state.snapshot(now=11.0).to_dict()["nodes"][0]["hardware"]["gpus"][0]["architecture"] == "Hopper"
