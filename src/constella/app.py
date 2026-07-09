from __future__ import annotations

import asyncio
import contextlib
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .analytics import node_analytics, overview_analytics
from .cluster import ClusterState, parse_agent_hello
from .collector import ALLOWED_REFRESH_INTERVALS, validate_refresh_interval
from .db import AsyncDBSink, SQLiteSinkConfig
from .highres import (
    HIGHRES_JOB_LOOKBACK_SECONDS,
    HighresGpuCache,
    HighresSampleBroadcaster,
    get_job,
    job_curve,
    query_jobs,
)
from .schema import local_node_id

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


class SettingsUpdate(BaseModel):
    refresh_interval: float | None = None
    process_interval: float | None = None


@dataclass(slots=True)
class ManagerSettings:
    refresh_interval: float = 1.0
    _process_interval: float = 5.0

    @classmethod
    def from_env(
        cls,
        *,
        refresh_interval: float | None = None,
        process_interval: float | None = None,
    ) -> ManagerSettings:
        refresh = (
            refresh_interval
            if refresh_interval is not None
            else float(os.environ.get("CONSTELLA_REFRESH_SECONDS", "1.0"))
        )
        process = (
            process_interval
            if process_interval is not None
            else float(os.environ.get("CONSTELLA_PROCESS_SECONDS", "5.0"))
        )
        return cls(
            refresh_interval=validate_refresh_interval(refresh),
            _process_interval=max(1.0, float(process)),
        )

    @property
    def process_interval(self) -> float:
        return max(self._process_interval, self.refresh_interval)

    def to_dict(self) -> dict[str, object]:
        return {
            "refresh_interval": self.refresh_interval,
            "allowed_refresh_intervals": list(ALLOWED_REFRESH_INTERVALS),
            "process_interval": self.process_interval,
        }

    def config_message(self) -> dict[str, object]:
        return {
            "type": "config",
            "refresh_interval": self.refresh_interval,
            "process_interval": self.process_interval,
        }

    def update(self, update: SettingsUpdate) -> dict[str, object]:
        if update.refresh_interval is None and update.process_interval is None:
            return self.to_dict()
        if update.refresh_interval is not None:
            self.refresh_interval = validate_refresh_interval(update.refresh_interval)
        if update.process_interval is not None:
            self._process_interval = max(1.0, float(update.process_interval))
        return self.to_dict()


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


def _load_highres_token() -> str | None:
    token = os.environ.get("CONSTELLA_HIGHRES_TOKEN")
    if token:
        return token
    token_file = os.environ.get("CONSTELLA_HIGHRES_TOKEN_FILE")
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


def _load_db_sink() -> AsyncDBSink | None:
    db_path = os.environ.get("CONSTELLA_DB_PATH")
    if not db_path:
        return None
    queue_size = int(os.environ.get("CONSTELLA_DB_QUEUE_SIZE", "1024"))
    raw_interval = float(os.environ.get("CONSTELLA_RAW_SNAPSHOT_SECONDS", "0"))
    return AsyncDBSink(
        SQLiteSinkConfig(
            path=Path(db_path),
            queue_size=queue_size,
            raw_snapshot_interval=raw_interval,
        )
    )


