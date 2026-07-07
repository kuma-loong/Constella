use constella::nvidia_smi::{
    parse_gpu_query_csv, parse_process_query_csv, parse_process_query_csv_with_details,
    ProcessDetails,
};
use constella::procfs::{parent_pid_from_stat, process_start_time_seconds_at, uid_from_status};

#[test]
fn parse_gpu_query_csv_matches_python_contract() {
    let output = concat!(
        "0, GPU-abc, NVIDIA RTX 6000 Ada Generation, 00000000:0F:00.0, 580.65.06, ",
        "35, 73, 32, 81559, 35299, 45781, 370.91, 700.00, 1980, 2619, P0, Default, Disabled\n"
    );

    let (gpus, driver) = parse_gpu_query_csv(output);

    assert_eq!(driver.as_deref(), Some("580.65.06"));
    assert_eq!(gpus.len(), 1);
    let gpu = &gpus[0];
    assert_eq!(gpu.index, 0);
    assert_eq!(gpu.uuid, "GPU-abc");
    assert_eq!(gpu.memory_percent(), 43.3);
    assert_eq!(gpu.power_percent(), 53.0);
    assert_eq!(gpu.clock_sm_mhz, Some(1980));
}

#[test]
fn parse_gpu_query_csv_handles_na() {
    let output = concat!(
        "1, GPU-def, NVIDIA A100-SXM4-80GB, 00000000:34:00.0, 580.65.06, ",
        "N/A, N/A, N/A, 81559, 0, 81080, N/A, 700.00, N/A, N/A, P0, Default, Disabled\n"
    );

    let (gpus, _) = parse_gpu_query_csv(output);

    assert_eq!(gpus[0].temperature_c, 0);
    assert_eq!(gpus[0].utilization_gpu, 0);
    assert_eq!(gpus[0].clock_sm_mhz, None);
}

#[test]
fn parse_process_query_csv_matches_python_contract() {
    let processes =
        parse_process_query_csv("GPU-abc, 1234, python, 4096\nGPU-abc, 2222, python, 1024\n");

    assert!(processes.contains_key("GPU-abc"));
    assert_eq!(
        processes["GPU-abc"]
            .iter()
            .map(|process| process.pid)
            .collect::<Vec<_>>(),
        vec![1234, 2222]
    );
    assert_eq!(
        processes["GPU-abc"]
            .iter()
            .map(|process| process.gpu_memory_mb)
            .sum::<i64>(),
        5120
    );
}

#[test]
fn parse_process_query_csv_includes_parent_identity() {
    let processes = parse_process_query_csv_with_details("GPU-abc, 1234, python, 4096\n", |pid| {
        ProcessDetails {
            ppid: Some(4321),
            user: Some("alice".to_string()),
            process_start_time: Some(if pid == 1234 { 90.0 } else { 0.0 }),
            parent_start_time: Some(80.0),
            cmdline: Some("python train.py".to_string()),
            detail_status: "ok".to_string(),
            ..Default::default()
        }
    });
    let process = &processes["GPU-abc"][0];

    assert_eq!(process.ppid, Some(4321));
    assert_eq!(process.user.as_deref(), Some("alice"));
    assert_eq!(process.process_start_time, Some(90.0));
    assert_eq!(process.parent_start_time, Some(80.0));
    assert_eq!(process.task_name.as_deref(), Some("train.py"));
    assert!(process.cmdline_hash.is_some());
}

#[test]
fn parent_pid_from_proc_stat_handles_spaces_in_comm() {
    let stat = "123 (python train.py) S 42 42 42 0 -1 4194304 0 0 0 0 0 0 0 0 20 0 1 0 1000\n";

    assert_eq!(parent_pid_from_stat(stat), Some(42));
}

#[test]
fn procfs_uid_from_status_uses_real_uid_column() {
    let status = "Name:\tpython\nUid:\t1001\t1001\t1001\t1001\nGid:\t1001\t1001\t1001\t1001\n";

    assert_eq!(uid_from_status(status), Some(1001));
}

#[test]
fn procfs_process_start_time_uses_boot_time_and_start_ticks() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("stat"), "cpu  1 2 3\nbtime 100\n").unwrap();
    let process_dir = dir.path().join("1234");
    std::fs::create_dir(&process_dir).unwrap();
    std::fs::write(
        process_dir.join("stat"),
        "1234 (python train.py) S 42 0 0 0 0 0 0 0 0 0 0 0 0 0 20 0 1 0 250\n",
    )
    .unwrap();

    let started_at = process_start_time_seconds_at(dir.path(), 1234).unwrap();

    assert!(started_at > 100.0);
    assert!(started_at < 110.0);
}
