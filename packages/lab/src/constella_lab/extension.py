from __future__ import annotations

from fastapi import FastAPI

from .api import build_lab_router
from .activity import build_activity_router
from .activity_usage import user_statistics
from .auth import CloudflareAccessVerifier, IdentityVerifier, LabAuthMiddleware
from .config import LabConfig
from .store import LabStore


class LabExtension:
    def __init__(
        self,
        config: LabConfig,
        *,
        store: LabStore | None = None,
        verifier: IdentityVerifier | None = None,
    ) -> None:
        self.config = config
        self.store = store or LabStore(config.db_path)
        self.verifier = verifier or CloudflareAccessVerifier(
            team_domain=config.access_team_domain,
            audience=config.access_audience,
        )

    def configure(self, app: FastAPI) -> None:
        app.state.lab_store = self.store
        app.state.user_usage_resolver = lambda rows, start, end: user_statistics(
            rows, self.store.list_bindings(active_only=False), start, end
        )
        app.include_router(build_activity_router(self.store))
        app.add_middleware(
            LabAuthMiddleware,
            config=self.config,
            store=self.store,
            verifier=self.verifier,
        )
        app.include_router(build_lab_router(config=self.config, store=self.store))

    async def start(self, app: FastAPI) -> None:
        self.store.open()

    async def stop(self, app: FastAPI) -> None:
        self.store.close()
