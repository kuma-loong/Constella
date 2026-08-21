from __future__ import annotations

import os

NVIDIA_GPM_PROFILE = "nvidia.gpm.v1"

NVIDIA_GPM_METRICS = (
    "nvidia.gpm.sm_active",
    "nvidia.gpm.sm_occupancy",
    "nvidia.gpm.tensor_active",
    "nvidia.gpm.dram_bw_active",
    "nvidia.gpm.fp16_non_tensor_active",
    "nvidia.gpm.fp32_non_tensor_active",
    "nvidia.gpm.fp64_non_tensor_active",
)

PERFORMANCE_STATUSES = frozenset({"warming", "available", "unsupported", "error"})


def performance_profiles(device_type: str) -> list[str]:
    if device_type == "nvidia" and nvidia_gpm_enabled():
        return [NVIDIA_GPM_PROFILE]
    return []


def nvidia_gpm_enabled() -> bool:
    return os.environ.get("CONSTELLA_NVML_GPM", "auto").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def nvidia_gpm_rollup_enabled() -> bool:
    return os.environ.get("CONSTELLA_NVIDIA_GPM_ROLLUP", "on").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def nvidia_gpm_highres_enabled() -> bool:
    return os.environ.get("CONSTELLA_NVIDIA_GPM_HIGHRES", "on").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
