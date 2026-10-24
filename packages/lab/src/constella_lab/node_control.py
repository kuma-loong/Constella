from __future__ import annotations

import socket
from typing import Any

from fastapi import APIRouter, Request

from constella.agent_rpc import (
    AgentCapabilityError,
    AgentRpcError,
    AgentRpcTimeoutError,
    AgentUnavailableError,
)

from .api import SlidingWindowLimiter, _error, _request_id, _require_admin
from .store import LabStore


def build_node_control_router(store: LabStore) -> APIRouter:
    router = APIRouter(prefix="/api/lab/admin/nodes")
    limiter = SlidingWindowLimiter()

    @router.get("")
    async def nodes(request: Request) -> dict[str, Any]:
        _require_admin(request)
        cluster = request.app.state.cluster_state
        broker = request.app.state.agent_rpc
        snapshot = cluster.snapshot()
        return {
            "nodes": [
                {
                    "node_id": node.node_id,
                    "hostname": node.hostname,
                    "status": node.status,
                    "connected": broker.capabilities(node.node_id) is not None,
                    "restart_supported": bool(
                        (broker.capabilities(node.node_id) or {}).get("agent_restart_v1") is True
                    ),
                    "manager_host": node.hostname == socket.gethostname(),
                    "agent_version": node.agent_version,
                    "agent_instance_id": (broker.capabilities(node.node_id) or {}).get(
                        "agent_instance_id"
                    ),
                    "last_seen_at": node.received_at,
                }
                for node in sorted(snapshot.nodes, key=lambda item: item.node_id)
            ],
            "request_id": _request_id(request),
        }

    @router.post("/{node_id:path}/restart", status_code=202)
    async def restart(node_id: str, request: Request) -> dict[str, Any]:
        actor = _require_admin(request)
        broker = request.app.state.agent_rpc
        capabilities = broker.capabilities(node_id)
        if capabilities is None:
            raise _error(409, "node_offline", request)
        if capabilities.get("agent_restart_v1") is not True:
            raise _error(409, "agent_restart_unsupported", request)
        if not limiter.consume((node_id, "restart"), amount=1, limit=1, window=60):
            raise _error(429, "agent_restart_cooldown", request)

        def audit(outcome: str) -> None:
            store.audit(
                actor_user_id=actor["id"], action=f"agent.restart_{outcome}",
                target_type="node", target_id=node_id, request_id=_request_id(request),
                details={"scope": "agent"},
            )

        # Persist intent before dispatching so an interrupted request remains traceable.
        audit("requested")
        try:
            response = await broker.restart(node_id, timeout=5.0)
        except AgentRpcTimeoutError as exc:
            audit("unconfirmed")
            raise _error(504, "agent_restart_unconfirmed", request) from exc
        except (AgentUnavailableError, AgentCapabilityError) as exc:
            audit("unconfirmed")
            raise _error(409, "agent_restart_unconfirmed", request) from exc
        except AgentRpcError as exc:
            audit("unconfirmed")
            raise _error(502, "agent_restart_unconfirmed", request) from exc
        if response.get("ok") is not True:
            audit("rejected")
            raise _error(502, "agent_restart_rejected", request)
        audit("accepted")
        return {"node_id": node_id, "status": "accepted", "request_id": _request_id(request)}

    return router
