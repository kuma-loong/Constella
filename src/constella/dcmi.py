from __future__ import annotations

import ctypes
import ctypes.util
import os
import pwd
import socket
import time

from .procfs import (
    process_parent_pid,
    process_runtime_seconds,
    process_start_time_seconds,
)
from .schema import GpuHardwareInfo, GpuInfo, GpuProcess, NodeHardware, Snapshot

DCMI_SUCCESS = 0
DCMI_MAX_CARD_NUM = 64
DCMI_MAX_CHIP_NAME_LEN = 32
DCMI_MAX_PROCESS_NUM = 1024
DCMI_UTILIZATION_RATE_AICORE = 2
DCMI_UTILIZATION_RATE_HBM = 6
DCMI_MAIN_CMD_LP = 8
DCMI_LP_SUB_CMD_GET_POWER_INFO = 10


class DCMIUnavailable(RuntimeError):
    pass


class DCMIChipInfo(ctypes.Structure):
    _fields_ = [
        ("chip_type", ctypes.c_ubyte * DCMI_MAX_CHIP_NAME_LEN),
        ("chip_name", ctypes.c_ubyte * DCMI_MAX_CHIP_NAME_LEN),
        ("chip_ver", ctypes.c_ubyte * DCMI_MAX_CHIP_NAME_LEN),
        ("aicore_cnt", ctypes.c_uint),
        ("npu_name", ctypes.c_ubyte * DCMI_MAX_CHIP_NAME_LEN),
    ]


class DCMIHbmInfo(ctypes.Structure):
    _fields_ = [
        ("memory_size", ctypes.c_ulonglong),
        ("freq", ctypes.c_uint),
        ("memory_usage", ctypes.c_ulonglong),
        ("temperature", ctypes.c_int),
        ("bandwidth_utilization", ctypes.c_uint),
    ]


class DCMIMemoryInfo(ctypes.Structure):
    _fields_ = [
        ("memory_size", ctypes.c_ulonglong),
        ("memory_available", ctypes.c_ulonglong),
        ("freq", ctypes.c_uint),
        ("hugepage_size", ctypes.c_ulong),
        ("hugepages_total", ctypes.c_ulong),
        ("hugepages_free", ctypes.c_ulong),
        ("utilization", ctypes.c_uint),
        ("reserved", ctypes.c_ubyte * 60),
    ]


class DCMIPcieInfo(ctypes.Structure):
    _fields_ = [
        ("vendor_id", ctypes.c_uint),
        ("subvendor_id", ctypes.c_uint),
        ("device_id", ctypes.c_uint),
        ("subdevice_id", ctypes.c_uint),
        ("domain", ctypes.c_int),
        ("bus", ctypes.c_uint),
        ("device", ctypes.c_uint),
        ("function", ctypes.c_uint),
        ("reserved", ctypes.c_ubyte * 32),
    ]


class DCMIProcessInfo(ctypes.Structure):
    _fields_ = [("pid", ctypes.c_int), ("memory_bytes", ctypes.c_ulong)]


class DCMIPowerInfo(ctypes.Structure):
    _fields_ = [("soc_rated_power_mw", ctypes.c_uint), ("reserved", ctypes.c_ubyte * 32)]


def _load_library() -> ctypes.CDLL:
    candidates = (
        os.environ.get("CONSTELLA_DCMI_LIBRARY"),
        ctypes.util.find_library("dcmi"),
        "/usr/local/dcmi/libdcmi.so",
        "/usr/local/Ascend/driver/lib64/driver/libdcmi.so",
        "libdcmi.so",
    )
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ctypes.CDLL(candidate)
        except OSError:
            continue
    raise DCMIUnavailable("Cannot find libdcmi.so")


