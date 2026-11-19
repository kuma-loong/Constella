use crate::archive;
use crate::cluster::{NodeMetadata, gpu_id, raw_snapshot, valid};
use crate::gpm_rollup;
use constella_protocol::{Sample, Settings, unix_time};
use rusqlite::{Connection, OpenFlags, Transaction, params};
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering::Relaxed};
use std::time::Duration;
use tokio::sync::{Semaphore, mpsc};

pub struct Ingest {
    pub node_id: String,
    pub hostname: String,
    pub agent_version: String,
    pub seq: u64,
    pub received_at: f64,
    pub settings: Settings,
    pub sample: Arc<Sample>,
}

#[derive(Default)]
struct Stats {
    healthy: AtomicBool,
    written: AtomicU64,
    dropped: AtomicU64,
    last_commit_lag_micros: AtomicU64,
    archive_healthy: AtomicBool,
}

pub struct Database {
    path: PathBuf,
    queue: Option<mpsc::Sender<Ingest>>,
    writer: Option<std::thread::JoinHandle<()>>,
    stats: Arc<Stats>,
    readers: Arc<Semaphore>,
}

impl Database {
    pub fn open(path: PathBuf, capacity: usize) -> Result<Self, crate::Error> {
        let raw_interval = std::env::var("CONSTELLA_RAW_SNAPSHOT_SECONDS")
            .unwrap_or_else(|_| "0".into())
            .parse::<f64>()?;
        if !raw_interval.is_finite() {
            return Err("CONSTELLA_RAW_SNAPSHOT_SECONDS must be finite".into());
        }
        let conn = initialize(&path)?;
        let stats = Arc::new(Stats::default());
        stats.healthy.store(true, Relaxed);
        stats.archive_healthy.store(true, Relaxed);
        let (queue, receiver) = mpsc::channel(capacity);
        let writer_stats = stats.clone();
        let writer = std::thread::Builder::new()
            .name("sqlite-writer".into())
            .spawn(move || write_loop(conn, receiver, writer_stats, raw_interval))?;
        Ok(Self {
            path,
            queue: Some(queue),
            writer: Some(writer),
            stats,
            readers: Arc::new(Semaphore::new(4)),
        })
    }

    fn queue(&self) -> &mpsc::Sender<Ingest> {
        self.queue
            .as_ref()
            .expect("database queue lives until shutdown")
    }

    pub fn submit(&self, ingest: Ingest) {
        if self.queue().try_send(ingest).is_err() {
            self.stats.dropped.fetch_add(1, Relaxed);
        }
    }

    pub fn healthy(&self) -> bool {
        self.stats.healthy.load(Relaxed)
            && self.stats.archive_healthy.load(Relaxed)
            && !self.queue().is_closed()
    }

    pub fn status(&self) -> Value {
        let queue = self.queue();
        json!({
            "enabled": true, "healthy": self.healthy(),
            "queue_depth": queue.max_capacity() - queue.capacity(),
            "queue_capacity": queue.max_capacity(),
            "written_samples": self.stats.written.load(Relaxed),
            "archive_healthy": self.stats.archive_healthy.load(Relaxed),
            "dropped_samples": self.stats.dropped.load(Relaxed),
            "last_commit_lag_ms": self.stats.last_commit_lag_micros.load(Relaxed) as f64 / 1000.0,
        })
    }

    pub async fn read<T, F>(&self, read: F) -> Result<T, crate::Error>
    where
        T: Send + 'static,
        F: FnOnce(&Connection) -> rusqlite::Result<T> + Send + 'static,
    {
        let slot = self.readers.clone().try_acquire_owned()?;
        let path = self.path.clone();
        Ok(tokio::task::spawn_blocking(move || {
            let _slot = slot;
            let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
            conn.busy_timeout(Duration::from_secs(3))?;
            read(&conn)
        })
        .await??)
    }
}

impl Drop for Database {
    fn drop(&mut self) {
        drop(self.queue.take());
        if let Some(writer) = self.writer.take()
            && writer.join().is_err()
        {
            eprintln!("SQLite writer stopped unexpectedly");
        }
    }
}

