from __future__ import annotations

import builtins
from io import StringIO

from constella import procfs


def test_process_parent_pid_reads_proc_stat(monkeypatch) -> None:
    real_open = builtins.open

    def fake_open(path: str, *args, **kwargs):
        if path == "/proc/123/stat":
            return StringIO(
                "123 (python train.py) S 42 42 42 0 -1 4194304 0 0 0 0 "
                "0 0 0 0 20 0 1 0 1000\n"
            )
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)

    assert procfs.process_parent_pid(123) == 42