def _setup(lib: ctypes.CDLL) -> None:
    lib.dcmi_init.restype = ctypes.c_int
    lib.dcmi_get_card_num_list.argtypes = [
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_int,
    ]
    lib.dcmi_get_card_num_list.restype = ctypes.c_int
    lib.dcmi_get_device_num_in_card.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    lib.dcmi_get_device_num_in_card.restype = ctypes.c_int
    lib.dcmi_get_device_chip_info_v2.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(DCMIChipInfo),
    ]
    lib.dcmi_get_device_chip_info_v2.restype = ctypes.c_int
    lib.dcmi_get_device_hbm_info.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(DCMIHbmInfo),
    ]
    lib.dcmi_get_device_hbm_info.restype = ctypes.c_int
    lib.dcmi_get_device_memory_info_v3.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(DCMIMemoryInfo),
    ]
    lib.dcmi_get_device_memory_info_v3.restype = ctypes.c_int
    lib.dcmi_get_device_pcie_info_v2.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(DCMIPcieInfo),
    ]
    lib.dcmi_get_device_pcie_info_v2.restype = ctypes.c_int
    lib.dcmi_get_device_temperature.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.dcmi_get_device_temperature.restype = ctypes.c_int
    lib.dcmi_get_device_power_info.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.dcmi_get_device_power_info.restype = ctypes.c_int
    lib.dcmi_get_device_utilization_rate.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_uint),
    ]
    lib.dcmi_get_device_utilization_rate.restype = ctypes.c_int
    lib.dcmi_get_device_resource_info.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(DCMIProcessInfo),
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.dcmi_get_device_resource_info.restype = ctypes.c_int
    lib.dcmi_get_device_info.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint),
    ]
    lib.dcmi_get_device_info.restype = ctypes.c_int
    for name in ("dcmi_get_driver_version", "dcmi_get_dcmi_version"):
        func = getattr(lib, name)
        func.argtypes = [ctypes.c_char_p, ctypes.c_uint]
        func.restype = ctypes.c_int


def _decode(value: ctypes.Array[ctypes.c_ubyte]) -> str:
    return bytes(value).split(b"\0", 1)[0].decode("utf-8", errors="replace")


