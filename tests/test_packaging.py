from __future__ import annotations

import os

from fastapi.testclient import TestClient

from constella.app import create_app
from constella.cli import main
from constella.cluster_control import AGENT_RUNTIME_MODULES, prepare_agent_runtime
from constella.paths import resolve_frontend_dist


def test_resolve_frontend_dist_uses_explicit_index(tmp_path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>Constella</html>\n", encoding="utf-8")

    assert resolve_frontend_dist(frontend) == frontend


def test_create_app_serves_explicit_frontend_dist(tmp_path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>Constella packaged UI</html>\n", encoding="utf-8")
    (frontend / "ready.txt").write_text("ok\n", encoding="utf-8")

    client = TestClient(create_app(frontend_dist=frontend))

    assert client.get("/ready.txt").text == "ok\n"
    response = client.get("/overview")
    assert response.status_code == 200
    assert "Constella packaged UI" in response.text


def test_cli_serve_sets_explicit_runtime_environment(tmp_path, monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    db_path = tmp_path / "constella.db"
    token_file = tmp_path / "agent-token"
    highres_token_file = tmp_path / "highres-token"
    frontend = tmp_path / "frontend"

    def fake_run(app: str, **kwargs: object) -> None:
        calls.append({"app": app, **kwargs})

    for name in (
        "CONSTELLA_AGENT_TOKEN_FILE",
        "CONSTELLA_HIGHRES_TOKEN_FILE",
        "CONSTELLA_DB_PATH",
        "CONSTELLA_DB_QUEUE_SIZE",
        "CONSTELLA_RAW_SNAPSHOT_SECONDS",
        "CONSTELLA_FRONTEND_DIST",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("constella.cli.uvicorn.run", fake_run)

    main(
        [
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "18875",
            "--refresh",
            "1.0",
            "--process-refresh",
            "5.0",
            "--agent-token-file",
            str(token_file),
            "--highres-token-file",
            str(highres_token_file),
            "--db-path",
            str(db_path),
            "--db-queue-size",
            "16",
            "--raw-snapshot-seconds",
            "0.5",
            "--frontend-dir",
            str(frontend),
        ]
    )

    assert calls == [
        {
            "app": "constella.app:create_app",
            "host": "127.0.0.1",
            "port": 18875,
            "factory": True,
            "log_level": "info",
            "lifespan": "on",
        }
    ]
    assert os.environ["CONSTELLA_AGENT_TOKEN_FILE"] == str(token_file)
    assert os.environ["CONSTELLA_HIGHRES_TOKEN_FILE"] == str(highres_token_file)
    assert os.environ["CONSTELLA_DB_PATH"] == str(db_path)
    assert os.environ["CONSTELLA_DB_QUEUE_SIZE"] == "16"
    assert os.environ["CONSTELLA_RAW_SNAPSHOT_SECONDS"] == "0.5"
    assert os.environ["CONSTELLA_FRONTEND_DIST"] == str(frontend)


def test_prepare_agent_runtime_accepts_installed_package_dir(tmp_path) -> None:
    package_dir = tmp_path / "site-packages" / "constella"
    package_dir.mkdir(parents=True)
    for module in AGENT_RUNTIME_MODULES:
        (package_dir / module).write_text("# test module\n", encoding="utf-8")
    build_root = tmp_path / "cache"

    runtime = prepare_agent_runtime(package_dir, build_root=build_root)

    assert runtime == build_root / "agent-runtime"
    assert (runtime / "constella" / "agent_main.py").exists()
    assert (runtime / "websockets").is_dir()
