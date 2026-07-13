from __future__ import annotations

from constella import cli


def test_serve_configures_graceful_shutdown_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("CONSTELLA_REFRESH_SECONDS", "1.0")
    monkeypatch.setenv("CONSTELLA_PROCESS_SECONDS", "5.0")

    def fake_run(*args, **kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main(["serve", "--graceful-timeout", "7.5"])

    assert captured["timeout_graceful_shutdown"] == 7.5


def test_highres_sidecar_configures_graceful_shutdown_timeout(
    tmp_path, monkeypatch
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("CONSTELLA_DB_PATH", "")
    monkeypatch.setenv("CONSTELLA_HIGHRES_MANAGER_STREAM_URL", "")
    monkeypatch.setenv("CONSTELLA_HIGHRES_TOKEN", "")
    monkeypatch.setenv("CONSTELLA_HIGHRES_RETENTION_SECONDS", "7200")

    def fake_run(*args, **kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main(
        [
            "highres-sidecar",
            "--db-path",
            str(tmp_path / "constella.db"),
            "--graceful-timeout",
            "8",
        ]
    )

    assert captured["timeout_graceful_shutdown"] == 8.0