def _process_name(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as handle:
            return handle.read().strip() or "?"
    except OSError:
        return "?"


def _process_user(pid: int) -> str | None:
    try:
        return pwd.getpwuid(os.stat(f"/proc/{pid}").st_uid).pw_name
    except (KeyError, OSError):
        return None


class DCMISampler:
    def __init__(self, process_interval: float = 5.0):
        self.process_interval = max(1.0, process_interval)
        self._lib = _load_library()
        _setup(self._lib)
        rc = self._lib.dcmi_init()
        if rc != DCMI_SUCCESS:
            raise DCMIUnavailable(f"dcmi_init failed with code {rc}")
        self._last_process_at = 0.0
        self._next_process_at = 0.0
        self._process_snapshot: dict[str, list[GpuProcess]] = {}

    def close(self) -> None:
        pass

    def set_process_interval(self, process_interval: float) -> None:
        self.process_interval = max(1.0, process_interval)
        if self._last_process_at:
            self._next_process_at = self._last_process_at + self.process_interval

    def sample(self) -> Snapshot:
        started = time.monotonic()
        devices = self._devices()
        now = time.monotonic()
        collect_processes = now >= self._next_process_at
        gpus = [
            self._sample_device(index, card_id, device_id, collect_processes)
            for index, (card_id, device_id) in enumerate(devices)
        ]
        if collect_processes:
            self._last_process_at = time.monotonic()
            self._next_process_at = self._last_process_at + self.process_interval
        return Snapshot(
            ok=True,
            source="dcmi",
            hostname=socket.gethostname(),
            timestamp=time.time(),
            elapsed_ms=round((time.monotonic() - started) * 1000, 1),
            gpus=gpus,
            driver_version=self._version("dcmi_get_driver_version"),
            nvml_version=self._version("dcmi_get_dcmi_version"),
        )

    def hardware_inventory(self) -> NodeHardware:
        gpus: list[GpuHardwareInfo] = []
        for index, (card_id, device_id) in enumerate(self._devices()):
            chip = self._chip_info(card_id, device_id)
            pci_bus_id = self._pci_bus_id(card_id, device_id)
            gpus.append(
                GpuHardwareInfo(
                    index=index,
                    uuid=f"ascend-{pci_bus_id or f'{card_id}-{device_id}'}",
                    name=_decode(chip.chip_name) or "Ascend NPU",
                    architecture=_decode(chip.chip_ver) or None,
                )
            )
        return NodeHardware(gpus=gpus)

    def _devices(self) -> list[tuple[int, int]]:
        card_count = ctypes.c_int(0)
        cards = (ctypes.c_int * DCMI_MAX_CARD_NUM)()
        rc = self._lib.dcmi_get_card_num_list(
            ctypes.byref(card_count), cards, DCMI_MAX_CARD_NUM
        )
        if rc != DCMI_SUCCESS:
            raise DCMIUnavailable(f"dcmi_get_card_num_list failed with code {rc}")
        devices: list[tuple[int, int]] = []
        for card_id in list(cards)[: card_count.value]:
            device_count = ctypes.c_int(0)
            rc = self._lib.dcmi_get_device_num_in_card(card_id, ctypes.byref(device_count))
            if rc != DCMI_SUCCESS:
                continue
            devices.extend((card_id, device_id) for device_id in range(device_count.value))
        if not devices:
            raise DCMIUnavailable("DCMI returned no devices")
        return devices

    def _sample_device(
        self,
        index: int,
        card_id: int,
        device_id: int,
        collect_processes: bool,
    ) -> GpuInfo:
        chip = self._chip_info(card_id, device_id)
        hbm = DCMIHbmInfo()
        hbm_rc = self._lib.dcmi_get_device_hbm_info(card_id, device_id, ctypes.byref(hbm))
        memory_total, memory_used, memory_frequency = self._memory_values(
            card_id, device_id, hbm, hbm_rc
        )
        pci_bus_id = self._pci_bus_id(card_id, device_id)
        uuid = f"ascend-{pci_bus_id or f'{card_id}-{device_id}'}"
        gpu = GpuInfo(
            index=index,
            device_type="ascend",
            card_id=str(card_id),
            die_id=device_id,
            uuid=uuid,
            name=_decode(chip.chip_name) or "Ascend NPU",
            pci_bus_id=pci_bus_id,
            utilization_gpu=self._utilization(card_id, device_id, DCMI_UTILIZATION_RATE_AICORE),
            utilization_mem=self._memory_utilization(card_id, device_id, memory_total, memory_used),
            memory_total_mb=memory_total,
            memory_used_mb=memory_used,
            memory_free_mb=max(0, memory_total - memory_used),
            temperature_c=self._int_value("dcmi_get_device_temperature", card_id, device_id),
            power_watts=(
                round(self._int_value("dcmi_get_device_power_info", card_id, device_id) / 10.0, 1)
                if device_id == 0
                else 0.0
            ),
            power_limit_watts=(
                self._rated_power_watts(card_id, device_id) if device_id == 0 else 0.0
            ),
            clock_mem_mhz=memory_frequency or None,
        )
        if collect_processes:
            gpu.processes = self._processes(card_id, device_id)
            self._process_snapshot[uuid] = gpu.processes
        else:
            gpu.processes = self._process_snapshot.get(uuid, [])
        return gpu

    def _rated_power_watts(self, card_id: int, device_id: int) -> float:
        info = DCMIPowerInfo()
        size = ctypes.c_uint(ctypes.sizeof(info))
        rc = self._lib.dcmi_get_device_info(
            card_id,
            device_id,
            DCMI_MAIN_CMD_LP,
            DCMI_LP_SUB_CMD_GET_POWER_INFO,
            ctypes.byref(info),
            ctypes.byref(size),
        )
        if rc != DCMI_SUCCESS:
            return 0.0
        return round(info.soc_rated_power_mw / 1000.0, 1)

    def _memory_values(
        self,
        card_id: int,
        device_id: int,
        hbm: DCMIHbmInfo,
        hbm_rc: int,
    ) -> tuple[int, int, int]:
        if hbm_rc == DCMI_SUCCESS and hbm.memory_size:
            return int(hbm.memory_size), int(hbm.memory_usage), int(hbm.freq)
        memory = DCMIMemoryInfo()
        rc = self._lib.dcmi_get_device_memory_info_v3(
            card_id, device_id, ctypes.byref(memory)
        )
        if rc != DCMI_SUCCESS:
            return 0, 0, 0
        total = int(memory.memory_size)
        return total, max(0, total - int(memory.memory_available)), int(memory.freq)

    def _memory_utilization(
        self, card_id: int, device_id: int, total: int, used: int
    ) -> int:
        utilization = self._utilization(card_id, device_id, DCMI_UTILIZATION_RATE_HBM)
        if utilization or not total:
            return utilization
        return round(used * 100 / total)

    def _chip_info(self, card_id: int, device_id: int) -> DCMIChipInfo:
        chip = DCMIChipInfo()
        rc = self._lib.dcmi_get_device_chip_info_v2(card_id, device_id, ctypes.byref(chip))
        if rc != DCMI_SUCCESS:
            raise DCMIUnavailable(f"dcmi_get_device_chip_info_v2 failed with code {rc}")
        return chip

    def _pci_bus_id(self, card_id: int, device_id: int) -> str | None:
        info = DCMIPcieInfo()
        rc = self._lib.dcmi_get_device_pcie_info_v2(card_id, device_id, ctypes.byref(info))
        if rc != DCMI_SUCCESS:
            return None
        return f"{info.domain:04x}:{info.bus:02x}:{info.device:02x}.{info.function:x}"

    def _utilization(self, card_id: int, device_id: int, input_type: int) -> int:
        value = ctypes.c_uint(0)
        rc = self._lib.dcmi_get_device_utilization_rate(
            card_id, device_id, input_type, ctypes.byref(value)
        )
        return int(value.value) if rc == DCMI_SUCCESS else 0

    def _int_value(self, name: str, card_id: int, device_id: int) -> int:
        value = ctypes.c_int(0)
        rc = getattr(self._lib, name)(card_id, device_id, ctypes.byref(value))
        return int(value.value) if rc == DCMI_SUCCESS else 0

    def _processes(self, card_id: int, device_id: int) -> list[GpuProcess]:
        processes = (DCMIProcessInfo * DCMI_MAX_PROCESS_NUM)()
        count = ctypes.c_int(DCMI_MAX_PROCESS_NUM)
        rc = self._lib.dcmi_get_device_resource_info(
            card_id, device_id, processes, ctypes.byref(count)
        )
        if rc != DCMI_SUCCESS:
            return []
        result: list[GpuProcess] = []
        for info in list(processes)[: count.value]:
            pid = int(info.pid)
            parent_pid = process_parent_pid(pid)
            result.append(
                GpuProcess(
                    pid=pid,
                    name=_process_name(pid),
                    gpu_memory_mb=int(info.memory_bytes // (1024 * 1024)),
                    ppid=parent_pid,
                    user=_process_user(pid),
                    runtime_seconds=process_runtime_seconds(pid),
                    process_start_time=process_start_time_seconds(pid),
                    parent_start_time=(
                        process_start_time_seconds(parent_pid) if parent_pid else None
                    ),
                    detail_status="names",
                )
            )
        return sorted(result, key=lambda process: process.gpu_memory_mb, reverse=True)

    def _version(self, name: str) -> str | None:
        buffer = ctypes.create_string_buffer(256)
        rc = getattr(self._lib, name)(buffer, len(buffer))
        return buffer.value.decode("utf-8", errors="replace") if rc == DCMI_SUCCESS else None


def sample_hardware_inventory() -> NodeHardware | None:
    try:
        return DCMISampler().hardware_inventory()
    except Exception:
        return None