def create_app(
    refresh_interval: float | None = None,
    process_interval: float | None = None,
    cluster_state: ClusterState | None = None,
    agent_token: str | None = None,
    db_sink: AsyncDBSink | None = None,
    manager_settings: ManagerSettings | None = None,
    highres_cache: HighresGpuCache | None = None,
    highres_broadcaster: HighresSampleBroadcaster | None = None,
) -> FastAPI:
    if manager_settings is None:
        manager_settings = ManagerSettings.from_env(
            refresh_interval=refresh_interval,
            process_interval=process_interval,
        )
    if cluster_state is None:
        cluster_state = ClusterState(local_node_id=local_node_id())
    expected_agent_token = agent_token if agent_token is not None else _load_agent_token()
    expected_highres_token = _load_highres_token()
    db_sink = db_sink if db_sink is not None else _load_db_sink()
    highres_cache = highres_cache if highres_cache is not None else HighresGpuCache()
    highres_broadcaster = highres_broadcaster or HighresSampleBroadcaster()
    agent_queues: set[asyncio.Queue[dict[str, object]]] = set()

    def broadcast_config() -> None:
        for queue in list(agent_queues):
            queue.put_nowait(manager_settings.config_message())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if db_sink is not None:
            await db_sink.start()
        app.state.cluster_state = cluster_state
        app.state.settings = manager_settings
        app.state.db_sink = db_sink
        app.state.highres_cache = highres_cache
        app.state.highres_broadcaster = highres_broadcaster
        yield
        if db_sink is not None:
            await db_sink.stop()

    app = FastAPI(
        title="Constella",
        version="0.2.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.cluster_state = cluster_state
    app.state.settings = manager_settings
    app.state.db_sink = db_sink
    app.state.highres_cache = highres_cache
    app.state.highres_broadcaster = highres_broadcaster

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        snapshot = cluster_state.snapshot()
        return {
            "ok": True,
            "seq": snapshot.seq,
            "source": "manager",
            "agent_ingest_enabled": expected_agent_token is not None,
            "node_count": snapshot.totals.node_count,
            "online_node_count": snapshot.totals.online_node_count,
            "gpu_count": snapshot.totals.gpu_count,
        }

    @app.get("/api/snapshot")
    async def snapshot() -> dict[str, object]:
        raise HTTPException(
            status_code=410,
            detail="GET /api/snapshot is retired; use GET /api/cluster/snapshot",
        )

    @app.get("/api/cluster/snapshot")
    async def cluster_snapshot() -> dict[str, object]:
        return cluster_state.snapshot().to_dict()

    @app.get("/api/history/gpu")
    async def gpu_history(
        node_id: str | None = None,
        gpu_uuid: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 1000,
    ) -> dict[str, object]:
        if db_sink is None:
            return {"enabled": False, "items": []}
        return {
            "enabled": True,
            "items": db_sink.store.query_gpu_history(
                node_id=node_id,
                gpu_uuid=gpu_uuid,
                since=since,
                until=until,
                limit=max(1, min(limit, 5000)),
            ),
        }

    @app.get("/api/history/tasks")
    async def task_history(
        user: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> dict[str, object]:
        if db_sink is None:
            return {"enabled": False, "items": []}
        return {
            "enabled": True,
            "items": db_sink.store.query_tasks(user=user, status=status, limit=max(1, min(limit, 1000))),
        }

    @app.get("/api/users")
    async def users() -> dict[str, object]:
        if db_sink is None:
            return {"enabled": False, "items": []}
        return {"enabled": True, "items": db_sink.store.query_users()}

    @app.get("/api/highres/status")
    async def highres_status() -> dict[str, object]:
        return app.state.highres_cache.status()

    @app.get("/api/highres/jobs")
    async def highres_jobs(
        q: str | None = None,
        user: str | None = None,
        pid: int | None = None,
        node_id: str | None = None,
        status: str | None = None,
        since: float | None = None,
        until: float | None = None,
        max_duration_seconds: float | None = None,
        recent_seconds: float = HIGHRES_JOB_LOOKBACK_SECONDS,
        limit: int = 100,
    ) -> dict[str, object]:
        if db_sink is None:
            return {"enabled": False, "items": []}
        return {
            "enabled": True,
            "items": query_jobs(
                db_sink.store,
                q=q,
                user=user,
                pid=pid,
                node_id=node_id,
                status=status,
                since=since,
                until=until,
                max_duration_seconds=(
                    max(1.0, min(max_duration_seconds, HIGHRES_JOB_LOOKBACK_SECONDS))
                    if max_duration_seconds is not None
                    else None
                ),
                recent_seconds=max(60.0, min(recent_seconds, HIGHRES_JOB_LOOKBACK_SECONDS)),
                limit=max(1, min(limit, 500)),
            ),
        }

    @app.get("/api/highres/jobs/{job_key:path}/gpu")
    async def highres_job_gpu(
        job_key: str,
        padding_seconds: float = 20.0,
        resolution: str = "auto",
    ) -> dict[str, object]:
        if db_sink is None:
            return {"enabled": False, "series": []}
        payload = job_curve(
            db_sink.store,
            app.state.highres_cache,
            key=job_key,
            padding_seconds=padding_seconds,
            resolution=resolution,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail="job not found")
        return payload

    @app.get("/api/highres/jobs/{job_key:path}")
    async def highres_job(job_key: str) -> dict[str, object]:
        if db_sink is None:
            return {"enabled": False}
        job = get_job(db_sink.store, job_key)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {"enabled": True, "item": job}

    @app.get("/api/analytics/overview")
    async def analytics_overview(range: str = "7d") -> dict[str, object]:
        if db_sink is None:
            return {"enabled": False}
        return overview_analytics(db_sink.store, range_name=range)

    @app.get("/api/analytics/node/{node_id}")
    async def analytics_node(node_id: str, range: str = "24h") -> dict[str, object]:
        if db_sink is None:
            return {"enabled": False}
        return node_analytics(db_sink.store, node_id=node_id, range_name=range)

    @app.get("/api/settings")
    async def settings_endpoint() -> dict[str, object]:
        return app.state.settings.to_dict()

    @app.patch("/api/settings")
    async def update_settings(update: SettingsUpdate) -> dict[str, object]:
        try:
            payload = app.state.settings.update(update)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        broadcast_config()
        return payload

    @app.websocket("/ws/gpu")
    async def gpu_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.close(code=1008, reason="WS /ws/gpu is retired; use /ws/cluster")

    @app.websocket("/ws/cluster")
    async def cluster_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        last_seq = -1
        last_sent_at = 0.0
        try:
            while True:
                current = cluster_state.snapshot()
                if current.seq != last_seq:
                    last_seq = current.seq
                    await websocket.send_json(current.to_dict())
                    last_sent_at = time.monotonic()
                interval = max(app.state.settings.refresh_interval, 0.5)
                await cluster_state.wait_for_update(
                    last_seq,
                    timeout=interval,
                )
                remaining = interval - (time.monotonic() - last_sent_at)
                if remaining > 0:
                    await asyncio.sleep(remaining)
        except WebSocketDisconnect:
            return

    @app.websocket("/api/agents/ws")
    async def agent_ws(websocket: WebSocket) -> None:
        if not _agent_authorized(websocket, expected_agent_token):
            await websocket.close(code=4401)
            return

        await websocket.accept()
        connection_id = object()
        node_id: str | None = None
        send_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()

        async def sender() -> None:
            while True:
                await websocket.send_json(await send_queue.get())

        sender_task = asyncio.create_task(sender(), name="agent-ws-sender")
        agent_queues.add(send_queue)
        try:
            hello = parse_agent_hello(await websocket.receive_json())
            node_id = hello.node_id
            cluster_state.register_hello(hello, connection_id=connection_id)
            send_queue.put_nowait(app.state.settings.config_message())

            while True:
                message = await websocket.receive_json()
                message_type = message.get("type")
                if message_type == "sample":
                    accepted = cluster_state.ingest_sample(message, connection_id=connection_id)
                    if accepted:
                        runtime = cluster_state.latest_by_node.get(str(message.get("node_id") or ""))
                        if runtime is not None:
                            app.state.highres_cache.add_snapshot(runtime.snapshot)
                            app.state.highres_broadcaster.publish_snapshot(runtime.snapshot)
                            if db_sink is not None:
                                db_sink.submit_node_snapshot(runtime.snapshot)
                    send_queue.put_nowait(
                        {"type": "ack", "seq": message.get("seq"), "accepted": accepted}
                    )
                elif message_type == "heartbeat":
                    heartbeat_node_id = str(message.get("node_id") or node_id or "")
                    if heartbeat_node_id:
                        cluster_state.ingest_heartbeat(
                            heartbeat_node_id,
                            seq=int(message.get("seq") or 0),
                            connection_id=connection_id,
                        )
                    send_queue.put_nowait({"type": "ack", "seq": message.get("seq")})
                else:
                    send_queue.put_nowait(
                        {"type": "error", "error": f"unsupported agent message: {message_type}"}
                    )
        except WebSocketDisconnect:
            if node_id:
                cluster_state.disconnect(node_id, connection_id=connection_id)
            return
        finally:
            agent_queues.discard(send_queue)
            sender_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender_task

    @app.websocket("/api/highres/stream")
    async def highres_stream(websocket: WebSocket) -> None:
        if expected_highres_token and not _agent_authorized(websocket, expected_highres_token):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        queue = app.state.highres_broadcaster.subscribe()
        try:
            await websocket.send_json(
                {"type": "hello", **app.state.highres_broadcaster.status()}
            )
            while True:
                await websocket.send_json(await queue.get())
        except WebSocketDisconnect:
            return
        finally:
            app.state.highres_broadcaster.unsubscribe(queue)

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
