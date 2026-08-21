from __future__ import annotations

import pytest

from constella import cli
from constella.db import SQLiteStore
from constella.performance_rollup import NvidiaGpmRollupBucket
from constella.schema import AcceleratorPerformance


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


def test_tui_command_starts_textual_app(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_init(self, manager_url: str, *, reconnect_delay: float) -> None:
        captured.update(manager_url=manager_url, reconnect_delay=reconnect_delay)

    def fake_run(self) -> None:
        captured["ran"] = True

    monkeypatch.setattr("constella_tui.app.ConstellaTui.__init__", fake_init)
    monkeypatch.setattr("constella_tui.app.ConstellaTui.run", fake_run)

    cli.main(["tui", "--url", "https://gpu.example.com", "--reconnect-delay", "3"])

    assert captured == {
        "manager_url": "https://gpu.example.com",
        "reconnect_delay": 3.0,
        "ran": True,
    }


@pytest.mark.parametrize("delay", ["0", "nan", "inf"])
def test_tui_command_rejects_invalid_reconnect_delay(delay: str) -> None:
    with pytest.raises(SystemExit):
        cli.main(["tui", "--reconnect-delay", delay])


def test_db_rollup_includes_nvidia_gpm(tmp_path, capsys) -> None:
    path = tmp_path / "constella.db"
    store = SQLiteStore(path)
    store.open()
    bucket = NvidiaGpmRollupBucket(
        bucket_start=20.0,
        node_id="node-a",
        gpu_uuid="GPU-0",
    )
    bucket.add(
        AcceleratorPerformance(
            profile="nvidia.gpm.v1",
            status="available",
            sampled_at=21.0,
            interval_ms=1000.0,
            metrics={"nvidia.gpm.sm_active": 42.0},
        )
    )
    store.upsert_nvidia_gpm_rollups([bucket.to_row(20)])
    store.close()

    cli.main(
        [
            "db",
            "rollup",
            "--path",
            str(path),
            "--from-bucket-seconds",
            "20",
            "--to-bucket-seconds",
            "120",
        ]
    )

    assert "1 NVIDIA GPM buckets" in capsys.readouterr().out
