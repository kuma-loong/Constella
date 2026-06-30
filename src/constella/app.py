from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .cluster import ClusterState, parse_agent_hello
from .collector import SnapshotCollector, snapshot_to_jsonable
from .schema import local_node_id

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


class SettingsUpdate(BaseModel):
    refresh_interval: float


def _load_agent_token() -> str | None:
    token = os.environ.get("CONSTELLA_AGENT_TOKEN")
    if token:
        return token
    token_file = os.environ.get("CONSTELLA_AGENT_TOKEN_FILE")
    if not token_file:
        return None
    try:
        return Path(token_file).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _agent_authorized(websocket: WebSocket, expected_token: str | None) -> bool:
    if not expected_token:
        return False
    authorization = websocket.headers.get("authorization", "")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return False
    return authorization[len(prefix) :] == expected_token


def create_app(
    refresh_interval: float | None = None,
    collector: SnapshotCollector | None = None,
    cluster_state: ClusterState | None = None,
    agent_token: str | None = None,
) -> FastAPI:
    if collector is None:
        interval = (
            refresh_interval
            if refresh_interval is not None
            else float(os.environ.get("CONSTELLA_REFRESH_SECONDS", "1.0"))
        )
        process_interval = float(os.environ.get("CONSTELLA_PROCESS_SECONDS", "3.0"))
        collector = SnapshotCollector(refresh_interval=interval, process_interval=process_interval)
    if cluster_state is None:
        cluster_state = ClusterState(local_node_id=local_node_id())
    expected_agent_token = agent_token if agent_token is not None else _load_agent_token()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await collector.start()
        app.state.collector = collector
        app.state.cluster_state = cluster_state
        yield
        await collector.stop()

    app = FastAPI(
        title="Constella",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
    )

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        snapshot = collector.snapshot
        return {
            "ok": bool(snapshot and snapshot.ok),
            "seq": snapshot.seq if snapshot else 0,
            "source": snapshot.source if snapshot else "none",
            "gpu_count": len(snapshot.gpus) if snapshot else 0,
            "error": snapshot.error if snapshot else None,
        }

    @app.get("/api/snapshot")
    async def snapshot() -> dict[str, object]:
        return snapshot_to_jsonable(collector.snapshot)

    @app.get("/api/cluster/snapshot")
    async def cluster_snapshot() -> dict[str, object]:
        return cluster_state.snapshot(
            local_snapshot=collector.snapshot,
            local_process_interval=collector.process_interval,
        ).to_dict()

    @app.get("/api/settings")
    async def settings() -> dict[str, object]:
        return collector.settings()

    @app.patch("/api/settings")
    async def update_settings(update: SettingsUpdate) -> dict[str, object]:
        try:
            return collector.set_refresh_interval(update.refresh_interval)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.websocket("/ws/gpu")
    async def gpu_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        last_seq = 0
        try:
            while True:
                current = await collector.wait_for_update(last_seq, timeout=30.0)
                payload = snapshot_to_jsonable(current)
                last_seq = int(payload.get("seq") or last_seq)
                await websocket.send_json(payload)
        except WebSocketDisconnect:
            return

    @app.websocket("/ws/cluster")
    async def cluster_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        last_seq = -1
        try:
            while True:
                current = cluster_state.snapshot(
                    local_snapshot=collector.snapshot,
                    local_process_interval=collector.process_interval,
                )
                if current.seq != last_seq:
                    last_seq = current.seq
                    await websocket.send_json(current.to_dict())
                await cluster_state.wait_for_update(last_seq, timeout=collector.refresh_interval)
        except WebSocketDisconnect:
            return

    @app.websocket("/api/agents/ws")
    async def agent_ws(websocket: WebSocket) -> None:
        if not _agent_authorized(websocket, expected_agent_token):
            await websocket.close(code=4401)
            return

        await websocket.accept()
        node_id: str | None = None
        try:
            hello = parse_agent_hello(await websocket.receive_json())
            node_id = hello.node_id
            cluster_state.register_hello(hello)
            await websocket.send_json(
                {
                    "type": "config",
                    "refresh_interval": collector.refresh_interval,
                    "process_interval": collector.process_interval,
                }
            )

            while True:
                message = await websocket.receive_json()
                message_type = message.get("type")
                if message_type == "sample":
                    accepted = cluster_state.ingest_sample(message)
                    await websocket.send_json({"type": "ack", "seq": message.get("seq"), "accepted": accepted})
                elif message_type == "heartbeat":
                    heartbeat_node_id = str(message.get("node_id") or node_id or "")
                    if heartbeat_node_id:
                        cluster_state.ingest_heartbeat(
                            heartbeat_node_id,
                            seq=int(message.get("seq") or 0),
                        )
                    await websocket.send_json({"type": "ack", "seq": message.get("seq")})
                else:
                    await websocket.send_json(
                        {"type": "error", "error": f"unsupported agent message: {message_type}"}
                    )
        except WebSocketDisconnect:
            if node_id:
                cluster_state.disconnect(node_id)
            return

    if FRONTEND_DIST.exists():
        assets_path = FRONTEND_DIST / "assets"
        if assets_path.exists():
            app.mount("/assets", StaticFiles(directory=assets_path), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        @app.head("/{path:path}", include_in_schema=False)
        async def frontend(path: str):
            requested = FRONTEND_DIST / path
            if path and requested.exists() and requested.is_file():
                return FileResponse(requested)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()
