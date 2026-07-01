from __future__ import annotations

from fastapi.testclient import TestClient

from constella.app import ManagerSettings, create_app
from constella.cluster import ClusterState


def test_settings_api_get_and_patch() -> None:
    settings = ManagerSettings(refresh_interval=1.0, _process_interval=3.0)
    client = TestClient(create_app(manager_settings=settings))

    response = client.get("/api/settings")

    assert response.status_code == 200
    assert response.json() == {
        "refresh_interval": 1.0,
        "allowed_refresh_intervals": [0.5, 1.0, 2.0, 5.0],
        "process_interval": 3.0,
    }

    response = client.patch("/api/settings", json={"refresh_interval": 0.5})

    assert response.status_code == 200
    assert response.json()["refresh_interval"] == 0.5
    assert settings.refresh_interval == 0.5


def test_settings_api_rejects_unsupported_refresh_interval() -> None:
    settings = ManagerSettings(refresh_interval=1.0, _process_interval=3.0)
    client = TestClient(create_app(manager_settings=settings))

    response = client.patch("/api/settings", json={"refresh_interval": 3.0})

    assert response.status_code == 400
    assert settings.refresh_interval == 1.0


def test_settings_patch_broadcasts_config_to_connected_agent() -> None:
    settings = ManagerSettings(refresh_interval=1.0, _process_interval=3.0)
    client = TestClient(
        create_app(
            cluster_state=ClusterState(local_node_id="manager"),
            agent_token="secret",
            manager_settings=settings,
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
            }
        )
        assert websocket.receive_json() == {
            "type": "config",
            "refresh_interval": 1.0,
            "process_interval": 3.0,
        }

        response = client.patch(
            "/api/settings",
            json={"refresh_interval": 2.0, "process_interval": 5.0},
        )

        assert response.status_code == 200
        assert websocket.receive_json() == {
            "type": "config",
            "refresh_interval": 2.0,
            "process_interval": 5.0,
        }
