use constella::nvml::{
    bytes_to_mib, compute_mode_label, milliwatts_to_watts, performance_state_label,
    used_gpu_memory_mib,
};
use nvml_wrapper::enum_wrappers::device::{ComputeMode, PerformanceState};
use nvml_wrapper::enums::device::UsedGpuMemory;

#[test]
fn nvml_unit_conversions_match_snapshot_contract() {
    assert_eq!(bytes_to_mib(80 * 1024 * 1024), 80);
    assert_eq!(milliwatts_to_watts(Some(643_650)), 643.7);
    assert_eq!(milliwatts_to_watts(None), 0.0);
    assert_eq!(
        used_gpu_memory_mib(UsedGpuMemory::Used(25 * 1024 * 1024)),
        25
    );
    assert_eq!(used_gpu_memory_mib(UsedGpuMemory::Unavailable), 0);
}

#[test]
fn nvml_labels_match_nvidia_smi_style_values() {
    assert_eq!(performance_state_label(PerformanceState::Zero), "P0");
    assert_eq!(performance_state_label(PerformanceState::Fifteen), "P15");
    assert_eq!(compute_mode_label(ComputeMode::Default), "Default");
    assert_eq!(
        compute_mode_label(ComputeMode::ExclusiveProcess),
        "Exclusive Process"
    );
}
