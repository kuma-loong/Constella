"""Exercise Rust Lab authentication, identity management, and a real preview Agent."""

from contextlib import ExitStack, closing
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import asyncio
import ipaddress
import json
import os
from pathlib import Path
import runpy
import ssl
import pwd
import secrets
import sqlite3
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from constella.analytics import _usage_rows
from constella.cluster import ClusterState
from constella.db import SQLiteStore
from constella_lab.activity import payload as python_activity, read_activity
from constella_lab.activity_usage import TIMEZONE, user_statistics
from constella_lab.store import LabStore
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import jwt
import websockets


root = Path(__file__).resolve().parents[2]
run = root / "run" / "preview-lab-auth" / str(time.time_ns())
run.mkdir(mode=0o700, parents=True)
helper = runpy.run_path(str(root / "scripts/dev/benchmark-runtime.py"))
agent_binary = os.environ.get(
    "CONSTELLA_PREVIEW_AGENT", str(root / "target/release/constella-agent")
)
manager_binary = os.environ.get(
    "CONSTELLA_PREVIEW_MANAGER", str(root / "target/release/constella-manager")
)
audience = "preview-audience"
issuer = "https://127.0.0.1:8875"
origin = "https://preview.example.com"
base = "http://127.0.0.1:8876"
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Constella preview JWKS")])
now = datetime.now(timezone.utc)
cert = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
cert = cert.serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
cert = cert.not_valid_after(now + timedelta(hours=1))
cert = cert.add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
cert = cert.sign(key, hashes.SHA256())
leaf = (
    x509.CertificateBuilder()
    .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
    .issuer_name(name)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
)
leaf = leaf.not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
leaf = leaf.add_extension(
    x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False
).sign(key, hashes.SHA256())
cert_path = run / "preview-ca.pem"
cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
leaf_path = run / "preview-leaf.pem"
leaf_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
key_path = run / "key.pem"
key_path.touch(mode=0o600)
key_path.write_bytes(
    key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
)
jwks = json.dumps(
    {
        "keys": [
            {
                **jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True),
                "kid": "preview",
                "alg": "RS256",
            }
        ]
    }
)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/cdn-cgi/access/certs":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(jwks.encode())))
        self.end_headers()
        self.wfile.write(jwks.encode())

    def log_message(self, *args):
        pass


def token(subject, email, **overrides):
    now = int(time.time())
    return jwt.encode(
        {
            "iss": issuer,
            "aud": [audience],
            "sub": subject,
            "email": email,
            "type": "app",
            "iat": now,
            "exp": now + 600,
            **overrides,
        },
        key,
        algorithm="RS256",
        headers={"kid": "preview"},
    )


def request(path, auth=None, body=None, *, method="GET", csrf=False):
    headers = {}
    if auth:
        headers["Cf-Access-Jwt-Assertion"] = auth
    if csrf:
        headers.update({"Origin": origin, "X-Constella-Request": "same-origin"})
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(
        base + path,
        headers=headers,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
    )
    try:
        with urlopen(req, timeout=6) as res:
            return res.status, json.load(res), res.headers
    except HTTPError as error:
        return error.code, json.load(error), error.headers


async def check_ws(auth):
    endpoint = "ws://127.0.0.1:8876/ws/cluster"
    try:
        async with websockets.connect(endpoint):
            raise AssertionError("unauthenticated browser socket was upgraded")
    except websockets.exceptions.InvalidStatus as error:
        assert error.response.status_code == 401
    async with websockets.connect(
        endpoint, additional_headers={"Cf-Access-Jwt-Assertion": auth}
    ) as ws:
        assert "nodes" in json.loads(await asyncio.wait_for(ws.recv(), 2))
        try:
            await asyncio.wait_for(ws.recv(), 5)
        except websockets.exceptions.ConnectionClosedError as error:
            assert error.rcvd.code == 4401, error
        else:
            raise AssertionError("authenticated socket did not enforce JWT expiration")
    try:
        async with websockets.connect("ws://127.0.0.1:8876/api/highres/stream"):
            raise AssertionError("unauthenticated machine stream was upgraded")
    except websockets.exceptions.InvalidStatus as error:
        assert error.response.status_code == 401


