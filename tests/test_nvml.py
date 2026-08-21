from __future__ import annotations

import ctypes

from constella.nvml import NVMLSampler, architecture_label


def test_architecture_label_maps_known_nvml_values() -> None:
    assert architecture_label(9) == "Hopper"
    assert architecture_label(10) == "Blackwell"
    assert architecture_label(0xFFFFFFFF) is None


def test_nvml_sampler_isolates_unexpected_gpm_failure() -> None:
    class FailingProvider:
        @staticmethod
        def sample(*_args, **_kwargs):
            raise RuntimeError("broken GPM provider")

    sampler = object.__new__(NVMLSampler)
    sampler._gpm = FailingProvider()

    result = sampler._sample_performance(0, ctypes.c_void_p(1), 10.0)

    assert result.status == "error"
    assert result.metrics == {}
    assert result.error == "GPM sample failed: broken GPM provider"
