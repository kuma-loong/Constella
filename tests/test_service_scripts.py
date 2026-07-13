from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_service_stop_has_bounded_force_escalation() -> None:
    script = (PROJECT_ROOT / "scripts" / "service" / "stop.sh").read_text(
        encoding="utf-8"
    )

    assert "kill -TERM" in script
    assert "kill -INT" in script
    assert "kill -KILL" in script
    assert "wait_for_exit" in script
    assert script.index('rm -f "$pid_file"\n  echo "$label: stopped') > script.index(
        'echo "$label: failed to stop'
    )
