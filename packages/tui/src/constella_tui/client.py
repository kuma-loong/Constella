from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from websockets.asyncio.client import connect


class ClusterConnectionError(RuntimeError):
    """Raised when a cluster stream cannot provide a valid snapshot."""


class ClusterAPIError(RuntimeError):
    """Raised when a manager HTTP endpoint cannot provide JSON data."""


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


def manager_http_url(manager_url: str) -> str:
    """Normalize any supported manager URL to an HTTP base URL."""
    websocket_url = cluster_websocket_url(manager_url)
    parsed = urlparse(websocket_url)
    scheme = {"ws": "http", "wss": "https"}[parsed.scheme]
    path = parsed.path.removesuffix("/ws/cluster").rstrip("/")
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


class ClusterClient:
    """Small WebSocket client for Constella's existing cluster stream."""

    def __init__(self, manager_url: str, *, open_timeout: float = 5.0) -> None:
        self.websocket_url = cluster_websocket_url(manager_url)
        self.http_url = manager_http_url(manager_url)
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

    async def get_json(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> dict[str, object]:
        """Fetch a read-only manager endpoint without adding another dependency."""
        normalized_path = "/" + path.lstrip("/")
        query = f"?{urlencode(params)}" if params else ""
        url = f"{self.http_url}{normalized_path}{query}"

        def fetch() -> dict[str, object]:
            request = Request(url, headers={"Accept": "application/json"})
            try:
                with urlopen(request, timeout=timeout) as response:
                    payload = json.load(response)
            except HTTPError as exc:
                raise ClusterAPIError(f"manager returned HTTP {exc.code}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                raise ClusterAPIError(str(exc.reason if isinstance(exc, URLError) else exc)) from exc
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ClusterAPIError("manager returned invalid JSON") from exc
            if not isinstance(payload, dict):
                raise ClusterAPIError("manager returned an invalid API payload")
            return payload

        return await asyncio.to_thread(fetch)
