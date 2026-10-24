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
        self._pending: dict[tuple[str, str, str], asyncio.Future[dict[str, Any]]] = {}

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
        return await self._request(
            node_id, capability="account_lookup_v1", request_type="account_lookup_request",
            response_type="account_lookup_response", payload={"username": username}, timeout=timeout,
        )

    async def restart(self, node_id: str, *, timeout: float = 5.0) -> dict[str, Any]:
        return await self._request(
            node_id, capability="agent_restart_v1", request_type="agent_restart_request",
            response_type="agent_restart_response", payload={}, timeout=timeout,
        )

    async def _request(
        self, node_id: str, *, capability: str, request_type: str, response_type: str,
        payload: dict[str, Any], timeout: float,
    ) -> dict[str, Any]:
        connection = self._connections.get(node_id)
        if connection is None:
            raise AgentUnavailableError(f"node {node_id!r} is not connected")
        if connection.capabilities.get(capability) is not True:
            raise AgentCapabilityError(f"node {node_id!r} does not support {capability}")

        request_id = uuid.uuid4().hex
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        key = (node_id, request_id, response_type)
        self._pending[key] = future
        try:
            connection.send_queue.put_nowait(
                {
                    "type": request_type,
                    "request_id": request_id,
                    "node_id": node_id,
                    **payload,
                }
            )
        except asyncio.QueueFull as exc:
            self._pending.pop(key, None)
            raise AgentUnavailableError(f"node {node_id!r} request queue is full") from exc

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise AgentRpcTimeoutError(f"node {node_id!r} {request_type} timed out") from exc
        finally:
            self._pending.pop(key, None)

    def resolve_account_lookup(
        self,
        message: dict[str, Any],
        *,
        connection_id: object,
    ) -> bool:
        if message.get("type") != "account_lookup_response":
            return False
        return self._resolve_response(message, connection_id=connection_id)

    def resolve_restart(self, message: dict[str, Any], *, connection_id: object) -> bool:
        if message.get("type") != "agent_restart_response":
            return False
        return self._resolve_response(message, connection_id=connection_id)

    def _resolve_response(self, message: dict[str, Any], *, connection_id: object) -> bool:
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
        future = self._pending.get((node_id, request_id, str(message.get("type") or "")))
        if future is None or future.done():
            return False
        future.set_result(dict(message))
        return True

    def _fail_pending(self, node_id: str, error: AgentRpcError) -> None:
        for (pending_node_id, _request_id, _response_type), future in list(self._pending.items()):
            if pending_node_id == node_id and not future.done():
                future.set_exception(error)
