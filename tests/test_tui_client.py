from __future__ import annotations

import pytest

import asyncio
import io

from constella_tui import client as client_module
from constella_tui.client import ClusterAPIError, ClusterClient, cluster_websocket_url, manager_http_url


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("127.0.0.1:8765", "ws://127.0.0.1:8765/ws/cluster"),
        ("http://127.0.0.1:8765", "ws://127.0.0.1:8765/ws/cluster"),
        ("https://gpu.example.com", "wss://gpu.example.com/ws/cluster"),
        ("ws://gpu.example.com/ws/cluster", "ws://gpu.example.com/ws/cluster"),
        ("https://example.com/constella/", "wss://example.com/constella/ws/cluster"),
    ],
)
def test_cluster_websocket_url(source: str, expected: str) -> None:
    assert cluster_websocket_url(source) == expected


@pytest.mark.parametrize("source", ["", "ftp://example.com", "http:///missing-host"])
def test_cluster_websocket_url_rejects_invalid_values(source: str) -> None:
    with pytest.raises(ValueError):
        cluster_websocket_url(source)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("ws://127.0.0.1:8765/ws/cluster", "http://127.0.0.1:8765"),
        ("https://gpu.example.com", "https://gpu.example.com"),
        ("wss://example.com/constella/ws/cluster", "https://example.com/constella"),
    ],
)
def test_manager_http_url(source: str, expected: str) -> None:
    assert manager_http_url(source) == expected


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_cluster_client_fetches_analytics_json(monkeypatch) -> None:
    def fake_urlopen(request, *, timeout):
        assert request.full_url == "http://manager:8765/api/analytics/overview?range=7d"
        assert timeout == 3.0
        return FakeResponse(b'{"enabled": true}')

    monkeypatch.setattr(client_module, "urlopen", fake_urlopen)
    client = ClusterClient("http://manager:8765")
    result = asyncio.run(
        client.get_json("/api/analytics/overview", params={"range": "7d"}, timeout=3.0)
    )
    assert result == {"enabled": True}


def test_cluster_client_rejects_non_object_json(monkeypatch) -> None:
    monkeypatch.setattr(
        client_module,
        "urlopen",
        lambda _request, *, timeout: FakeResponse(b"[]"),
    )
    client = ClusterClient("http://manager:8765")
    with pytest.raises(ClusterAPIError, match="invalid API payload"):
        asyncio.run(client.get_json("/api/analytics/overview"))
