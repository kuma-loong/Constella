from __future__ import annotations

import pytest

from constella_tui.client import cluster_websocket_url


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
