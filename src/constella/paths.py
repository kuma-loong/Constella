from __future__ import annotations

import os
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent


def package_dir() -> Path:
    return PACKAGE_DIR


def source_project_root() -> Path | None:
    candidate = PACKAGE_DIR.parents[1]
    source_package = candidate / "src" / "constella"
    if (candidate / "pyproject.toml").is_file() and source_package.resolve() == PACKAGE_DIR:
        return candidate
    return None


def default_project_root() -> Path:
    return source_project_root() or package_dir()


def default_build_root() -> Path:
    root = source_project_root()
    if root is not None:
        return root / ".constella-build"
    cache_home = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return cache_home.expanduser() / "constella" / "build"


def resolve_frontend_dist(override: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if override is not None:
        candidates.append(override)
    else:
        env_path = os.environ.get("CONSTELLA_FRONTEND_DIST")
        if env_path:
            candidates.append(Path(env_path))
        root = source_project_root()
        if root is not None:
            candidates.append(root / "frontend" / "dist")
        candidates.append(package_dir() / "frontend" / "dist")

    for candidate in candidates:
        resolved = candidate.expanduser()
        if (resolved / "index.html").is_file():
            return resolved
    return None