pub fn migrate(path: &std::path::Path) -> Result<(), crate::Error> {
    if !path.is_file() {
        return Err("SQLite migration requires an existing CONSTELLA_DB_PATH".into());
    }
    initialize(path)?;
    Ok(())
}

fn initialize(path: &std::path::Path) -> Result<Connection, crate::Error> {
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::create_dir_all(parent)?;
    }
    let mut conn = Connection::open(path)?;
    conn.busy_timeout(Duration::from_secs(3))?;
    conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")?;
    {
        let tx = conn.transaction()?;
        tx.execute_batch(include_str!("sql/schema.sql"))?;
        initialize_legacy_columns(&tx)?;
        gpm_rollup::initialize(&tx)?;
        archive::initialize(&tx)?;
        tx.commit()?;
    }
    Ok(conn)
}

fn initialize_legacy_columns(tx: &Transaction<'_>) -> rusqlite::Result<()> {
    for (table, columns) in [
        (
            "gpus",
            &[
                ("device_type", "TEXT NOT NULL DEFAULT 'nvidia'"),
                ("card_id", "TEXT"),
                ("die_id", "INTEGER"),
            ][..],
        ),
        ("process_sessions", &[("user_uid", "INTEGER")][..]),
    ] {
        let mut statement = tx.prepare(&format!("PRAGMA table_info({table})"))?;
        let existing: HashSet<String> = statement
            .query_map([], |row| row.get(1))?
            .collect::<rusqlite::Result<_>>()?;
        for (column, definition) in columns {
            if !existing.contains(*column) {
                tx.execute_batch(&format!(
                    "ALTER TABLE {table} ADD COLUMN {column} {definition}"
                ))?;
            }
        }
    }
    Ok(())
}

fn write_loop(
    mut conn: Connection,
    mut receiver: mpsc::Receiver<Ingest>,
    stats: Arc<Stats>,
    raw_interval: f64,
) {
    let runtime = match tokio::runtime::Builder::new_current_thread()
        .enable_time()
        .build()
    {
        Ok(runtime) => runtime,
        Err(error) => {
            eprintln!("SQLite writer scheduler: {error}");
            return;
        }
    };
    runtime.block_on(async {
        let gpm = gpm_rollup::enabled().then(gpm_rollup::statement);
        let mut raw = RawSnapshots::new(raw_interval);
        let mut archive = archive::Archive::new();
        let tick = tokio::time::sleep(Duration::from_secs(10));
        tokio::pin!(tick);
        loop {
            tokio::select! {
                message = receiver.recv() => {
                    let Some(ingest) = message else { break };
                    commit(&mut conn, &ingest, gpm.as_deref(), &mut raw, &stats);
                }
                _ = &mut tick => {
                    let wait = match archive.maintain(&mut conn, unix_time()) {
                        Ok(lagging) => {
                            stats.archive_healthy.store(true, Relaxed);
                            if lagging { 2 } else { 30 }
                        }
                        Err(error) => {
                            stats.archive_healthy.store(false, Relaxed);
                            eprintln!("SQLite archive: {error}");
                            30
                        }
                    };
                    tick.as_mut().reset(tokio::time::Instant::now() + Duration::from_secs(wait));
                }
            }
        }
    });
}

struct RawSnapshots {
    interval: f64,
    last: HashMap<String, f64>,
}

impl RawSnapshots {
    fn new(interval: f64) -> Self {
        Self {
            interval,
            last: HashMap::new(),
        }
    }

    fn due(&self, ingest: &Ingest) -> bool {
        self.interval > 0.0
            && self
                .last
                .get(&ingest.node_id)
                .is_none_or(|at| ingest.sample.sampled_at - at >= self.interval)
    }
}

