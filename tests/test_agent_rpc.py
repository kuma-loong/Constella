from __future__ import annotations

import asyncio

import pytest

from constella.agent_rpc import (
    AgentCapabilityError,
    AgentRpcBroker,
    AgentRpcTimeoutError,
    AgentUnavailableError,
)


def test_account_lookup_round_trip() -> None:
    async def run() -> None:
        broker = AgentRpcBroker()
        connection_id = object()
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        broker.register(
            "node-a",
            connection_id=connection_id,
            send_queue=queue,
            capabilities={"account_lookup_v1": True},
        )

        pending = asyncio.create_task(broker.account_lookup("node-a", "alice"))
        request = await queue.get()
        assert request["type"] == "account_lookup_request"
        assert request["username"] == "alice"
        assert broker.resolve_account_lookup(
            {
                "type": "account_lookup_response",
                "request_id": request["request_id"],
                "node_id": "node-a",
                "ok": True,
                "exists": True,
                "canonical_username": "alice",
                "uid": 1001,
                "gid": 1001,
                "shell": "/bin/bash",
            },
            connection_id=connection_id,
        )
        result = await pending
        assert result["uid"] == 1001

    asyncio.run(run())


def test_account_lookup_rejects_legacy_and_disconnected_agents() -> None:
    async def run() -> None:
        broker = AgentRpcBroker()
        broker.register(
            "legacy",
            connection_id=object(),
            send_queue=asyncio.Queue(),
            capabilities={},
        )
        with pytest.raises(AgentCapabilityError):
            await broker.account_lookup("legacy", "alice")
        with pytest.raises(AgentUnavailableError):
            await broker.account_lookup("offline", "alice")

    asyncio.run(run())


def test_disconnect_fails_pending_lookup() -> None:
    async def run() -> None:
        broker = AgentRpcBroker()
        connection_id = object()
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        broker.register(
            "node-a",
            connection_id=connection_id,
            send_queue=queue,
            capabilities={"account_lookup_v1": True},
        )
        pending = asyncio.create_task(broker.account_lookup("node-a", "alice"))
        await queue.get()
        broker.unregister("node-a", connection_id=connection_id)
        with pytest.raises(AgentUnavailableError):
            await pending

    asyncio.run(run())


def test_restart_requires_matching_response_type_and_current_connection() -> None:
    async def run() -> None:
        broker = AgentRpcBroker()
        connection = object()
        queue = asyncio.Queue()
        broker.register("node-a", connection_id=connection, send_queue=queue,
                        capabilities={"agent_restart_v1": True})
        pending = asyncio.create_task(broker.restart("node-a"))
        request = await queue.get()
        assert request["type"] == "agent_restart_request"
        response = {**request, "type": "agent_restart_response", "ok": True}
        assert not broker.resolve_restart(response, connection_id=object())
        assert not broker.resolve_account_lookup(
            {**response, "type": "account_lookup_response"}, connection_id=connection,
        )
        assert not broker.resolve_restart({**response, "node_id": "other"}, connection_id=connection)
        assert not pending.done()
        assert broker.resolve_restart(response, connection_id=connection)
        assert (await pending)["ok"] is True
        assert not broker.resolve_restart(response, connection_id=connection)

    asyncio.run(run())


def test_restart_handles_legacy_disconnect_timeout_and_full_queue() -> None:
    async def run() -> None:
        broker = AgentRpcBroker()
        queue = asyncio.Queue(maxsize=1)
        connection = object()
        broker.register("node-a", connection_id=connection, send_queue=queue, capabilities={})
        with pytest.raises(AgentCapabilityError):
            await broker.restart("node-a")
        with pytest.raises(AgentUnavailableError):
            await broker.restart("missing")
        broker.register("node-a", connection_id=connection, send_queue=queue,
                        capabilities={"agent_restart_v1": True})
        with pytest.raises(AgentRpcTimeoutError):
            await broker.restart("node-a", timeout=0.001)
        assert not broker._pending
        with pytest.raises(AgentUnavailableError):
            await broker.restart("node-a")
        await queue.get()
        pending = asyncio.create_task(broker.restart("node-a"))
        await queue.get()
        broker.unregister("node-a", connection_id=connection)
        with pytest.raises(AgentUnavailableError):
            await pending
        assert not broker._pending

    asyncio.run(run())
