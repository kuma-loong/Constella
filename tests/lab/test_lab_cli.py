import pytest

from constella_lab import cli
from constella_lab.config import LabConfig


@pytest.mark.parametrize("args,timeout", [([], 10.0), (["--graceful-timeout", "2.5"], 2.5)])
def test_lab_cli_configures_bounded_shutdown(tmp_path, monkeypatch, args, timeout):
    config = LabConfig(db_path=tmp_path / "identity.sqlite3",
        access_team_domain="https://test.cloudflareaccess.com", access_audience="test",
        bootstrap_admin_email="admin@example.com", public_origin="https://preview.example.com")
    monkeypatch.setattr(cli.LabConfig, "from_env", lambda: config)
    captured = {}
    monkeypatch.setattr(cli, "create_lab_app", lambda **kwargs: "app")
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    cli.main(["serve", "--port", "8875", *args])
    assert captured["lifespan"] == "on"
    assert captured["timeout_graceful_shutdown"] == timeout


@pytest.mark.parametrize("value", ["nan", "inf", "-1"])
def test_lab_cli_rejects_invalid_shutdown_timeout(value):
    with pytest.raises(SystemExit):
        cli.main(["serve", f"--graceful-timeout={value}"])