fn commit(
    conn: &mut Connection,
    ingest: &Ingest,
    gpm: Option<&str>,
    raw: &mut RawSnapshots,
    stats: &Stats,
) {
    let write_raw = raw.due(ingest);
    match write_sample(conn, ingest, gpm, write_raw) {
        Ok(written) => {
            if written {
                if write_raw {
                    raw.last
                        .insert(ingest.node_id.clone(), ingest.sample.sampled_at);
                }
                stats.written.fetch_add(1, Relaxed);
                let lag = (unix_time() - ingest.sample.sampled_at) * 1_000_000.0;
                stats.last_commit_lag_micros.store(lag as u64, Relaxed);
            }
            stats.healthy.store(true, Relaxed);
        }
        Err(error) => {
            stats.healthy.store(false, Relaxed);
            stats.dropped.fetch_add(1, Relaxed);
            eprintln!("SQLite ingest: {error}");
        }
    }
}

fn write_sample(
    conn: &mut Connection,
    ingest: &Ingest,
    gpm: Option<&str>,
    write_raw: bool,
) -> rusqlite::Result<bool> {
    let tx = conn.transaction()?;
    let sample = &ingest.sample;
    let node = &ingest.node_id;
    let at = sample.sampled_at;
    let bucket = (at / 20.0).floor() * 20.0;
    let status = if sample.error.is_some() {
        "error"
    } else {
        "online"
    };
    let written = tx
        .prepare_cached(include_str!("sql/node.sql"))?
        .execute(params![
            node,
            ingest.hostname,
            at,
            at,
            ingest.agent_version,
            status,
        ])?;
    if written == 0 {
        return Ok(false);
    }
    {
        let mut gpu_stmt = tx.prepare_cached(include_str!("sql/gpu.sql"))?;
        let mut rollup_stmt = tx.prepare_cached(include_str!("sql/rollup.sql"))?;
        let mut performance_stmt = gpm.map(|sql| tx.prepare_cached(sql)).transpose()?;
        let mut process_stmt = tx.prepare_cached(include_str!("sql/process.sql"))?;
        let mut process_gpu_stmt = tx.prepare_cached(include_str!("sql/process_gpu.sql"))?;
        let mut sessions = HashSet::new();
        for gpu in &sample.gpus {
            gpu_stmt.execute(params![
                gpu_id(node, gpu),
                node,
                gpu.uuid,
                gpu.index,
                gpu.pci_bus_id,
                gpu.name,
                gpu.device_type,
                gpu.card_id,
                gpu.die_id,
                gpu.memory_total_mb,
                at,
                at,
            ])?;
            let uuid = if gpu.uuid.is_empty() || gpu.uuid == "unknown" {
                gpu_id(node, gpu)
            } else {
                gpu.uuid.clone()
            };
            if valid(
                gpu,
                &[
                    "utilization_gpu",
                    "utilization_mem",
                    "memory_total_mb",
                    "memory_used_mb",
                    "memory_free_mb",
                    "power_watts",
                    "power_limit_watts",
                    "temperature_c",
                ],
            ) {
                rollup_stmt.execute(params![
                    bucket,
                    node,
                    uuid,
                    gpu.utilization_gpu,
                    gpu.memory_used_mb,
                    gpu.power_watts,
                    gpu.temperature_c,
                ])?;
            }
            if let Some((statement, performance)) =
                performance_stmt.as_mut().zip(gpu.performance.as_ref())
            {
                gpm_rollup::write(statement, bucket, node, &uuid, performance)?;
            }
            let mut seen_pids = HashSet::new();
            for process in &gpu.processes {
                if !seen_pids.insert(process.pid) {
                    continue;
                }
                let started = process
                    .process_start_time
                    .map_or_else(|| "unknown".into(), |at| format!("{at:.6}"));
                let session = format!("{node}:{}:{started}", process.pid);
                if sessions.insert(session.clone()) {
                    let task_name = process
                        .task_name
                        .as_deref()
                        .filter(|name| !name.is_empty())
                        .unwrap_or(&process.name);
                    process_stmt.execute(params![
                        session,
                        node,
                        process.pid,
                        process.ppid,
                        process.process_start_time,
                        process.parent_start_time,
                        process.user,
                        process.user_uid,
                        task_name,
                        process.name,
                        process.exe,
                        process.cmdline_hash,
                        process.cmdline,
                        at,
                        at,
                    ])?;
                }
                process_gpu_stmt.execute(params![
                    session,
                    node,
                    gpu.uuid,
                    at,
                    at,
                    process.gpu_memory_mb,
                    process.gpu_memory_mb as f64,
                    process.gpu_memory_mb,
                ])?;
            }
        }
    }
    if write_raw {
        let metadata = NodeMetadata {
            node_id: node,
            hostname: &ingest.hostname,
            agent_version: &ingest.agent_version,
            seq: ingest.seq,
            received_at: ingest.received_at,
        };
        let payload = raw_snapshot(metadata, sample, ingest.settings)
            .map_err(|error| rusqlite::Error::ToSqlConversionFailure(Box::new(error)))?;
        tx.prepare_cached(
            "INSERT INTO raw_snapshots(sampled_at, node_id, payload_json) VALUES (?, ?, ?)",
        )?
        .execute(params![at, node, payload])?;
    }
    archive::revisit(&tx, at, unix_time())?;
    tx.commit()?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use constella_protocol::{Gpu, Process};

    #[test]
    fn keeps_existing_bucket_and_process_ids_and_skips_replayed_samples() {
        let mut conn = Connection::open_in_memory().unwrap();
        let tx = conn.transaction().unwrap();
        tx.execute_batch(include_str!("sql/schema.sql")).unwrap();
        archive::initialize(&tx).unwrap();
        tx.commit().unwrap();
        let mut ingest = Ingest {
            node_id: "node".into(),
            hostname: "host".into(),
            agent_version: "test".into(),
            seq: 1,
            received_at: 101.02,
            settings: Settings::new(1.0, 5.0).unwrap(),
            sample: Arc::new(Sample {
                sampled_at: 101.0,
                gpus: vec![Gpu {
                    uuid: "GPU-1".into(),
                    utilization_gpu: 15,
                    processes: vec![Process {
                        pid: 42,
                        name: "test".into(),
                        process_start_time: Some(85.5),
                        ..Process::default()
                    }],
                    ..Gpu::default()
                }],
                ..Sample::default()
            }),
        };
        let stats = Stats::default();
        let mut raw = RawSnapshots::new(20.0);
        commit(&mut conn, &ingest, None, &mut raw, &stats);
        assert!(!write_sample(&mut conn, &ingest, None, true).unwrap());

        let sample = Arc::get_mut(&mut ingest.sample).unwrap();
        sample.sampled_at = 102.0;
        sample.gpus[0].utilization_gpu = 17;
        commit(&mut conn, &ingest, None, &mut raw, &stats);
        let mut records = conn
            .prepare("SELECT payload_json FROM raw_snapshots")
            .unwrap();
        let rows: Vec<String> = records
            .query_map([], |row| row.get(0))
            .unwrap()
            .collect::<rusqlite::Result<_>>()
            .unwrap();
        assert_eq!(rows.len(), 1);
        let payload: Value = serde_json::from_str(&rows[0]).unwrap();
        assert_eq!(payload["sampled_at"], 101.0);
        assert_eq!(payload["seq"], 1);
        assert_eq!(payload["gpus"][0]["gpu_id"], "node:GPU-1");
        assert_eq!(payload["gpus"][0]["processes"][0]["pid"], 42);
        let bucket: (f64, i64, f64, i64) = conn.query_row(
            "SELECT bucket_start, bucket_seconds, avg_gpu_utilization, sample_count FROM gpu_metric_rollups",
            [], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
        ).unwrap();
        assert_eq!(bucket, (100.0, 20, 16.0, 2));
        let session: (String, i64, f64) = conn
            .query_row(
                "SELECT session_id, sample_count, duration_seconds FROM process_sessions",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .unwrap();
        assert_eq!(session, ("node:42:85.500000".into(), 2, 1.0));
    }
}
