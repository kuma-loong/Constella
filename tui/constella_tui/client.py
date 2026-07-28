from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from urllib.parse import urlparse, urlunparse

from websockets.asyncio.client import connect


class ClusterConnectionError(RuntimeError):
    """Raised when a cluster stream cannot provide a valid snapshot."""


def cluster_websocket_url(manager_url: str) -> str:
    """Convert a manager HTTP or WebSocket URL to the cluster stream URL."""
    value = manager_url.strip()
    if not value:
        raise ValueError("manager URL cannot be empty")
    if "://" not in value:
        value = f"http://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        raise ValueError(f"invalid manager URL: {manager_url}")

    scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
    path = parsed.path.rstrip("/")
    if not path or path == "/":
        path = "/ws/cluster"
    elif not path.endswith("/ws/cluster"):
        path = f"{path}/ws/cluster"
    return urlunparse((scheme, parsed.netloc, path, "", parsed.query, ""))


class ClusterClient:
    """Small WebSocket client for Constella's existing cluster stream."""

    def __init__(self, manager_url: str, *, open_timeout: float = 5.0) -> None:
        self.websocket_url = cluster_websocket_url(manager_url)
        self.open_timeout = open_timeout

    async def snapshots(self) -> AsyncIterator[dict[str, object]]:
        try:
            async with connect(
                self.websocket_url,
                open_timeout=self.open_timeout,
                ping_interval=20,
                ping_timeout=20,
                max_size=8 * 1024 * 1024,
            ) as websocket:
                async for message in websocket:
                    try:
                        payload = json.loads(message)
                    except (json.JSONDecodeError, TypeError) as exc:
                        raise ClusterConnectionError("manager returned invalid JSON") from exc
                    if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
                        raise ClusterConnectionError("manager returned an invalid cluster snapshot")
                    yield payload
        except asyncio.CancelledError:
            raise
        except ClusterConnectionError:
            raise
        except Exception as exc:
            raise ClusterConnectionError(str(exc) or exc.__class__.__name__) from exc
