from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from constella.app import create_app
from constella.cluster import ClusterState


def test_agent_websocket_updates_cluster_snapshot() -> None:
    cluster_state = ClusterState(local_node_id="manager")
    client = TestClient(
        create_app(
            cluster_state=cluster_state,
            agent_token="secret",
        )
    )

    with client.websocket_connect(
        "/api/agents/ws",
        headers={"authorization": "Bearer secret"},
    ) as websocket:
        websocket.send_json(
            {
                "type": "hello",
                "schema_version": 1,
                "node_id": "node-a",
                "hostname": "node-a-host",
                "agent_version": "0.2.0",
                "capabilities": {"nvml": True},
            }
        )
        assert websocket.receive_json()["type"] == "config"

        websocket.send_json(
            {
                "type": "sample",
                "schema_version": 1,
                "node_id": "node-a",
                "seq": 1,
                "sampled_at": 10.0,
                "refresh_interval": 1.0,
                "process_interval": 3.0,
                "snapshot": {
                    "ok": True,
                    "source": "test",
                    "hostname": "node-a-host",
                    "timestamp": 10.0,
                    "elapsed_ms": 2.0,
                    "gpus": [
                        {
                            "index": 0,
                            "uuid": "GPU-a",
                            "name": "NVIDIA Test",
                            "memory_total_mb": 100,
                            "memory_used_mb": 20,
                        }
                    ],
                },
            }
        )
        ack = websocket.receive_json()

    assert ack == {"type": "ack", "seq": 1, "accepted": True}
    response = client.get("/api/cluster/snapshot")
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["node_count"] == 1
    assert payload["nodes"][0]["node_id"] == "node-a"
    assert payload["nodes"][0]["gpus"][0]["gpu_id"] == "node-a:GPU-a"


def test_deprecated_single_node_http_api_returns_gone() -> None:
    client = TestClient(create_app(cluster_state=ClusterState(local_node_id="manager")))

    response = client.get("/api/snapshot")

    assert response.status_code == 410
    assert "/api/cluster/snapshot" in response.json()["detail"]


def test_deprecated_single_node_websocket_closes() -> None:
    client = TestClient(create_app(cluster_state=ClusterState(local_node_id="manager")))

    with client.websocket_connect("/ws/gpu") as websocket:
        try:
            websocket.receive_json()
            raise AssertionError("deprecated websocket should close immediately")
        except WebSocketDisconnect as exc:
            assert exc.code == 1008
