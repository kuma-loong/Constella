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
    assert response.json()["detail"]["results"][1]["error"] == "account_not_found"
    assert me.json()["user"]["bindings"] == []


def test_personal_activity_reads_bound_uid_intervals_and_isolates_users(tmp_path, monkeypatch) -> None:
    from constella.db import AsyncDBSink, SQLiteSinkConfig
    from constella.schema import GpuInfo, GpuProcess, NodeSnapshot, node_totals_from_gpus

    verifier = MutableVerifier(identity("admin-sub", "admin@example.com"))
    sink = AsyncDBSink(SQLiteSinkConfig(path=tmp_path / "telemetry.sqlite3"))
    app = create_lab_app(config=config(tmp_path), verifier=verifier, db_sink=sink)
    start = time.time() - 5 * 3600
    with TestClient(app) as client:
        user = client.get("/api/lab/me", headers=headers()).json()["user"]
        store = app.state.lab_store
        store.create_bindings(user_id=user["id"], actor_user_id=user["id"], request_id="test", accounts=[
            {"node_id": "a", "canonical_username": "usra", "uid": 1001, "gid": 1001},
            {"node_id": "b", "canonical_username": "usrb", "uid": 2001, "gid": 2001},
        ])
        with store.connection:
            store.connection.execute("UPDATE lab_account_bindings SET valid_from = ?", (start,))
        for at in (start, start + 7200):
            for node, uid, name, count in (("a", 1001, "usra", 2), ("b", 2001, "usrb", 1)):
                gpus = [GpuInfo(index=i, uuid=f"GPU-{i}", name="NVIDIA H100", utilization_gpu=50,
                    utilization_mem=20, memory_total_mb=80000, memory_used_mb=20000, power_watts=100,
                    power_limit_watts=700, temperature_c=40, processes=[GpuProcess(pid=42, name="python",
                    task_name="training", user=name, user_uid=uid, gpu_memory_mb=20000,
                    process_start_time=start)]) for i in range(count)]
                snapshot = NodeSnapshot(node_id=node, hostname=node, seq=int(at), sampled_at=at, received_at=at,
                    refresh_interval=1, process_interval=5, status="online", source="test",
                    gpus=gpus, totals=node_totals_from_gpus(gpus))
                sink.store.write_node_snapshot(snapshot)
        sink.store.close_stale_sessions(now=time.time())
        response = client.get("/api/lab/me/activity?range=7d&limit=1", headers=headers())
        assert response.status_code == 200
        data = response.json()
        assert data["summary"]["gpu_hours"] == 6 and data["summary"]["active_hours"] == 2
        assert data["jobs"]["total"] == 2 and len(data["jobs"]["items"]) == 1
        assert {r["model"] for r in data["gpu_models"]} == {"H100"}
        assert data["coverage"] == "unknown"
        overview = client.get("/api/analytics/overview?range=7d", headers=headers()).json()
        assert len(overview["user_gpu_hours"]) == 1
        assert overview["user_gpu_hours"][0]["gpu_hours"] == 6
        assert client.get("/api/lab/me/activity?range=90d", headers=headers()).status_code == 422
        assert client.get("/api/lab/me/activity").status_code == 401
        verifier.identity = identity("member-sub", "member@example.com")
        other = client.get(f"/api/lab/me/activity?user_id={user['id']}", headers=headers()).json()
        assert other["availability"] == "unbound" and other["summary"]["gpu_hours"] == 0
        verifier.identity = identity("admin-sub", "admin@example.com")
        # A disconnected account retains its history and invalidates the cached binding scope.
        with store.connection:
            store.connection.execute("UPDATE lab_account_bindings SET valid_to = ?, status = 'revoked' WHERE node_id = 'b'", (start + 3600,))
        changed = client.get("/api/lab/me/activity", headers=headers()).json()
        assert changed["summary"]["gpu_hours"] == 5

        for gpu in snapshot.gpus:
            gpu.processes[0].pid = 99
            gpu.processes[0].user_uid = None
        sink.store.write_node_snapshot(snapshot)
        missing_uid = client.get("/api/lab/me/activity?range=30d", headers=headers()).json()
        assert missing_uid["missing_uid_sessions"] == 1
        assert missing_uid["summary"]["gpu_hours"] == 5
        monkeypatch.setattr("constella_lab.activity.MAX_ROWS", 1)
        with store.connection:
            store.connection.execute("UPDATE lab_account_bindings SET valid_from = ?", (start - 1,))
        assert client.get("/api/lab/me/activity", headers=headers()).status_code == 503


def test_choose_readonly_persists_and_blocks_personal_features(tmp_path):
    verifier = MutableVerifier(identity("member-sub", "member@example.com"))
    app = create_lab_app(config=config(tmp_path), verifier=verifier)
    with TestClient(app) as client:
        assert client.post("/api/lab/me/readonly", headers=headers()).status_code == 403
        response = client.post("/api/lab/me/readonly", headers=headers(csrf=True))
        assert response.status_code == 200
        user = response.json()["user"]
        assert user["role"] == "viewer" and user["onboarding_completed_at"] is not None
        assert user["bindings"] == []
        assert client.get("/api/lab/me", headers=headers()).json()["user"] == user
        assert client.get("/api/cluster/snapshot", headers=headers()).status_code == 200
        assert client.get("/api/lab/me/activity", headers=headers()).status_code == 403
        assert client.patch("/api/lab/me", json={"display_name": "changed"}, headers=headers(csrf=True)).status_code == 403
        assert client.post("/api/lab/me/readonly", headers=headers(csrf=True)).status_code == 409
        verifier.identity = identity("admin-sub", "admin@example.com")
        assert client.post("/api/lab/me/readonly", headers=headers(csrf=True)).status_code == 409
        assert client.get("/api/lab/me", headers=headers()).json()["user"]["role"] == "admin"
