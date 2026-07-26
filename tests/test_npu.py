from constella.npu import parse_npu_smi


def test_parse_npu_smi_inventory_and_processes() -> None:
    output = """
| npu-smi 23.0.2.1                 Version: 23.0.2.1 |
| NPU   Name                | Health | Power(W) Temp(C) |
| 0     910B2C              | OK     | 88.6 51          |
| 0                         | 0000:5A:00.0 | 0 0 / 0 20701/ 65536 |
| NPU     Chip              | Process id | Process name | Process memory(MB) |
| 0       0                 | 124528 | python3.8 | 17400 |
"""
    gpus, driver = parse_npu_smi(output)
    assert driver == "23.0.2.1"
    assert len(gpus) == 1
    assert gpus[0].name == "910B2C"
    assert gpus[0].memory_used_mb == 20701
    assert gpus[0].memory_total_mb == 65536
    assert gpus[0].processes[0].pid == 124528
    assert gpus[0].processes[0].gpu_memory_mb == 17400


def test_parse_npu_smi_supports_devices_without_hbm() -> None:
    output = """
| npu-smi 23.0.0 Version: 23.0.0 |
| 0 310B4 | Alarm | 0.0 65 |
| 0 0 | NA | 0 3628 / 15609 |
"""
    gpus, _ = parse_npu_smi(output)
    assert gpus[0].memory_used_mb == 3628
    assert gpus[0].memory_total_mb == 15609
    assert gpus[0].error == "Alarm"
