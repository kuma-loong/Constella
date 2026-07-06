from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import websockets
from fastapi import FastAPI, HTTPException

from .db import SQLiteStore
from .highres import (
    HIGHRES_JOB_LOOKBACK_SECONDS,
    HighresGpuCache,
    get_job,
    job_curve,
    query_jobs,
)


@dataclass(slots=True)
class HighresSidecarConfig:
    db_path: Path
    manager_stream_url: str = "ws://127.0.0.1:8765/api/highres/stream"
    token: str | None = None
    retention_seconds: float = 2 * 60 * 60
    reconnect_seconds: float = 2.0

    @classmethod
    def from_env(
        cls,
        *,
        db_path: str | Path | None = None,
        manager_stream_url: str | None = None,
        token: str | None = None,
        token_file: str | Path | None = None,
        retention_seconds: float | None = None,
    ) -> HighresSidecarConfig:
        resolved_db_path = db_path or os.environ.get("CONSTELLA_DB_PATH")
        if not resolved_db_path:
            raise ValueError("highres sidecar requires CONSTELLA_DB_PATH or --db-path")
        resolved_token = token or os.environ.get("CONSTELLA_HIGHRES_TOKEN")
        resolved_token_file = token_file or os.environ.get("CONSTELLA_HIGHRES_TOKEN_FILE")
        if not resolved_token and resolved_token_file:
            resolved_token = Path(resolved_token_file).read_text(encoding="utf-8").strip()
        return cls(
            db_path=Path(resolved_db_path),
            manager_stream_url=manager_stream_url
            or os.environ.get("CONSTELLA_HIGHRES_MANAGER_STREAM_URL")
            or "ws://127.0.0.1:8765/api/highres/stream",
            token=resolved_token,
            retention_seconds=float(
                retention_seconds
                if retention_seconds is not None
                else os.environ.get("CONSTELLA_HIGHRES_RETENTION_SECONDS", str(2 * 60 * 60))
            ),
        )


class HighresStreamClient:
    def __init__(self, config: HighresSidecarConfig, cache: HighresGpuCache):
        self.config = config
        self.cache = cache
        self.connected = False
        self.last_connected_at: float | None = None
        self.last_message_at: float | None = None
        self.reconnect_count = 0
        self.message_count = 0
        self.error_count = 0
        self.last_error: str | None = None

    async def run_forever(self) -> None:
        while True:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self.error_count += 1
                self.last_error = str(exc)
                self.reconnect_count += 1
                await asyncio.sleep(max(0.2, self.config.reconnect_seconds))

    async def _run_once(self) -> None:
        headers = {}
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        async with websockets.connect(
            self.config.manager_stream_url,
            additional_headers=headers or None,
            max_queue=16,
            open_timeout=10,
        ) as websocket:
            self.connected = True
            self.last_connected_at = time.time()
            self.last_error = None
            async for raw in websocket:
                message = json.loads(raw)
                if message.get("type") == "hello":
                    continue
                if message.get("type") != "gpu_sample":
                    continue
                self.cache.add_sample_message(message)
                self.message_count += 1
                self.last_message_at = time.time()

    def status(self) -> dict[str, Any]:
        return {
            "manager_stream_url": self.config.manager_stream_url,
            "connected": self.connected,
            "last_connected_at": self.last_connected_at,
            "last_message_at": self.last_message_at,
            "reconnect_count": self.reconnect_count,
            "message_count": self.message_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
        }


def create_highres_sidecar_app(
    config: HighresSidecarConfig | None = None,
    *,
    store: SQLiteStore | None = None,
    cache: HighresGpuCache | None = None,
    stream_client: HighresStreamClient | None = None,
) -> FastAPI:
    config = config or HighresSidecarConfig.from_env()
    store = store or SQLiteStore(config.db_path)
    cache = cache or HighresGpuCache(retention_seconds=config.retention_seconds)
    stream_client = stream_client or HighresStreamClient(config, cache)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if store.connection is None:
            store.open()
        task = asyncio.create_task(stream_client.run_forever(), name="constella-highres-stream")
        app.state.store = store
        app.state.highres_cache = cache
        app.state.stream_client = stream_client
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            store.close()

    app = FastAPI(
        title="Constella Highres Sidecar",
        version="0.2.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.store = store
    app.state.highres_cache = cache
    app.state.stream_client = stream_client

    @app.get("/api/highres/status")
    async def highres_status() -> dict[str, object]:
        return {
            **app.state.highres_cache.status(),
            "sidecar": True,
            "stream": app.state.stream_client.status(),
        }

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
        return {
            "enabled": True,
            "items": query_jobs(
                app.state.store,
                q=q,
                user=user,
                pid=pid,
                node_id=node_id,
                status=status,
                since=since,
                until=until,
                max_duration_seconds=max_duration_seconds,
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
        payload = job_curve(
            app.state.store,
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
        job = get_job(app.state.store, job_key)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {"enabled": True, "item": job}

    return app
