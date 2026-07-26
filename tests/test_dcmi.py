from __future__ import annotations

import ctypes

from constella.dcmi import (
    DCMIChipInfo,
    DCMIHbmInfo,
    DCMIMemoryInfo,
    DCMIPcieInfo,
    DCMIPowerInfo,
    DCMIProcessInfo,
    DCMISampler,
)


class FakeDCMI:
    def dcmi_get_card_num_list(self, count_ptr, cards, _length: int) -> int:
        ctypes.cast(count_ptr, ctypes.POINTER(ctypes.c_int)).contents.value = 1
        cards[0] = 2
        return 0

    def dcmi_get_device_num_in_card(self, _card_id: int, count_ptr) -> int:
        ctypes.cast(count_ptr, ctypes.POINTER(ctypes.c_int)).contents.value = 1
        return 0

    def dcmi_get_device_chip_info_v2(self, _card_id: int, _device_id: int, info_ptr) -> int:
        info = ctypes.cast(info_ptr, ctypes.POINTER(DCMIChipInfo)).contents
        info.chip_name[:9] = b"Ascend910"
        info.chip_ver[:2] = b"V1"
        return 0

    def dcmi_get_device_hbm_info(self, _card_id: int, _device_id: int, info_ptr) -> int:
        info = ctypes.cast(info_ptr, ctypes.POINTER(DCMIHbmInfo)).contents
        info.memory_size = 65536
        info.memory_usage = 16384
        info.freq = 1600
        return 0

    def dcmi_get_device_memory_info_v3(self, _card_id: int, _device_id: int, _info_ptr) -> int:
        return -1

    def dcmi_get_device_pcie_info_v2(self, _card_id: int, _device_id: int, info_ptr) -> int:
        info = ctypes.cast(info_ptr, ctypes.POINTER(DCMIPcieInfo)).contents
        info.bus = 0x95
        return 0

    def dcmi_get_device_utilization_rate(
        self, _card_id: int, _device_id: int, input_type: int, value_ptr
    ) -> int:
        ctypes.cast(value_ptr, ctypes.POINTER(ctypes.c_uint)).contents.value = {
            2: 73,
            6: 25,
        }[input_type]
        return 0

    def dcmi_get_device_temperature(self, _card_id: int, _device_id: int, value_ptr) -> int:
        ctypes.cast(value_ptr, ctypes.POINTER(ctypes.c_int)).contents.value = 48
        return 0

    def dcmi_get_device_power_info(self, _card_id: int, _device_id: int, value_ptr) -> int:
        ctypes.cast(value_ptr, ctypes.POINTER(ctypes.c_int)).contents.value = 1540
        return 0

    def dcmi_get_device_info(
        self, _card_id: int, _device_id: int, _main: int, _sub: int, info_ptr, _size_ptr
    ) -> int:
        info = ctypes.cast(info_ptr, ctypes.POINTER(DCMIPowerInfo)).contents
        info.soc_rated_power_mw = 949200
        return 0

    def dcmi_get_device_resource_info(self, _card_id: int, _device_id: int, items, count_ptr) -> int:
        items[0] = DCMIProcessInfo(pid=4321, memory_bytes=2 * 1024 * 1024 * 1024)
        ctypes.cast(count_ptr, ctypes.POINTER(ctypes.c_int)).contents.value = 1
        return 0

    def dcmi_get_driver_version(self, buffer, _length: int) -> int:
        ctypes.memmove(buffer, b"25.5.0\0", 7)
        return 0

    def dcmi_get_dcmi_version(self, buffer, _length: int) -> int:
        ctypes.memmove(buffer, b"25.5.0\0", 7)
        return 0


def test_dcmi_sampler_maps_metrics_and_processes(monkeypatch) -> None:
    sampler = object.__new__(DCMISampler)
    sampler.process_interval = 5.0
    sampler._lib = FakeDCMI()
    sampler._last_process_at = 0.0
    sampler._next_process_at = 0.0
    sampler._process_snapshot = {}
    monkeypatch.setattr("constella.dcmi._process_name", lambda _pid: "python")
    monkeypatch.setattr("constella.dcmi._process_user", lambda _pid: "alice")

    snapshot = sampler.sample()

    assert snapshot.source == "dcmi"
    assert snapshot.driver_version == "25.5.0"
    assert len(snapshot.gpus) == 1
    assert snapshot.gpus[0].name == "Ascend910"
    assert snapshot.gpus[0].uuid == "ascend-0000:95:00.0"
    assert snapshot.gpus[0].utilization_gpu == 73
    assert snapshot.gpus[0].utilization_mem == 25
    assert snapshot.gpus[0].memory_used_mb == 16384
    assert snapshot.gpus[0].temperature_c == 48
    assert snapshot.gpus[0].power_watts == 154.0
    assert snapshot.gpus[0].power_limit_watts == 949.2
    assert snapshot.gpus[0].card_id == "2"
    assert snapshot.gpus[0].die_id == 0
    assert snapshot.gpus[0].processes[0].gpu_memory_mb == 2048


def test_dcmi_sampler_falls_back_to_ddr_memory() -> None:
    class DdrDCMI(FakeDCMI):
        def dcmi_get_device_hbm_info(self, _card_id: int, _device_id: int, _info_ptr) -> int:
            return -1

        def dcmi_get_device_memory_info_v3(self, _card_id: int, _device_id: int, info_ptr) -> int:
            info = ctypes.cast(info_ptr, ctypes.POINTER(DCMIMemoryInfo)).contents
            info.memory_size = 16384
            info.memory_available = 4096
            info.freq = 800
            return 0

        def dcmi_get_device_utilization_rate(
            self, _card_id: int, _device_id: int, input_type: int, value_ptr
        ) -> int:
            value = 10 if input_type == 2 else 0
            ctypes.cast(value_ptr, ctypes.POINTER(ctypes.c_uint)).contents.value = value
            return 0

    sampler = object.__new__(DCMISampler)
    sampler.process_interval = 5.0
    sampler._lib = DdrDCMI()
    sampler._last_process_at = 0.0
    sampler._next_process_at = float("inf")
    sampler._process_snapshot = {}

    gpu = sampler.sample().gpus[0]
    assert gpu.memory_total_mb == 16384
    assert gpu.memory_used_mb == 12288
    assert gpu.utilization_mem == 75
    assert gpu.clock_mem_mhz == 800
