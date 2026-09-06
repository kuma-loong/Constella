"""Exclude desktop infrastructure from workload tracking."""

from dataclasses import replace
from pathlib import PurePosixPath

from .schema import GpuInfo, GpuProcess

EXCLUDED_USERS = frozenset({"gdm", "Debian-gdm"})
EXCLUDED_PROCESS_NAMES = frozenset({
    "X", "Xorg", "Xwayland", "gnome-shell", "kwin_x11", "kwin_wayland",
    "plasmashell", "mutter", "cinnamon", "muffin", "xfwm4", "picom", "compton",
})


def is_excluded_process(process: GpuProcess) -> bool:
    # Match executable basenames, never task labels or command-line arguments:
    # e.g. `python Xorg.py` and render workloads must still be tracked.
    return process.user in EXCLUDED_USERS or any(
        PurePosixPath(value).name in EXCLUDED_PROCESS_NAMES
        for value in (process.name, process.exe)
        if value
    )


def filter_gpu_processes(gpu: GpuInfo) -> GpuInfo:
    """Copy process lists without changing the original or device telemetry."""
    return replace(
        gpu,
        processes=[process for process in gpu.processes if not is_excluded_process(process)],
        other_users=[other for other in gpu.other_users if other.user not in EXCLUDED_USERS],
    )
