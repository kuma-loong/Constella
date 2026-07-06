use constella::collector::SnapshotCollector;
use constella::schema::{GpuInfo, Snapshot};
use constella::settings::ALLOWED_REFRESH_INTERVALS;

fn snapshot() -> Snapshot {
    Snapshot {
        ok: true,
        source: "test".to_string(),
        hostname: "node".to_string(),
        timestamp: 1.0,
        elapsed_ms: 2.0,
        gpus: vec![GpuInfo {
            index: 0,
            utilization_gpu: 50,
            memory_total_mb: 100,
            memory_used_mb: 25,
            power_watts: 100.0,
            power_limit_watts: 200.0,
            temperature_c: 40,
            ..Default::default()
        }],
        driver_version: None,
        cuda_driver_version: None,
        nvml_version: None,
        error: None,
        seq: 0,
        refresh_interval: 1.0,
        history: Default::default(),
    }
}

#[test]
fn collector_accepts_allowed_refresh_intervals() {
    let mut collector = SnapshotCollector::new(1.0, 3.0, 120).unwrap();

    for interval in ALLOWED_REFRESH_INTERVALS {
        assert_eq!(collector.set_refresh_interval(interval).unwrap(), interval);
        assert_eq!(collector.refresh_interval, interval);
    }
}

#[test]
fn collector_rejects_unsupported_refresh_intervals() {
    let mut collector = SnapshotCollector::new(1.0, 3.0, 120).unwrap();

    for interval in [0.25, 3.0, 10.0] {
        assert!(collector.set_refresh_interval(interval).is_err());
    }
}

#[test]
fn publish_snapshot_sets_runtime_refresh_interval_and_history() {
    let mut collector = SnapshotCollector::new(1.0, 3.0, 2).unwrap();
    collector.set_refresh_interval(2.0).unwrap();

    let first = collector.publish(snapshot());
    let mut second_input = snapshot();
    second_input.gpus[0].utilization_gpu = 80;
    let second = collector.publish(second_input);

    assert_eq!(first.seq, 1);
    assert_eq!(second.seq, 2);
    assert_eq!(second.refresh_interval, 2.0);
    assert_eq!(second.history["0"]["gpu"], vec![50.0, 80.0]);
    assert_eq!(collector.snapshot().unwrap().seq, 2);
}
