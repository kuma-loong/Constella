from __future__ import annotations

from fastapi.testclient import TestClient

from constella.app import create_app
from constella.collector import SnapshotCollector


def test_settings_api_get_and_patch() -> None:
    collector = SnapshotCollector(refresh_interval=1.0, process_interval=3.0)
    client = TestClient(create_app(collector=collector))

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
    assert collector.refresh_interval == 0.5


def test_settings_api_rejects_unsupported_refresh_interval() -> None:
    collector = SnapshotCollector(refresh_interval=1.0, process_interval=3.0)
    client = TestClient(create_app(collector=collector))

    response = client.patch("/api/settings", json={"refresh_interval": 3.0})

    assert response.status_code == 400
    assert collector.refresh_interval == 1.0