async def check_agent_service_auth(env, tls, auth):
    # Stand-in for the separate TLS machine route and its Service Auth policy.
    client_id = secrets.token_urlsafe(24)
    client_secret = secrets.token_urlsafe(32)
    id_path, secret_path = run / "edge-client-id", run / "edge-client-secret"
    for path, value in ((id_path, client_id), (secret_path, client_secret)):
        path.touch(mode=0o600)
        path.write_text(value + "\n")
    config = {
        **env,
        "CONSTELLA_NODE_ID": "lab-preview-edge-agent",
        "CONSTELLA_ACCESS_CLIENT_ID_FILE": str(id_path),
        "CONSTELLA_ACCESS_CLIENT_SECRET_FILE": str(secret_path),
    }
    with ExitStack() as processes:
        cleartext = helper["launch"](processes, run, config, "cleartext-agent", [agent_binary])
        assert cleartext.wait(timeout=3) != 0
    assert "Cloudflare Access credentials require HTTPS or WSS" in (
        run / "cleartext-agent.log"
    ).read_text()
    checks = {
        "CF-Access-Client-Id": client_id,
        "CF-Access-Client-Secret": client_secret,
    }
    rejected_bearer = asyncio.Event()
    upstream = "ws://127.0.0.1:8876/api/agents/ws"

    def validate(socket, request):
        if request.path != "/api/agents/ws" or any(
            request.headers.get(name) != value for name, value in checks.items()
        ):
            return socket.respond(403, "Service Auth required")
        return None

    async def copy_frames(source, dest):
        async for frame in source:
            await dest.send(frame)

    async def relay(socket):
        bearer = socket.request.headers.get("Authorization")
        headers = {"Authorization": bearer} if bearer else {}
        try:
            async with websockets.connect(upstream, additional_headers=headers) as manager:
                tasks = [
                    asyncio.create_task(copy_frames(socket, manager)),
                    asyncio.create_task(copy_frames(manager, socket)),
                ]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except websockets.exceptions.InvalidStatus as error:
            assert error.response.status_code == 401
            rejected_bearer.set()
            await socket.close(code=1008)

    client_tls = ssl.create_default_context(cafile=str(cert_path))
    async with websockets.serve(
        relay, "127.0.0.1", 0, ssl=tls, process_request=validate
    ) as edge:
        port = edge.sockets[0].getsockname()[1]
        url = f"wss://127.0.0.1:{port}"
        try:
            async with websockets.connect(url + "/api/agents/ws", ssl=client_tls):
                raise AssertionError("machine route accepted a request without Service Auth")
        except websockets.exceptions.InvalidStatus as error:
            assert error.response.status_code == 403
        async with websockets.connect(
            url + "/api/agents/ws", ssl=client_tls, additional_headers=checks
        ):
            await asyncio.wait_for(rejected_bearer.wait(), timeout=3)
        config["CONSTELLA_MANAGER_URL"] = url
        with ExitStack() as processes:
            agent = helper["launch"](processes, run, config, "edge-agent", [agent_binary])
            async with websockets.connect(
                "ws://127.0.0.1:8876/ws/cluster",
                additional_headers={"Cf-Access-Jwt-Assertion": auth},
            ) as browser:
                async with asyncio.timeout(10):
                    async for message in browser:
                        assert agent.poll() is None
                        if any(
                            node["node_id"] == config["CONSTELLA_NODE_ID"]
                            and node["status"] == "online" and node["gpus"]
                            for node in json.loads(message)["nodes"]
                        ):
                            print("TLS machine route verified Service Auth and separate Agent Bearer token")
                            return
        raise AssertionError("machine route did not deliver Agent telemetry")


