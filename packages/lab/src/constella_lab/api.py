from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from constella.agent_rpc import AgentCapabilityError, AgentRpcError, AgentUnavailableError

from .config import LabConfig
from .store import (
    BindingConflictError,
    LabStore,
    LabStoreError,
    LastAdminError,
)


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)


class AccountInput(BaseModel):
    node_id: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=256)


class AccountBatch(BaseModel):
    accounts: list[AccountInput] = Field(min_length=1, max_length=32)


class AdminUserUpdate(BaseModel):
    role: str | None = None
    status: str | None = None
    display_name: str | None = Field(default=None, max_length=80)


class IdentityMigration(BaseModel):
    pending_user_id: str
    reason: str = Field(min_length=1, max_length=500)


class BindingReassignment(BaseModel):
    user_id: str
    node_id: str
    username: str
    reason: str = Field(min_length=1, max_length=500)


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def consume(self, key: tuple[str, str], *, amount: int, limit: int, window: float) -> bool:
        now = time.monotonic()
        events = self._events[key]
        while events and events[0] <= now - window:
            events.popleft()
        if len(events) + amount > limit:
            return False
        events.extend([now] * amount)
        return True


def build_lab_router(*, config: LabConfig, store: LabStore) -> APIRouter:
    router = APIRouter(prefix="/api/lab")
    limiter = SlidingWindowLimiter()

    @router.get("/me")
    async def me(request: Request) -> dict[str, Any]:
        user = _user(request)
        return {"user": store.user_with_bindings(user["id"]), "request_id": _request_id(request)}

    @router.post("/me/readonly")
    async def choose_readonly(request: Request) -> dict[str, Any]:
        user = _user(request)
        try:
            updated = store.choose_readonly(user["id"], request_id=_request_id(request))
        except ValueError as exc:
            raise _error(409, "readonly_onboarding_unavailable", request) from exc
        return {"user": updated, "request_id": _request_id(request)}

    @router.patch("/me")
    async def update_me(update: ProfileUpdate, request: Request) -> dict[str, Any]:
        user = _require_member(request)
        updated = store.update_profile(user["id"], update.display_name)
        store.audit(
            actor_user_id=user["id"],
            action="user.profile_updated",
            target_type="user",
            target_id=user["id"],
            request_id=_request_id(request),
            details={},
        )
        return {"user": updated, "request_id": _request_id(request)}

    @router.get("/account-binding-nodes")
    async def binding_nodes(request: Request) -> dict[str, Any]:
        _require_member(request)
        cluster_state = request.app.state.cluster_state
        broker = request.app.state.agent_rpc
        nodes = []
        for node_id, runtime in sorted(cluster_state.latest_by_node.items()):
            capabilities = broker.capabilities(node_id)
            nodes.append(
                {
                    "node_id": node_id,
                    "hostname": runtime.hostname,
                    "status": runtime.snapshot.status,
                    "connected": runtime.connected,
                    "binding_supported": bool(
                        capabilities and capabilities.get("account_lookup_v1") is True
                    ),
                }
            )
        return {"nodes": nodes, "request_id": _request_id(request)}

    @router.post("/account-lookups/batch")
    async def preview_accounts(batch: AccountBatch, request: Request) -> dict[str, Any]:
        user = _require_member(request)
        _validate_unique_nodes(batch.accounts)
        if not limiter.consume(
            (user["id"], "lookup-minute"),
            amount=len(batch.accounts),
            limit=10,
            window=60,
        ) or not limiter.consume(
            (user["id"], "lookup-day"),
            amount=len(batch.accounts),
            limit=50,
            window=86400,
        ):
            raise _error(429, "account_lookup_rate_limited", request)
        results = await _lookup_accounts(
            request,
            config=config,
            store=store,
            user_id=user["id"],
            accounts=batch.accounts,
        )
        return {"results": results, "request_id": _request_id(request)}

    @router.post("/account-bindings/batch", status_code=201)
    async def create_bindings(batch: AccountBatch, request: Request) -> dict[str, Any]:
        user = _require_member(request)
        _validate_unique_nodes(batch.accounts)
        if not limiter.consume(
            (user["id"], "binding-day"), amount=1, limit=5, window=86400
        ):
            raise _error(429, "account_binding_rate_limited", request)
        results = await _lookup_accounts(
            request,
            config=config,
            store=store,
            user_id=user["id"],
            accounts=batch.accounts,
        )
        if any(not result["bindable"] for result in results):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "account_batch_not_bindable",
                    "results": results,
                    "request_id": _request_id(request),
                },
            )
        try:
            bindings = store.create_bindings(
                user_id=user["id"],
                accounts=results,
                actor_user_id=user["id"],
                request_id=_request_id(request),
            )
        except BindingConflictError as exc:
            raise _error(409, "account_binding_conflict", request) from exc
        return {"bindings": bindings, "request_id": _request_id(request)}

    @router.delete("/account-bindings/{binding_id}")
    async def delete_binding(binding_id: str, request: Request) -> dict[str, Any]:
        user = _require_member(request)
        ended = store.end_binding(
            binding_id,
            actor_user_id=user["id"],
            owner_user_id=user["id"],
            request_id=_request_id(request),
        )
        if not ended:
            raise _error(404, "binding_not_found", request)
        return {"ended": True, "request_id": _request_id(request)}

    @router.get("/admin/users")
    async def admin_users(request: Request, q: str | None = None) -> dict[str, Any]:
        _require_admin(request)
        return {"users": store.list_users(q), "request_id": _request_id(request)}

    @router.patch("/admin/users/{user_id}")
    async def admin_update_user(
        user_id: str, update: AdminUserUpdate, request: Request
    ) -> dict[str, Any]:
        actor = _require_admin(request)
        try:
            user = store.admin_update_user(
                user_id,
                actor_user_id=actor["id"],
                request_id=_request_id(request),
                role=update.role,
                status=update.status,
                display_name=update.display_name,
            )
        except LastAdminError as exc:
            raise _error(409, "last_active_admin", request) from exc
        except (ValueError, LabStoreError) as exc:
            raise _error(422, "invalid_user_update", request) from exc
        return {"user": user, "request_id": _request_id(request)}

    @router.post("/admin/users/{user_id}/migrate-identity")
    async def migrate_identity(
        user_id: str, migration: IdentityMigration, request: Request
    ) -> dict[str, Any]:
        actor = _require_admin(request)
        try:
            user = store.migrate_identity(
                user_id,
                pending_user_id=migration.pending_user_id,
                actor_user_id=actor["id"],
                request_id=_request_id(request),
                reason=migration.reason,
            )
        except (ValueError, LabStoreError) as exc:
            raise _error(422, "invalid_identity_migration", request) from exc
        return {"user": user, "request_id": _request_id(request)}

    @router.get("/admin/bindings")
    async def admin_bindings(request: Request) -> dict[str, Any]:
        _require_admin(request)
        return {"bindings": store.list_bindings(), "request_id": _request_id(request)}

    @router.post("/admin/bindings/reassign")
    async def reassign_binding(
        reassignment: BindingReassignment, request: Request
    ) -> dict[str, Any]:
        actor = _require_admin(request)
        results = await _lookup_accounts(
            request,
            config=config,
            store=store,
            user_id=reassignment.user_id,
            accounts=[
                AccountInput(node_id=reassignment.node_id, username=reassignment.username)
            ],
            ignore_binding_conflict=True,
        )
        if not results[0]["bindable"]:
            raise _error(422, "account_not_bindable", request)
        try:
            binding = store.reassign_binding(
                user_id=reassignment.user_id,
                account=results[0],
                actor_user_id=actor["id"],
                request_id=_request_id(request),
                reason=reassignment.reason,
            )
        except BindingConflictError as exc:
            raise _error(409, "account_binding_conflict", request) from exc
        return {"binding": binding, "request_id": _request_id(request)}

    @router.post("/admin/bindings/{binding_id}/verify")
    async def verify_binding(binding_id: str, request: Request) -> dict[str, Any]:
        actor = _require_admin(request)
        binding = store.verify_binding(
            binding_id,
            actor_user_id=actor["id"],
            request_id=_request_id(request),
        )
        if binding is None:
            raise _error(404, "binding_not_found", request)
        return {"binding": binding, "request_id": _request_id(request)}

    @router.get("/admin/audit-events")
    async def audit_events(
        request: Request, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        _require_admin(request)
        return {
            "events": store.list_audit_events(limit=limit, offset=offset),
            "request_id": _request_id(request),
        }

    return router


async def _lookup_accounts(
    request: Request,
    *,
    config: LabConfig,
    store: LabStore,
    user_id: str,
    accounts: list[AccountInput],
    ignore_binding_conflict: bool = False,
) -> list[dict[str, Any]]:
    username_re = re.compile(config.account_username_pattern)

    async def lookup(account: AccountInput) -> dict[str, Any]:
        base: dict[str, Any] = {"node_id": account.node_id, "bindable": False}
        if not username_re.fullmatch(account.username):
            return {**base, "error": "invalid_username"}
        try:
            result = await request.app.state.agent_rpc.account_lookup(
                account.node_id,
                account.username,
                timeout=config.account_lookup_timeout,
            )
        except AgentCapabilityError:
            return {**base, "error": "account_lookup_unsupported"}
        except AgentUnavailableError:
            return {**base, "error": "node_offline"}
        except AgentRpcError:
            return {**base, "error": "account_lookup_unavailable"}
        rejection_reason = _account_rejection_reason(result, config=config)
        if rejection_reason is not None:
            store.audit(
                actor_user_id=user_id,
                action="account.lookup_rejected",
                target_type="node",
                target_id=account.node_id,
                request_id=_request_id(request),
                details={"reason": rejection_reason},
            )
            return {**base, "error": _public_rejection_error(rejection_reason)}
        uid = int(result["uid"])
        if not ignore_binding_conflict and not store.can_bind(
            user_id=user_id, node_id=account.node_id, unix_uid=uid
        ):
            return {**base, "error": "account_already_bound"}
        return {
            **base,
            "bindable": True,
            "canonical_username": result["canonical_username"],
            "uid": uid,
            "gid": result.get("gid"),
        }

    return list(await asyncio.gather(*(lookup(account) for account in accounts)))


def _account_rejection_reason(result: dict[str, Any], *, config: LabConfig) -> str | None:
    if result.get("ok") is not True:
        return "lookup_failed"
    if result.get("exists") is not True:
        return "account_not_found"
    username = result.get("canonical_username")
    uid = result.get("uid")
    shell = str(result.get("shell") or "")
    if not isinstance(username, str) or username in config.account_deny_users:
        return "denied_username"
    if re.fullmatch(config.account_username_pattern, username) is None:
        return "denied_username"
    if not isinstance(uid, int) or not config.account_uid_min <= uid <= config.account_uid_max:
        return "denied_uid"
    if shell.endswith(("/nologin", "/false")):
        return "denied_shell"
    return None


def _public_rejection_error(reason: str) -> str:
    return {
        "lookup_failed": "account_lookup_failed",
        "account_not_found": "account_not_found",
        "denied_username": "username_not_allowed",
        "denied_uid": "uid_not_allowed",
        "denied_shell": "login_disabled",
    }.get(reason, "account_not_bindable")


def _validate_unique_nodes(accounts: list[AccountInput]) -> None:
    node_ids = [account.node_id for account in accounts]
    if len(node_ids) != len(set(node_ids)):
        raise HTTPException(status_code=422, detail="duplicate node_id")


def _user(request: Request) -> dict[str, Any]:
    return request.state.lab_user


def _require_member(request: Request) -> dict[str, Any]:
    user = _user(request)
    if user["role"] not in {"member", "admin"}:
        raise _error(403, "member_required", request)
    return user


def _require_admin(request: Request) -> dict[str, Any]:
    user = _user(request)
    if user["role"] != "admin":
        raise _error(403, "admin_required", request)
    return user


def _request_id(request: Request) -> str:
    return request.state.request_id


def _error(status: int, code: str, request: Request) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"code": code, "request_id": _request_id(request)},
    )
