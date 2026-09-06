from dataclasses import replace

import pytest

from constella.process_filter import (
    EXCLUDED_PROCESS_NAMES,
    EXCLUDED_USERS,
    is_excluded_process,
)
from constella.schema import GpuProcess


@pytest.mark.parametrize("name", sorted(EXCLUDED_PROCESS_NAMES))
def test_desktop_executable_names_and_paths_are_excluded(name):
    process = GpuProcess(pid=1, name=name, gpu_memory_mb=10, user="alice")
    assert is_excluded_process(process)
    assert is_excluded_process(replace(process, name=f"/usr/bin/{name}"))
    assert is_excluded_process(replace(process, name="unknown", exe=f"/usr/bin/{name}"))


@pytest.mark.parametrize("user", sorted(EXCLUDED_USERS))
def test_display_manager_users_are_excluded(user):
    assert is_excluded_process(GpuProcess(pid=1, name="python", user=user, gpu_memory_mb=10))


@pytest.mark.parametrize("name", ["python", "Xorg.py", "Xorg-worker", "blender", "?", ""])
def test_workloads_and_unknown_processes_are_preserved(name):
    assert not is_excluded_process(GpuProcess(
        pid=1, name=name, gpu_memory_mb=10, user="gdm-worker", kind="graphics",
        task_name="Xorg", cmdline="python train.py --label gdm --display Xorg",
    ))
