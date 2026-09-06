from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

from constella.cluster import AgentHello, ClusterState
from constella_lab.app import create_lab_app
from constella_lab.auth import AccessIdentity
from constella_lab.config import LabConfig


@dataclass
class MutableVerifier:
    identity: AccessIdentity

    async def verify(self, token: str) -> AccessIdentity:
        if token != "valid":
            raise ValueError("invalid token")
        return self.identity


class FakeBroker:
    def __init__(self, accounts: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.accounts = accounts

    def capabilities(self, node_id: str) -> dict[str, Any]:
        return {"account_lookup_v1": True}

    async def account_lookup(
        self, node_id: str, username: str, *, timeout: float
    ) -> dict[str, Any]:
        await asyncio.sleep(0)
        return self.accounts.get(
            (node_id, username),
            {"ok": True, "exists": False},
        )


def identity(subject: str, email: str) -> AccessIdentity:
    return AccessIdentity(
        issuer="https://test.cloudflareaccess.com",
        subject=subject,
        email=email,
        expires_at=time.time() + 3600,
    )


def config(tmp_path) -> LabConfig:
    return LabConfig(
        db_path=tmp_path / "lab" / "identity.sqlite3",
        access_team_domain="https://test.cloudflareaccess.com",
        access_audience="test-aud",
        bootstrap_admin_email="admin@example.com",
        public_origin="https://gpu.example.com",
    )


def headers(*, csrf: bool = False) -> dict[str, str]:
    result = {"cf-access-jwt-assertion": "valid"}
    if csrf:
        result.update(
            {
                "origin": "https://gpu.example.com",
                "x-constella-request": "same-origin",
            }
        )
    return result


def test_lab_requires_access_identity_and_bootstraps_admin(tmp_path) -> None:
    verifier = MutableVerifier(identity("admin-sub", "admin@example.com"))
    app = create_lab_app(config=config(tmp_path), verifier=verifier)

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        denied = client.get("/api/cluster/snapshot")
        assert denied.status_code == 401
        assert denied.json()["error"] == "access_identity_required"

        response = client.get("/api/lab/me", headers=headers())

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "admin"
    assert response.json()["user"]["status"] == "active"
    assert response.json()["user"]["onboarding_completed_at"] is None
    assert response.headers["x-content-type-options"] == "nosniff"
    assert (tmp_path / "lab" / "identity.sqlite3").stat().st_mode & 0o777 == 0o600


def test_member_cannot_change_settings_and_csrf_is_required(tmp_path) -> None:
    verifier = MutableVerifier(identity("admin-sub", "admin@example.com"))
    app = create_lab_app(config=config(tmp_path), verifier=verifier)

    with TestClient(app) as client:
        assert client.get("/api/lab/me", headers=headers()).status_code == 200
        verifier.identity = identity("member-sub", "member@example.com")
        assert client.get("/api/lab/me", headers=headers()).status_code == 200

        denied = client.patch(
            "/api/settings", json={"refresh_interval": 2}, headers=headers(csrf=True)
        )
        no_csrf = client.patch("/api/lab/me", json={}, headers=headers())
        updated_profile = client.patch(
            "/api/lab/me",
            json={"display_name": "Lab Member"},
            headers=headers(csrf=True),
        )

    assert denied.status_code == 403
    assert denied.json()["error"] == "admin_required"
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"] == "csrf_check_failed"
    assert updated_profile.status_code == 200
    assert updated_profile.json()["user"]["display_name"] == "Lab Member"


def test_viewer_cannot_query_or_bind_node_accounts(tmp_path) -> None:
    verifier = MutableVerifier(identity("admin-sub", "admin@example.com"))
    app = create_lab_app(config=config(tmp_path), verifier=verifier)

    with TestClient(app) as client:
        admin = client.get("/api/lab/me", headers=headers()).json()["user"]
        verifier.identity = identity("viewer-sub", "viewer@example.com")
        viewer = client.get("/api/lab/me", headers=headers()).json()["user"]
        response = client.patch(
            f"/api/lab/admin/users/{viewer['id']}",
            json={"role": "viewer"},
            headers=headers(csrf=True),
        )
        assert response.status_code == 403

        verifier.identity = identity("admin-sub", "admin@example.com")
        assert client.patch(
            f"/api/lab/admin/users/{viewer['id']}",
            json={"role": "viewer"},
            headers=headers(csrf=True),
        ).status_code == 200

        verifier.identity = identity("viewer-sub", "viewer@example.com")
        denied = client.post(
            "/api/lab/account-lookups/batch",
            json={"accounts": [{"node_id": "node-a", "username": "viewer"}]},
            headers=headers(csrf=True),
        )

    assert admin["role"] == "admin"
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "member_required"


def test_multi_node_binding_is_rechecked_and_created_atomically(tmp_path) -> None:
    verifier = MutableVerifier(identity("admin-sub", "admin@example.com"))
    cluster = ClusterState(local_node_id="manager")
    cluster.register_hello(AgentHello(node_id="node-a", hostname="host-a"))
    cluster.register_hello(AgentHello(node_id="node-b", hostname="host-b"))
    app = create_lab_app(
        config=config(tmp_path),
        verifier=verifier,
        cluster_state=cluster,
    )

    with TestClient(app) as client:
        app.state.agent_rpc = FakeBroker(
            {
                ("node-a", "alice"): {
                    "ok": True,
                    "exists": True,
                    "canonical_username": "alice",
                    "uid": 1001,
                    "gid": 1001,
                    "shell": "/bin/bash",
                },
                ("node-b", "alice-b"): {
                    "ok": True,
                    "exists": True,
                    "canonical_username": "alice-b",
                    "uid": 2001,
                    "gid": 2001,
                    "shell": "/bin/zsh",
                },
            }
        )
        payload = {
            "accounts": [
                {"node_id": "node-a", "username": "alice"},
                {"node_id": "node-b", "username": "alice-b"},
            ]
        }
        preview = client.post(
            "/api/lab/account-lookups/batch", json=payload, headers=headers(csrf=True)
        )
        created = client.post(
            "/api/lab/account-bindings/batch", json=payload, headers=headers(csrf=True)
        )
        me = client.get("/api/lab/me", headers=headers())

    assert preview.status_code == 200
    assert all(item["bindable"] for item in preview.json()["results"])
    assert created.status_code == 201
    assert len(created.json()["bindings"]) == 2
    assert me.json()["user"]["onboarding_completed_at"] is not None
    assert {(item["node_id"], item["unix_uid"]) for item in me.json()["user"]["bindings"]} == {
        ("node-a", 1001),
        ("node-b", 2001),
    }


def test_failed_multi_node_recheck_creates_no_bindings(tmp_path) -> None:
    verifier = MutableVerifier(identity("admin-sub", "admin@example.com"))
    app = create_lab_app(config=config(tmp_path), verifier=verifier)

    with TestClient(app) as client:
        app.state.agent_rpc = FakeBroker(
            {
                ("node-a", "alice"): {
                    "ok": True,
                    "exists": True,
                    "canonical_username": "alice",
                    "uid": 1001,
                    "gid": 1001,
                    "shell": "/bin/bash",
                }
            }
        )
        response = client.post(
            "/api/lab/account-bindings/batch",
            json={
                "accounts": [
                    {"node_id": "node-a", "username": "alice"},
                    {"node_id": "node-b", "username": "missing"},
                ]
            },
            headers=headers(csrf=True),
        )
        me = client.get("/api/lab/me", headers=headers())

    assert response.status_code == 422
    assert me.json()["user"]["bindings"] == []
