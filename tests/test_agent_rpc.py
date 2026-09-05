from __future__ import annotations

import asyncio

import pytest

from constella.agent_rpc import (
    AgentCapabilityError,
    AgentRpcBroker,
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
