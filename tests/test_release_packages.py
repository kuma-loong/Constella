from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.3"


def load_project(path: str) -> dict[str, object]:
    with (ROOT / path / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)["project"]


def test_release_distribution_dependency_matrix() -> None:
    full = load_project(".")
    backend = load_project("packages/backend")
    web = load_project("packages/web")
    tui = load_project("packages/tui")

    assert {project["version"] for project in (full, backend, web, tui)} == {VERSION}
    assert backend["name"] == "constella-gpu-backend"
    assert web["dependencies"] == [f"constella-gpu-backend=={VERSION}"]
    assert tui["dependencies"] == ["textual>=8.0.0,<9", "websockets>=13.0"]
    assert full["dependencies"] == [
        f"constella-gpu-tui=={VERSION}",
        f"constella-gpu-web=={VERSION}",
    ]
    assert backend["scripts"] == {"constella": "constella.cli:main"}
    assert tui["scripts"] == {"constella-tui": "constella_tui.app:main"}
    assert "scripts" not in web
    assert "scripts" not in full


def test_release_distribution_module_ownership() -> None:
    assert (ROOT / "packages/backend/src/constella/cli.py").is_file()
    assert not (ROOT / "packages/backend/src/constella_tui").exists()
    assert not (ROOT / "packages/backend/src/constella_web").exists()

    assert (ROOT / "packages/tui/src/constella_tui/app.py").is_file()
    assert not (ROOT / "packages/tui/src/constella").exists()

    assert (ROOT / "packages/web/src/constella_web/__init__.py").is_file()
    assert not (ROOT / "packages/web/src/constella").exists()
