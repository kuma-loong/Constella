from __future__ import annotations

import os
import time

_BOOT_TIME_SECONDS: int | None = None


def _boot_time_seconds() -> int | None:
    global _BOOT_TIME_SECONDS
    if _BOOT_TIME_SECONDS is not None:
        return _BOOT_TIME_SECONDS

    try:
        with open("/proc/stat", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("btime "):
                    _BOOT_TIME_SECONDS = int(line.split()[1])
                    return _BOOT_TIME_SECONDS
    except (OSError, ValueError):
        return None
    return None


def _stat_fields_after_comm(pid: int) -> list[str] | None:
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="replace") as f:
            stat = f.read()
    except OSError:
        return None

    comm_end = stat.rfind(")")
    if comm_end < 0:
        return None

    return stat[comm_end + 2 :].split()


def process_parent_pid(pid: int) -> int | None:
    fields_after_comm = _stat_fields_after_comm(pid)
    if fields_after_comm is None or len(fields_after_comm) <= 1:
        return None
    try:
        return int(fields_after_comm[1])
    except ValueError:
        return None


def process_start_time_seconds(pid: int) -> float | None:
    boot_time = _boot_time_seconds()
    if boot_time is None:
        return None

    try:
        ticks_per_second = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError):
        return None

    fields_after_comm = _stat_fields_after_comm(pid)
    if fields_after_comm is None:
        return None
    if len(fields_after_comm) <= 19:
        return None

    try:
        start_ticks = int(fields_after_comm[19])
    except ValueError:
        return None

    return boot_time + (start_ticks / ticks_per_second)


def process_runtime_seconds(pid: int) -> int | None:
    started_at = process_start_time_seconds(pid)
    if started_at is None:
        return None
    return max(0, int(time.time() - started_at))


def process_exe(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def process_cmdline(pid: int) -> tuple[str | None, str]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except PermissionError:
        return None, "permission_denied"
    except OSError:
        return None, "unavailable"
    if not raw:
        return None, "empty"
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip() or None, "ok"
