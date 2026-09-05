from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any


class AgentRpcError(RuntimeError):
    """Base error for manager-to-agent requests."""


class AgentUnavailableError(AgentRpcError):
    pass


class AgentCapabilityError(AgentRpcError):
    pass


class AgentRpcTimeoutError(AgentRpcError):
    pass


@dataclass(slots=True)
class AgentConnection:
    connection_id: object
    send_queue: asyncio.Queue[dict[str, object]]
    capabilities: dict[str, Any]


class AgentRpcBroker:
    """Tracks live agents and correlates small manager-to-agent RPC calls."""

    def __init__(self) -> None:
        self._connections: dict[str, AgentConnection] = {}
        self._pending: dict[tuple[str, str], asyncio.Future[dict[str, Any]]] = {}

    def register(
        self,
        node_id: str,
        *,
        connection_id: object,
        send_queue: asyncio.Queue[dict[str, object]],
        capabilities: dict[str, Any] | None,
    ) -> None:
        previous = self._connections.get(node_id)
        if previous is not None and previous.connection_id is not connection_id:
            self._fail_pending(node_id, AgentUnavailableError("agent connection was replaced"))
        self._connections[node_id] = AgentConnection(
            connection_id=connection_id,
            send_queue=send_queue,
            capabilities=dict(capabilities or {}),
        )

    def unregister(self, node_id: str, *, connection_id: object) -> None:
        connection = self._connections.get(node_id)
        if connection is None or connection.connection_id is not connection_id:
            return
        self._connections.pop(node_id, None)
        self._fail_pending(node_id, AgentUnavailableError("agent disconnected"))

    def nodes_with_capability(self, capability: str) -> list[str]:
        return sorted(
            node_id
            for node_id, connection in self._connections.items()
            if connection.capabilities.get(capability) is True
        )

    def capabilities(self, node_id: str) -> dict[str, Any] | None:
        connection = self._connections.get(node_id)
        return dict(connection.capabilities) if connection is not None else None

    async def account_lookup(
        self,
        node_id: str,
        username: str,
        *,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        connection = self._connections.get(node_id)
        if connection is None:
            raise AgentUnavailableError(f"node {node_id!r} is not connected")
        if connection.capabilities.get("account_lookup_v1") is not True:
            raise AgentCapabilityError(f"node {node_id!r} does not support account lookup")

        request_id = uuid.uuid4().hex
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        key = (node_id, request_id)
        self._pending[key] = future
        try:
            connection.send_queue.put_nowait(
                {
                    "type": "account_lookup_request",
                    "request_id": request_id,
                    "node_id": node_id,
                    "username": username,
                }
            )
        except asyncio.QueueFull as exc:
            self._pending.pop(key, None)
            raise AgentUnavailableError(f"node {node_id!r} request queue is full") from exc

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise AgentRpcTimeoutError(f"node {node_id!r} account lookup timed out") from exc
        finally:
            self._pending.pop(key, None)

    def resolve_account_lookup(
        self,
        message: dict[str, Any],
        *,
        connection_id: object,
    ) -> bool:
        node_id = str(message.get("node_id") or "")
        request_id = str(message.get("request_id") or "")
        connection = self._connections.get(node_id)
        if (
            not node_id
            or not request_id
            or connection is None
            or connection.connection_id is not connection_id
        ):
            return False
        future = self._pending.get((node_id, request_id))
        if future is None or future.done():
            return False
        future.set_result(dict(message))
        return True

    def _fail_pending(self, node_id: str, error: AgentRpcError) -> None:
        for (pending_node_id, _request_id), future in list(self._pending.items()):
            if pending_node_id == node_id and not future.done():
                future.set_exception(error)