def run_activity_checks(first, second, admin):
    telemetry, identities = run / "telemetry.sqlite3", run / "identity.sqlite3"
    start = (
        datetime.now(TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(days=1)
    ).timestamp()
    a, b = (
        request("/api/lab/me", first)[1]["user"]["id"],
        request("/api/lab/me", second)[1]["user"]["id"],
    )
    node, uid, name = "ownership-case", 9251, "fixture-owner"
    with closing(sqlite3.connect(identities)) as conn, conn:
        for binding, owner, left, right in (("first", a, 1, 2), ("second", b, 3, None)):
            conn.execute(
                """INSERT INTO lab_account_bindings (
                id,user_id,node_id,unix_uid,unix_username,assurance,status,valid_from,valid_to,created_by)
                VALUES (?,?,?,?,?,'self_claimed',?,?,?,?)""",
                (
                    binding,
                    owner,
                    node,
                    uid,
                    name,
                    "reassigned" if right else "active",
                    start + left * 3600,
                    start + right * 3600 if right else None,
                    a,
                ),
            )
        conn.execute(
            "UPDATE lab_users SET onboarding_completed_at = ? WHERE id IN (?,?)", (start, a, b)
        )
    with closing(sqlite3.connect(telemetry)) as conn, conn:
        for index, uuid in enumerate(("die-0", "die-1")):
            conn.execute(
                """INSERT INTO gpus
                (gpu_id,node_id,uuid,gpu_index,name,device_type,card_id,die_id,memory_total_mb,first_seen_at,last_seen_at)
                VALUES(?,?,?,?,'Ascend 910','ascend','card-a',?,32768,?,?)""",
                (f"{node}:{uuid}", node, uuid, index, index, start, start + 14400),
            )
        for pid, observed_uid, username, end, devices in [
            (41, uid, "before-rename", 4, ("die-0", "die-1")),
            (42, None, name, 1.5, ("die-0",)),
            (43, 9252, name, 1.5, ("die-1",)),
        ]:
            session = f"{node}:{pid}"
            conn.execute(
                """INSERT INTO process_sessions
                (session_id,node_id,pid,user,user_uid,task_name,process_name,first_seen_at,last_seen_at,
                 duration_seconds,status,sample_count)
                VALUES (?,?,?,?,?,'Fixture workload','test',?,?,?,'ended',2)""",
                (session, node, pid, username, observed_uid, start, start + end * 3600, end * 3600),
            )
            for uuid in devices:
                conn.execute(
                    "INSERT INTO process_gpu_usages VALUES (?,?,?,?,?,128,128,128,2)",
                    (session, node, uuid, start, start + end * 3600),
                )
    identity = LabStore(identities)
    identity.open()
    try:
        bindings = identity.list_bindings(active_only=False)
    finally:
        identity.close()
    label = datetime.fromtimestamp(start, TIMEZONE).date().isoformat()
    url = f"/api/lab/me/activity?date={label}&gpu_model=Ascend%20910"
    for jwt_assertion, owner, expected_hours in [(first, a, 2), (second, b, 1)]:
        code, actual, _ = request(url, jwt_assertion)
        assert code == 200, actual
        data = read_activity(telemetry, bindings, owner, 7)
        data.as_of = actual["as_of"]
        today = data.days[-1]
        data.days[-1] = (today[0], today[1], actual["as_of"])
        expected = python_activity(
            data,
            ClusterState(local_node_id="local-fixture"),
            date=label,
            model="Ascend 910",
            status="all",
            q="",
            cursor=None,
            limit=20,
        )
        for key in expected:
            if key in {"jobs", "day_jobs"}:
                assert actual[key]["total"] == expected[key]["total"], key
                for job, reference in zip(
                    actual[key]["items"], expected[key]["items"], strict=True
                ):
                    for title in job:
                        if title != "intervals":
                            assert job[title] == reference[title], (
                                key,
                                title,
                                job[title],
                                reference[title],
                            )

                    def ordered(items):
                        return sorted(
                            (row["start"], row["end"], row["card"], row["model"]) for row in items
                        )

                    assert ordered(job["intervals"]) == ordered(reference["intervals"])
            else:
                assert actual[key] == expected[key], (key, actual[key], expected[key])
        assert (
            actual["availability"] == "ready" and actual["summary"]["gpu_hours"] == expected_hours
        )
    code, first_page, _ = request(url + "&limit=1", first)
    assert code == 200 and first_page["jobs"]["total"] == 2
    cursor = first_page["jobs"]["next_cursor"]
    assert cursor and request(url + "&limit=1&cursor=" + cursor, first)[1]["jobs"]["total"] == 2
    assert request(url + "&cursor=" + cursor, second)[1]["detail"] == "activity_page_expired"
    assert request(url + "&cursor=bad", first)[1]["detail"] == "invalid_cursor"
    ranked = request("/api/analytics/overview?range=7d", admin)[1]
    old = SQLiteStore(telemetry)
    old.open_readonly()
    try:
        rows = _usage_rows(old, range_start=ranked["range_start"], range_end=ranked["range_end"])
        expected = user_statistics(rows, bindings, ranked["range_start"], ranked["range_end"])
    finally:
        old.close()
    assert ranked["user_gpu_hours"] == expected, "ownership ranking parity"
    assert {record["owner_key"] for record in expected} >= {
        f"user:{a}",
        f"user:{b}",
        'account:["ownership-case", 9252]',
    }
    print("Lab personal activity, cursor isolation, and historical ownership rankings match Python")


with ExitStack() as stack:
    server = stack.enter_context(ThreadingHTTPServer(("127.0.0.1", 8875), Handler))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(leaf_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stack.callback(server.shutdown)
    env = {key: value for key, value in os.environ.items() if not key.startswith("CONSTELLA_")}
    env.update(
        CONSTELLA_BIND="127.0.0.1:8876",
        CONSTELLA_AUTH_MODE="cloudflare-access",
        CONSTELLA_ACCESS_TEAM_DOMAIN=issuer,
        CONSTELLA_ACCESS_AUD=audience,
        CONSTELLA_PUBLIC_ORIGIN=origin,
        CONSTELLA_BOOTSTRAP_ADMIN_EMAIL="admin@preview.example.com",
        CONSTELLA_LAB_DB_PATH=str(run / "identity.sqlite3"),
        CONSTELLA_DB_PATH=str(run / "telemetry.sqlite3"),
        SSL_CERT_FILE=str(cert_path),
        CONSTELLA_AGENT_TOKEN=secrets.token_urlsafe(32),
        CONSTELLA_NODE_ID="lab-preview-agent",
        CONSTELLA_MANAGER_URL="http://127.0.0.1:8876",
    )
    if frontend := os.environ.get("CONSTELLA_PREVIEW_FRONTEND"):
        env["CONSTELLA_FRONTEND_DIST"] = frontend
    manager = helper["launch"](stack, run, env, "manager", [manager_binary, "lab"])
    for _ in range(100):
        if manager.poll() is not None:
            raise RuntimeError("Lab manager exited")
        try:
            assert request("/api/health")[0] == 200
            break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("Lab manager did not start")
    assert request("/api/lab/me")[0] == 401
    assert (
        request("/api/lab/me", token("wrong", "bad@preview.example.com", aud=["wrong"]))[0] == 401
    )
    assert (
        request(
            "/api/lab/me",
            token("wrong", "bad@preview.example.com", iss="https://wrong.example.com"),
        )[0]
        == 401
    )
    assert (
        request("/api/lab/me", token("wrong", "bad@preview.example.com", type="service"))[0] == 401
    )
    admin = token("admin", "admin@preview.example.com")
    member = token("member", "member@preview.example.com")
    status, profile, headers = request("/api/lab/me", admin)
    assert status == 200 and profile["user"]["role"] == "admin", (status, profile)
    assert headers["x-request-id"] == profile["request_id"] and headers["x-frame-options"] == "DENY"
    admin_id = profile["user"]["id"]
    _, profile, _ = request("/api/lab/me", member)
    assert profile["user"]["role"] == "member"
    assert request("/api/lab/admin/users", member)[0] == 403
    assert (
        request("/api/lab/me", member, {"display_name": "Name"}, method="PATCH")[1]["error"]
        == "csrf_check_failed"
    )
    status, profile, _ = request(
        "/api/lab/me", member, {"display_name": "Name"}, method="PATCH", csrf=True
    )
    assert status == 200 and profile["user"]["display_name"] == "Name"
    status, profile, _ = request("/api/lab/me/readonly", member, method="POST", csrf=True)
    assert status == 200 and profile["user"]["role"] == "viewer"
    assert (
        request("/api/lab/me", member, {"display_name": "Name"}, method="PATCH", csrf=True)[0]
        == 403
    )
    update_path = "/api/lab/admin/users/" + admin_id
    assert request(update_path, admin, {"status": "disabled"}, method="PATCH", csrf=True)[0] == 409
    pending = token("admin-new", "admin@preview.example.com")
    assert request("/api/lab/me", pending)[1]["error"] == "user_not_active"
    _, records, _ = request("/api/lab/admin/users", admin)
    pending_id = next(
        row["id"] for row in records["users"] if row["status"] == "pending_identity_review"
    )
    assert (
        request(
            update_path + "/migrate-identity",
            admin,
            {"pending_user_id": pending_id, "reason": "preview"},
            method="POST",
            csrf=True,
        )[0]
        == 200
    )
    assert request("/api/lab/me", pending)[1]["user"]["id"] == admin_id
    _, audit, _ = request("/api/lab/admin/audit-events", pending)
    assert audit["events"][0]["action"] == "identity.migrated"
    asyncio.run(check_ws(token("socket", "socket@preview.example.com", exp=int(time.time()) + 3)))
    run_activity_checks(
        token("history-first", "first@preview.example.com"),
        token("history-second", "second@preview.example.com"),
        pending,
    )
    agent = helper["launch"](stack, run, env, "agent", [agent_binary])

    def node_snapshot():
        return request("/api/lab/admin/nodes", pending)[1]["nodes"]

    for _ in range(50):
        nodes = node_snapshot()
        if nodes and nodes[0]["restart_supported"] and nodes[0]["status"] == "online":
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("Rust Agent never became controllable")
    (node,) = nodes
    instance = node["agent_instance_id"]
    name, _, uid, gid, *_ = pwd.getpwuid(os.getuid())
    binding_member = token("binding-member", "bind@preview.example.com")
    recipient = token("binding-recipient", "recipient@preview.example.com")
    recipient_id = request("/api/lab/me", recipient)[1]["user"]["id"]
    assert request("/api/lab/account-binding-nodes", binding_member)[1]["nodes"][0][
        "binding_supported"
    ]
    targets = {"accounts": [{"node_id": node["node_id"], "username": name}]}

    def mutate(path, auth, body=None, method="POST"):
        return request(path, auth, body, method=method, csrf=True)

    status, lookup, _ = mutate("/api/lab/account-lookups/batch", binding_member, targets)
    assert status == 200 and lookup["results"] == [
        {
            "node_id": node["node_id"],
            "bindable": True,
            "canonical_username": name,
            "uid": uid,
            "gid": gid,
        }
    ], (status, lookup)
    status, created, _ = mutate("/api/lab/account-bindings/batch", binding_member, targets)
    assert status == 201, (status, created)
    (binding,) = created["bindings"]
    assert binding["assurance"] == "self_claimed"
    assert (
        mutate("/api/lab/admin/bindings/" + binding["id"] + "/verify", pending)[1]["binding"][
            "assurance"
        ]
        == "admin_verified"
    )
    assert mutate("/api/lab/account-bindings/batch", recipient, targets)[0] == 422
    status, changed, _ = mutate(
        "/api/lab/admin/bindings/reassign",
        pending,
        {
            "user_id": recipient_id,
            "reason": "testing",
            "username": name,
            "node_id": node["node_id"],
        },
    )
    assert status == 200 and changed["binding"]["user_id"] == recipient_id, (status, changed)
    history = request("/api/lab/me", binding_member)[1]["user"]["bindings"]
    assert history[0]["status"] == "reassigned" and history[0]["valid_to"] is not None
    new_id = changed["binding"]["id"]
    assert mutate("/api/lab/account-bindings/" + new_id, binding_member, method="DELETE")[0] == 404
    assert mutate("/api/lab/account-bindings/" + new_id, recipient, method="DELETE")[1]["ended"]
    restart_path = "/api/lab/admin/nodes/" + node["node_id"] + "/restart"
    assert mutate(restart_path, binding_member)[0] == 403
    status, response, _ = mutate(restart_path, pending)
    assert status == 202 and response["status"] == "accepted", (status, response)
    for _ in range(50):
        nodes = node_snapshot()
        if nodes and nodes[0]["restart_supported"] and nodes[0]["agent_instance_id"] != instance:
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("Agent instance remained unchanged after restart")
    assert agent.poll() is None
    assert mutate(restart_path, pending)[0] == 429
    _, audit, _ = request("/api/lab/admin/audit-events", pending)
    assert {"agent.restart_accepted", "agent.restart_requested"}.issubset(
        {row["action"] for row in audit["events"]}
    )
    print(
        "Lab real NSS lookup, account binding/verify/reassign/revoke, Agent re-exec and restart audit verified"
    )
    print(
        "Lab HTTPS JWT/JWKS, bootstrap and migration, role checks, CSRF, audit, and browser/machine WebSockets verified"
    )
    asyncio.run(check_agent_service_auth(env, context, pending))
