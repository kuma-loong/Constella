from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI

from constella.app import create_app

from .auth import IdentityVerifier
from .config import LabConfig
from .extension import LabExtension
from .store import LabStore


def packaged_frontend_dist() -> Path:
    return Path(__file__).resolve().parent / "dist"


def create_lab_app(
    *,
    config: LabConfig | None = None,
    store: LabStore | None = None,
    verifier: IdentityVerifier | None = None,
    frontend_dist: Path | None = None,
    **core_options: Any,
) -> FastAPI:
    resolved_config = config or LabConfig.from_env()
    extension = LabExtension(resolved_config, store=store, verifier=verifier)
    return create_app(
        extension=extension,
        frontend_dist=frontend_dist or packaged_frontend_dist(),
        **core_options,
    )
