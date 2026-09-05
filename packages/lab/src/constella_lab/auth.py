from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

import jwt
from jwt import PyJWKClient
from starlette.datastructures import Headers
from starlette.websockets import WebSocketDisconnect

from .config import LabConfig
from .store import LabStore


@dataclass(frozen=True, slots=True)
class AccessIdentity:
    issuer: str
    subject: str
    email: str
    expires_at: float


class IdentityVerifier(Protocol):
    async def verify(self, token: str) -> AccessIdentity: ...


class CloudflareAccessVerifier:
    def __init__(self, *, team_domain: str, audience: str) -> None:
        self.team_domain = team_domain.rstrip("/")
        self.audience = audience
        self._jwks = PyJWKClient(
            f"{self.team_domain}/cdn-cgi/access/certs",
            cache_keys=True,
            lifespan=3600,
        )

    async def verify(self, token: str) -> AccessIdentity:
        return await asyncio.to_thread(self._verify_sync, token)

    def _verify_sync(self, token: str) -> AccessIdentity:
        signing_key = self._jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.audience,
            issuer=self.team_domain,
            leeway=60,
            options={"require": ["exp", "iat", "iss", "sub", "aud", "email", "type"]},
        )
        if claims.get("type") != "app":
            raise jwt.InvalidTokenError("unexpected Access token type")
        subject = claims.get("sub")
        email = claims.get("email")
        if not isinstance(subject, str) or not subject:
            raise jwt.InvalidTokenError("missing Access subject")
        if not isinstance(email, str) or not email:
            raise jwt.InvalidTokenError("missing Access email")
        return AccessIdentity(
            issuer=str(claims["iss"]),
            subject=subject,
            email=email,
            expires_at=float(claims["exp"]),
        )


class LabAuthMiddleware:
    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        config: LabConfig,
        store: LabStore,
        verifier: IdentityVerifier,
    ) -> None:
        self.app = app
        self.config = config
        self.store = store
        self.verifier = verifier

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if _is_machine_or_health_path(path):
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        headers = Headers(scope=scope)
        token = headers.get("cf-access-jwt-assertion", "")
        if not token:
            await self._reject(scope, send, 401, "access_identity_required", request_id)
            return
        try:
            identity = await self.verifier.verify(token)
        except Exception:
            await self._reject(scope, send, 401, "invalid_access_identity", request_id)
            return
        try:
            user = self.store.resolve_identity(
                issuer=identity.issuer,
                subject=identity.subject,
                email=identity.email,
                bootstrap_admin_email=self.config.bootstrap_admin_email,
                request_id=request_id,
            )
        except ValueError:
            await self._reject(scope, send, 401, "invalid_access_identity", request_id)
            return
        if user["status"] != "active":
            await self._reject(scope, send, 403, "user_not_active", request_id)
            return
        if _admin_only_path(scope, path) and user["role"] != "admin":
            await self._reject(scope, send, 403, "admin_required", request_id)
            return
        if scope["type"] == "http" and scope.get("method") in {"POST", "PATCH", "DELETE"}:
            origin = headers.get("origin", "").rstrip("/")
            marker = headers.get("x-constella-request", "")
            if origin != self.config.public_origin or marker != "same-origin":
                await self._reject(scope, send, 403, "csrf_check_failed", request_id)
                return

        scope["state"]["lab_user"] = user
        scope["state"]["access_expires_at"] = identity.expires_at

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.extend(
                    [
                        (b"x-request-id", request_id.encode("ascii")),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"same-origin"),
                        (
                            b"content-security-policy",
                            b"frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
                        ),
                    ]
                )
                message["headers"] = response_headers
            await send(message)

        if scope["type"] == "websocket":
            await self._run_websocket(
                scope,
                receive,
                send_with_headers,
                send,
                user_id=user["id"],
                expires_at=identity.expires_at,
            )
            return
        await self.app(scope, receive, send_with_headers)

    async def _run_websocket(
        self,
        scope: dict[str, Any],
        receive: Any,
        protected_send: Any,
        raw_send: Any,
        *,
        user_id: str,
        expires_at: float,
    ) -> None:
        deadline = min(time.monotonic() + 900, time.monotonic() + max(0, expires_at - time.time()))
        app_task = asyncio.create_task(
            self.app(scope, receive, protected_send), name="lab-authenticated-websocket"
        )
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                done, _pending = await asyncio.wait(
                    {app_task}, timeout=min(60, remaining)
                )
                if app_task in done:
                    await app_task
                    return
                latest_user = self.store.user_with_bindings(user_id)
                if latest_user is None or latest_user["status"] != "active":
                    break
            await raw_send({"type": "websocket.close", "code": 4403})
        finally:
            app_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect):
                await app_task

    async def _reject(
        self,
        scope: dict[str, Any],
        send: Any,
        status: int,
        code: str,
        request_id: str,
    ) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401 if status == 401 else 4403})
            return
        body = json.dumps({"error": code, "request_id": request_id}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                    (b"x-request-id", request_id.encode("ascii")),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _is_machine_or_health_path(path: str) -> bool:
    return path in {"/api/health", "/api/agents/ws", "/api/highres/stream"}


def _admin_only_path(scope: dict[str, Any], path: str) -> bool:
    if (
        path.startswith("/api/lab/admin")
        or path.startswith("/api/docs")
        or path == "/openapi.json"
    ):
        return True
    return path == "/api/settings" and scope.get("method") == "PATCH"
