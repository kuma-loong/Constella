use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, RecvTimeoutError, SyncSender, TrySendError};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::{json, Value};
use thiserror::Error;

use crate::schema::{process_session_id, NodeSnapshot};

pub const RAW_SNAPSHOT_RETENTION_SECONDS: f64 = 12.0 * 60.0 * 60.0;
pub const ROLLUP_20S: i64 = 20;
pub const ROLLUP_2M: i64 = 120;
pub const ROLLUP_1H: i64 = 3600;
pub const ROLLUP_20S_RETENTION_SECONDS: f64 = 7.0 * 24.0 * 60.0 * 60.0;
pub const ROLLUP_2M_RETENTION_SECONDS: f64 = 60.0 * 24.0 * 60.0 * 60.0;
pub const ROLLUP_1H_RETENTION_SECONDS: f64 = 365.0 * 24.0 * 60.0 * 60.0;
pub const SESSION_CLOSE_INTERVAL_SECONDS: f64 = 30.0 * 60.0;

#[derive(Debug, Error)]
pub enum DbError {
    #[error(transparent)]
    Sqlite(#[from] rusqlite::Error),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error("SQLiteStore is not open")]
    NotOpen,
    #[error("unsupported history bucket: {0}")]
    UnsupportedHistoryBucket(i64),
    #[error("unsupported rollup path: {0} -> {1}")]
    UnsupportedRollupPath(i64, i64),
}

#[derive(Debug)]
pub struct SQLiteStore {
    path: PathBuf,
    connection: Option<Connection>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct DbSinkConfig {
    pub path: PathBuf,
    pub queue_size: usize,
    pub raw_snapshot_interval: f64,
}

impl DbSinkConfig {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self {
            path: path.into(),
            queue_size: 1024,
            raw_snapshot_interval: 0.0,
        }
    }
}

#[derive(Debug, Clone)]
pub struct AsyncDbSink {
    path: Arc<PathBuf>,
    sender: SyncSender<NodeSnapshot>,
    dropped_samples: Arc<AtomicU64>,
    write_errors: Arc<AtomicU64>,
}

impl AsyncDbSink {
    pub fn start(config: DbSinkConfig) -> Result<Self, DbError> {
        let mut store = SQLiteStore::new(config.path.clone());
        store.open()?;
        store.close();

        let queue_size = config.queue_size.max(1);
        let (sender, receiver) = sync_channel(queue_size);
        let dropped_samples = Arc::new(AtomicU64::new(0));
        let write_errors = Arc::new(AtomicU64::new(0));
        let worker_errors = Arc::clone(&write_errors);
        let path = Arc::new(config.path.clone());
        let worker_path = Arc::clone(&path);
        thread::Builder::new()
            .name("constella-db-writer".to_string())
            .spawn(move || {
                let mut worker = DbWorker::new(config, receiver, worker_errors);
                if let Err(error) = worker.run() {
                    tracing::warn!(path = %worker_path.display(), error = %error, "db worker stopped");
                }
            })?;

        Ok(Self {
            path,
            sender,
            dropped_samples,
            write_errors,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn submit_node_snapshot(&self, snapshot: &NodeSnapshot) -> bool {
        match self.sender.try_send(snapshot.clone()) {
            Ok(()) => true,
            Err(TrySendError::Full(_)) => {
                self.dropped_samples.fetch_add(1, Ordering::Relaxed);
                false
            }
            Err(TrySendError::Disconnected(_)) => {
                self.write_errors.fetch_add(1, Ordering::Relaxed);
                false
            }
        }
    }

    pub fn dropped_samples(&self) -> u64 {
        self.dropped_samples.load(Ordering::Relaxed)
    }

    pub fn write_errors(&self) -> u64 {
        self.write_errors.load(Ordering::Relaxed)
    }
}

#[derive(Debug)]
struct DbWorker {
    config: DbSinkConfig,
    receiver: Receiver<NodeSnapshot>,
    write_errors: Arc<AtomicU64>,
    last_raw_at: f64,
    last_20s_flush_at: f64,
    last_2m_rollup_at: f64,
    last_1h_rollup_at: f64,
    last_prune_at: f64,
    last_session_close_at: f64,
    rollup_20s: HashMap<(i64, String, String), RollupBucket>,
}

impl DbWorker {
    fn new(
        config: DbSinkConfig,
        receiver: Receiver<NodeSnapshot>,
        write_errors: Arc<AtomicU64>,
    ) -> Self {
        Self {
            config,
            receiver,
            write_errors,
            last_raw_at: 0.0,
            last_20s_flush_at: 0.0,
            last_2m_rollup_at: 0.0,
            last_1h_rollup_at: 0.0,
            last_prune_at: 0.0,
            last_session_close_at: 0.0,
            rollup_20s: HashMap::new(),
        }
    }

    fn run(&mut self) -> Result<(), DbError> {
        let mut store = SQLiteStore::new(self.config.path.clone());
        store.open()?;
        loop {
            match self.receiver.recv_timeout(Duration::from_secs(10)) {
                Ok(snapshot) => {
                    if let Err(error) = self.write_snapshot(&store, &snapshot) {
                        self.write_errors.fetch_add(1, Ordering::Relaxed);
                        tracing::warn!(error = %error, "failed to write node snapshot");
                    }
                }
                Err(RecvTimeoutError::Timeout) => {
                    let now = unix_now();
                    if let Err(error) = self.run_scheduled_maintenance(&store, now) {
                        self.write_errors.fetch_add(1, Ordering::Relaxed);
                        tracing::warn!(error = %error, "failed to run db maintenance");
                    }
                }
                Err(RecvTimeoutError::Disconnected) => {
                    let _ = self.flush_rollups(&store, unix_now());
                    store.close();
                    return Ok(());
                }
            }
        }
    }

    fn write_snapshot(
        &mut self,
        store: &SQLiteStore,
        snapshot: &NodeSnapshot,
    ) -> Result<(), DbError> {
        let write_raw = self.should_write_raw(snapshot.sampled_at);
        store.write_node_snapshot(snapshot, write_raw)?;
        self.accumulate_snapshot(snapshot);
        self.run_scheduled_maintenance(store, unix_now().max(snapshot.sampled_at))
    }

    fn should_write_raw(&mut self, sampled_at: f64) -> bool {
        if self.config.raw_snapshot_interval <= 0.0 {
            return false;
        }
        if sampled_at - self.last_raw_at >= self.config.raw_snapshot_interval {
            self.last_raw_at = sampled_at;
            true
        } else {
            false
        }
    }

    fn accumulate_snapshot(&mut self, snapshot: &NodeSnapshot) {
        let bucket_start = ((snapshot.sampled_at / ROLLUP_20S as f64).floor() as i64) * ROLLUP_20S;
        for gpu in &snapshot.gpus {
            let key = (bucket_start, snapshot.node_id.clone(), gpu.uuid.clone());
            self.rollup_20s
                .entry(key)
                .or_insert_with(|| {
                    RollupBucket::new(bucket_start as f64, &snapshot.node_id, &gpu.uuid)
                })
                .add_gpu(gpu);
        }
    }

    fn flush_rollups(&mut self, store: &SQLiteStore, now: f64) -> Result<usize, DbError> {
        let ready_keys = self
            .rollup_20s
            .iter()
            .filter_map(|(key, bucket)| {
                if bucket.bucket_start + ROLLUP_20S as f64 <= now - ROLLUP_20S as f64 {
                    Some(key.clone())
                } else {
                    None
                }
            })
            .collect::<Vec<_>>();
        let rows = ready_keys
            .iter()
            .filter_map(|key| self.rollup_20s.remove(key))
            .map(|bucket| bucket.to_row(ROLLUP_20S))
            .collect::<Vec<_>>();
        store.upsert_gpu_metric_rollups(&rows)
    }

    fn run_scheduled_maintenance(&mut self, store: &SQLiteStore, now: f64) -> Result<(), DbError> {
        if now - self.last_20s_flush_at >= 10.0 {
            self.flush_rollups(store, now)?;
            self.last_20s_flush_at = now;
        }
        if now - self.last_2m_rollup_at >= 120.0 {
            store.rollup_gpu_metric_rollups(ROLLUP_20S, ROLLUP_2M, now)?;
            self.last_2m_rollup_at = now;
        }
        if now - self.last_1h_rollup_at >= 3600.0 {
            store.rollup_gpu_metric_rollups(ROLLUP_2M, ROLLUP_1H, now)?;
            self.last_1h_rollup_at = now;
        }
        if now - self.last_prune_at >= 600.0 {
            store.prune_rollups(now, None)?;
            store.prune_raw_snapshots(now, RAW_SNAPSHOT_RETENTION_SECONDS)?;
            self.last_prune_at = now;
        }
        if now - self.last_session_close_at >= SESSION_CLOSE_INTERVAL_SECONDS {
            store.close_stale_sessions(now, 300.0)?;
            self.last_session_close_at = now;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq)]
struct RollupBucket {
    bucket_start: f64,
    node_id: String,
    gpu_uuid: String,
    sum_gpu_utilization: f64,
    max_gpu_utilization: f64,
    sum_memory_used_mb: f64,
    max_memory_used_mb: i64,
    sum_power_watts: f64,
    max_power_watts: f64,
    sum_temperature_c: f64,
    max_temperature_c: i64,
    sample_count: i64,
}

impl RollupBucket {
    fn new(bucket_start: f64, node_id: &str, gpu_uuid: &str) -> Self {
        Self {
            bucket_start,
            node_id: node_id.to_string(),
            gpu_uuid: gpu_uuid.to_string(),
            sum_gpu_utilization: 0.0,
            max_gpu_utilization: 0.0,
            sum_memory_used_mb: 0.0,
            max_memory_used_mb: 0,
            sum_power_watts: 0.0,
            max_power_watts: 0.0,
            sum_temperature_c: 0.0,
            max_temperature_c: 0,
            sample_count: 0,
        }
    }

    fn add_gpu(&mut self, gpu: &crate::schema::GpuInfo) {
        self.sample_count += 1;
        self.sum_gpu_utilization += gpu.utilization_gpu as f64;
        self.max_gpu_utilization = self.max_gpu_utilization.max(gpu.utilization_gpu as f64);
        self.sum_memory_used_mb += gpu.memory_used_mb as f64;
        self.max_memory_used_mb = self.max_memory_used_mb.max(gpu.memory_used_mb);
        self.sum_power_watts += gpu.power_watts;
        self.max_power_watts = self.max_power_watts.max(gpu.power_watts);
        self.sum_temperature_c += gpu.temperature_c as f64;
        self.max_temperature_c = self.max_temperature_c.max(gpu.temperature_c);
    }

    fn to_row(self, bucket_seconds: i64) -> RollupRow {
        let count = self.sample_count.max(1) as f64;
        RollupRow {
            bucket_start: self.bucket_start,
            bucket_seconds,
            node_id: self.node_id,
            gpu_uuid: self.gpu_uuid,
            avg_gpu_utilization: self.sum_gpu_utilization / count,
            max_gpu_utilization: self.max_gpu_utilization,
            avg_memory_used_mb: self.sum_memory_used_mb / count,
            max_memory_used_mb: self.max_memory_used_mb,
            avg_power_watts: self.sum_power_watts / count,
            max_power_watts: self.max_power_watts,
            avg_temperature_c: self.sum_temperature_c / count,
            max_temperature_c: self.max_temperature_c,
            sample_count: self.sample_count,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct MaintenanceResult {
    pub closed_sessions: usize,
    pub rollups_2m: usize,
    pub rollups_1h: usize,
    pub pruned_rollups: usize,
    pub pruned_raw_snapshots: usize,
}

impl MaintenanceResult {
    pub fn to_map(&self) -> BTreeMap<&'static str, usize> {
        BTreeMap::from([
            ("closed_sessions", self.closed_sessions),
            ("pruned_raw_snapshots", self.pruned_raw_snapshots),
            ("pruned_rollups", self.pruned_rollups),
            ("rollups_1h", self.rollups_1h),
            ("rollups_2m", self.rollups_2m),
        ])
    }
}

impl SQLiteStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self {
            path: path.into(),
            connection: None,
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn open(&mut self) -> Result<(), DbError> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let connection = Connection::open(&self.path)?;
        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.pragma_update(None, "foreign_keys", "ON")?;
        self.connection = Some(connection);
        self.initialize()
    }

    pub fn close(&mut self) {
        self.connection = None;
    }

    pub fn connection(&self) -> Result<&Connection, DbError> {
        self.connection.as_ref().ok_or(DbError::NotOpen)
    }

    pub fn initialize(&self) -> Result<(), DbError> {
        self.connection()?.execute_batch(
            r#"
            CREATE TABLE IF NOT EXISTS nodes (
              node_id TEXT PRIMARY KEY,
              hostname TEXT NOT NULL,
              display_name TEXT,
              first_seen_at REAL NOT NULL,
              last_seen_at REAL NOT NULL,
              agent_version TEXT,
              status TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS gpus (
              gpu_id TEXT PRIMARY KEY,
              node_id TEXT NOT NULL,
              uuid TEXT NOT NULL,
              gpu_index INTEGER NOT NULL,
              pci_bus_id TEXT,
              name TEXT NOT NULL,
              memory_total_mb INTEGER NOT NULL,
              first_seen_at REAL NOT NULL,
              last_seen_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS gpu_metric_samples (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              sampled_at REAL NOT NULL,
              node_id TEXT NOT NULL,
              gpu_uuid TEXT NOT NULL,
              utilization_gpu REAL NOT NULL,
              utilization_mem REAL NOT NULL,
              memory_used_mb INTEGER NOT NULL,
              memory_total_mb INTEGER NOT NULL,
              power_watts REAL NOT NULL,
              power_limit_watts REAL NOT NULL,
              temperature_c INTEGER NOT NULL,
              sample_count INTEGER NOT NULL DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS idx_gpu_metric_samples_lookup
              ON gpu_metric_samples(node_id, gpu_uuid, sampled_at);

            CREATE TABLE IF NOT EXISTS gpu_metric_rollups (
              bucket_start REAL NOT NULL,
              bucket_seconds INTEGER NOT NULL,
              node_id TEXT NOT NULL,
              gpu_uuid TEXT NOT NULL,
              avg_gpu_utilization REAL NOT NULL,
              max_gpu_utilization REAL NOT NULL,
              avg_memory_used_mb REAL NOT NULL,
              max_memory_used_mb INTEGER NOT NULL,
              avg_power_watts REAL NOT NULL,
              max_power_watts REAL NOT NULL,
              avg_temperature_c REAL NOT NULL,
              max_temperature_c INTEGER NOT NULL,
              sample_count INTEGER NOT NULL,
              PRIMARY KEY(bucket_start, bucket_seconds, node_id, gpu_uuid)
            );

            CREATE INDEX IF NOT EXISTS idx_gpu_metric_rollups_node_bucket_time
              ON gpu_metric_rollups(node_id, bucket_seconds, bucket_start);

            CREATE TABLE IF NOT EXISTS process_sessions (
              session_id TEXT PRIMARY KEY,
              node_id TEXT NOT NULL,
              pid INTEGER NOT NULL,
              ppid INTEGER,
              process_start_time REAL,
              parent_start_time REAL,
              user TEXT,
              task_name TEXT NOT NULL,
              process_name TEXT NOT NULL,
              exe TEXT,
              cmdline_hash TEXT,
              cmdline_text TEXT,
              first_seen_at REAL NOT NULL,
              last_seen_at REAL NOT NULL,
              duration_seconds REAL NOT NULL,
              status TEXT NOT NULL,
              sample_count INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_process_sessions_user_last_seen
              ON process_sessions(user, last_seen_at);

            CREATE TABLE IF NOT EXISTS process_gpu_usages (
              session_id TEXT NOT NULL,
              node_id TEXT NOT NULL,
              gpu_uuid TEXT NOT NULL,
              first_seen_at REAL NOT NULL,
              last_seen_at REAL NOT NULL,
              max_memory_mb INTEGER NOT NULL,
              avg_memory_mb REAL NOT NULL,
              last_memory_mb INTEGER NOT NULL,
              sample_count INTEGER NOT NULL,
              PRIMARY KEY(session_id, gpu_uuid)
            );

            CREATE INDEX IF NOT EXISTS idx_process_gpu_usages_node_window
              ON process_gpu_usages(node_id, first_seen_at, last_seen_at);

            CREATE TABLE IF NOT EXISTS raw_snapshots (
              sampled_at REAL NOT NULL,
              node_id TEXT NOT NULL,
              payload_json TEXT NOT NULL
            );
            "#,
        )?;
        Ok(())
    }

    pub fn write_node_snapshot(
        &self,
        snapshot: &NodeSnapshot,
        write_raw: bool,
    ) -> Result<(), DbError> {
        let con = self.connection()?;
        let sampled_at = snapshot.sampled_at;
        let mut written_sessions = HashSet::new();
        con.execute(
            r#"
            INSERT INTO nodes (
              node_id, hostname, display_name, first_seen_at, last_seen_at, agent_version, status
            )
            VALUES (?1, ?2, NULL, ?3, ?4, ?5, ?6)
            ON CONFLICT(node_id) DO UPDATE SET
              hostname=excluded.hostname,
              last_seen_at=excluded.last_seen_at,
              agent_version=excluded.agent_version,
              status=excluded.status
            "#,
            params![
                snapshot.node_id,
                snapshot.hostname,
                sampled_at,
                sampled_at,
                snapshot.agent_version,
                snapshot.status
            ],
        )?;
        for gpu in &snapshot.gpus {
            let gpu_id = gpu
                .gpu_id
                .clone()
                .unwrap_or_else(|| format!("{}:{}", snapshot.node_id, gpu.uuid));
            con.execute(
                r#"
                INSERT INTO gpus (
                  gpu_id, node_id, uuid, gpu_index, pci_bus_id, name,
                  memory_total_mb, first_seen_at, last_seen_at
                )
                VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
                ON CONFLICT(gpu_id) DO UPDATE SET
                  gpu_index=excluded.gpu_index,
                  pci_bus_id=excluded.pci_bus_id,
                  name=excluded.name,
                  memory_total_mb=excluded.memory_total_mb,
                  last_seen_at=excluded.last_seen_at
                "#,
                params![
                    gpu_id,
                    snapshot.node_id,
                    gpu.uuid,
                    gpu.index,
                    gpu.pci_bus_id,
                    gpu.name,
                    gpu.memory_total_mb,
                    sampled_at,
                    sampled_at
                ],
            )?;
            for process in &gpu.processes {
                let session_id = process_session_id(&snapshot.node_id, process);
                let task_name = process
                    .task_name
                    .clone()
                    .filter(|value| !value.is_empty())
                    .unwrap_or_else(|| {
                        if process.name.is_empty() {
                            format!("unknown:{}", process.pid)
                        } else {
                            process.name.clone()
                        }
                    });
                if written_sessions.insert(session_id.clone()) {
                    con.execute(
                        r#"
                        INSERT INTO process_sessions (
                          session_id, node_id, pid, ppid, process_start_time, parent_start_time,
                          user, task_name, process_name, exe, cmdline_hash, cmdline_text,
                          first_seen_at, last_seen_at, duration_seconds, status, sample_count
                        )
                        VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, 0.0, 'running', 1)
                        ON CONFLICT(session_id) DO UPDATE SET
                          ppid=COALESCE(excluded.ppid, process_sessions.ppid),
                          parent_start_time=COALESCE(excluded.parent_start_time, process_sessions.parent_start_time),
                          user=COALESCE(excluded.user, process_sessions.user),
                          task_name=excluded.task_name,
                          process_name=excluded.process_name,
                          exe=COALESCE(excluded.exe, process_sessions.exe),
                          cmdline_hash=COALESCE(excluded.cmdline_hash, process_sessions.cmdline_hash),
                          cmdline_text=COALESCE(excluded.cmdline_text, process_sessions.cmdline_text),
                          last_seen_at=excluded.last_seen_at,
                          duration_seconds=excluded.last_seen_at - process_sessions.first_seen_at,
                          status='running',
                          sample_count=process_sessions.sample_count + 1
                        "#,
                        params![
                            session_id,
                            snapshot.node_id,
                            process.pid,
                            process.ppid,
                            process.process_start_time,
                            process.parent_start_time,
                            process.user,
                            task_name,
                            process.name,
                            process.exe,
                            process.cmdline_hash,
                            process.cmdline,
                            sampled_at,
                            sampled_at
                        ],
                    )?;
                }
                con.execute(
                    r#"
                    INSERT INTO process_gpu_usages (
                      session_id, node_id, gpu_uuid, first_seen_at, last_seen_at,
                      max_memory_mb, avg_memory_mb, last_memory_mb, sample_count
                    )
                    VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 1)
                    ON CONFLICT(session_id, gpu_uuid) DO UPDATE SET
                      last_seen_at=excluded.last_seen_at,
                      max_memory_mb=MAX(process_gpu_usages.max_memory_mb, excluded.last_memory_mb),
                      avg_memory_mb=(
                        (process_gpu_usages.avg_memory_mb * process_gpu_usages.sample_count)
                        + excluded.last_memory_mb
                      ) / (process_gpu_usages.sample_count + 1),
                      last_memory_mb=excluded.last_memory_mb,
                      sample_count=process_gpu_usages.sample_count + 1
                    "#,
                    params![
                        process_session_id(&snapshot.node_id, process),
                        snapshot.node_id,
                        gpu.uuid,
                        sampled_at,
                        sampled_at,
                        process.gpu_memory_mb,
                        process.gpu_memory_mb as f64,
                        process.gpu_memory_mb
                    ],
                )?;
            }
        }
        if write_raw {
            con.execute(
                "INSERT INTO raw_snapshots(sampled_at, node_id, payload_json) VALUES (?1, ?2, ?3)",
                params![
                    sampled_at,
                    snapshot.node_id,
                    serde_json::to_string(snapshot).unwrap_or_else(|_| "{}".to_string())
                ],
            )?;
        }
        Ok(())
    }

    pub fn upsert_gpu_metric_rollups(&self, rows: &[RollupRow]) -> Result<usize, DbError> {
        let con = self.connection()?;
        for row in rows {
            con.execute(
                r#"
                INSERT INTO gpu_metric_rollups (
                  bucket_start, bucket_seconds, node_id, gpu_uuid,
                  avg_gpu_utilization, max_gpu_utilization,
                  avg_memory_used_mb, max_memory_used_mb,
                  avg_power_watts, max_power_watts,
                  avg_temperature_c, max_temperature_c,
                  sample_count
                )
                VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)
                ON CONFLICT(bucket_start, bucket_seconds, node_id, gpu_uuid) DO UPDATE SET
                  avg_gpu_utilization=excluded.avg_gpu_utilization,
                  max_gpu_utilization=excluded.max_gpu_utilization,
                  avg_memory_used_mb=excluded.avg_memory_used_mb,
                  max_memory_used_mb=excluded.max_memory_used_mb,
                  avg_power_watts=excluded.avg_power_watts,
                  max_power_watts=excluded.max_power_watts,
                  avg_temperature_c=excluded.avg_temperature_c,
                  max_temperature_c=excluded.max_temperature_c,
                  sample_count=excluded.sample_count
                "#,
                params![
                    row.bucket_start,
                    row.bucket_seconds,
                    row.node_id,
                    row.gpu_uuid,
                    row.avg_gpu_utilization,
                    row.max_gpu_utilization,
                    row.avg_memory_used_mb,
                    row.max_memory_used_mb,
                    row.avg_power_watts,
                    row.max_power_watts,
                    row.avg_temperature_c,
                    row.max_temperature_c,
                    row.sample_count
                ],
            )?;
        }
        Ok(rows.len())
    }

    pub fn close_stale_sessions(
        &self,
        now: f64,
        stale_after_seconds: f64,
    ) -> Result<usize, DbError> {
        let cutoff = now - stale_after_seconds;
        Ok(self.connection()?.execute(
            "UPDATE process_sessions SET status='ended', duration_seconds=last_seen_at - first_seen_at WHERE status='running' AND last_seen_at < ?1",
            params![cutoff],
        )?)
    }

    pub fn rollup_gpu_metric_rollups(
        &self,
        from_bucket_seconds: i64,
        to_bucket_seconds: i64,
        now: f64,
    ) -> Result<usize, DbError> {
        if source_bucket(to_bucket_seconds) != Some(from_bucket_seconds) {
            return Err(DbError::UnsupportedRollupPath(
                from_bucket_seconds,
                to_bucket_seconds,
            ));
        }
        let cutoff = now - to_bucket_seconds as f64;
        let mut stmt = self.connection()?.prepare(
            r#"
            SELECT
              target_bucket_start AS bucket_start,
              node_id,
              gpu_uuid,
              SUM(avg_gpu_utilization * sample_count) / SUM(sample_count) AS avg_gpu_utilization,
              MAX(max_gpu_utilization) AS max_gpu_utilization,
              SUM(avg_memory_used_mb * sample_count) / SUM(sample_count) AS avg_memory_used_mb,
              MAX(max_memory_used_mb) AS max_memory_used_mb,
              SUM(avg_power_watts * sample_count) / SUM(sample_count) AS avg_power_watts,
              MAX(max_power_watts) AS max_power_watts,
              SUM(avg_temperature_c * sample_count) / SUM(sample_count) AS avg_temperature_c,
              MAX(max_temperature_c) AS max_temperature_c,
              SUM(sample_count) AS sample_count
            FROM (
              SELECT
                CAST(bucket_start / ?1 AS INTEGER) * ?2 AS target_bucket_start,
                node_id, gpu_uuid, avg_gpu_utilization, max_gpu_utilization,
                avg_memory_used_mb, max_memory_used_mb, avg_power_watts, max_power_watts,
                avg_temperature_c, max_temperature_c, sample_count
              FROM gpu_metric_rollups
              WHERE bucket_seconds = ?3
            )
            GROUP BY target_bucket_start, node_id, gpu_uuid
            HAVING target_bucket_start + ?4 <= ?5 AND sample_count > 0
            "#,
        )?;
        let rows = stmt
            .query_map(
                params![
                    to_bucket_seconds,
                    to_bucket_seconds,
                    from_bucket_seconds,
                    to_bucket_seconds,
                    cutoff
                ],
                |row| RollupRow::from_row(row, to_bucket_seconds),
            )?
            .collect::<Result<Vec<_>, _>>()?;
        self.upsert_gpu_metric_rollups(&rows)
    }

    pub fn prune_raw_snapshots(&self, now: f64, retention_seconds: f64) -> Result<usize, DbError> {
        Ok(self.connection()?.execute(
            "DELETE FROM raw_snapshots WHERE sampled_at < ?1",
            params![now - retention_seconds],
        )?)
    }

    pub fn prune_rollups(&self, now: f64, bucket_seconds: Option<i64>) -> Result<usize, DbError> {
        let buckets = if let Some(bucket) = bucket_seconds {
            vec![normalize_history_bucket(bucket)?]
        } else {
            vec![ROLLUP_20S, ROLLUP_2M, ROLLUP_1H]
        };
        let mut deleted = 0usize;
        for bucket in buckets {
            let Some(retention_seconds) = rollup_retention_seconds(bucket) else {
                return Err(DbError::UnsupportedHistoryBucket(bucket));
            };
            deleted += self.connection()?.execute(
                "DELETE FROM gpu_metric_rollups WHERE bucket_seconds = ?1 AND bucket_start < ?2",
                params![bucket, now - retention_seconds],
            )?;
        }
        Ok(deleted)
    }

    pub fn maintain(
        &self,
        now: f64,
        stale_session_seconds: f64,
        raw_retention_seconds: f64,
    ) -> Result<MaintenanceResult, DbError> {
        Ok(MaintenanceResult {
            closed_sessions: self.close_stale_sessions(now, stale_session_seconds)?,
            rollups_2m: self.rollup_gpu_metric_rollups(ROLLUP_20S, ROLLUP_2M, now)?,
            rollups_1h: self.rollup_gpu_metric_rollups(ROLLUP_2M, ROLLUP_1H, now)?,
            pruned_rollups: self.prune_rollups(now, None)?,
            pruned_raw_snapshots: self.prune_raw_snapshots(now, raw_retention_seconds)?,
        })
    }

    pub fn query_gpu_history(
        &self,
        node_id: Option<&str>,
        gpu_uuid: Option<&str>,
        since: Option<f64>,
        until: Option<f64>,
        bucket_seconds: Option<i64>,
        limit: i64,
    ) -> Result<Vec<Value>, DbError> {
        let bucket_seconds = match bucket_seconds {
            Some(bucket) => normalize_history_bucket(bucket)?,
            None => select_history_bucket(since, until),
        };
        let mut sql = String::from(
            r#"
            SELECT bucket_start, bucket_seconds, node_id, gpu_uuid,
                   avg_gpu_utilization, max_gpu_utilization,
                   avg_memory_used_mb, max_memory_used_mb,
                   avg_power_watts, max_power_watts,
                   avg_temperature_c, max_temperature_c,
                   sample_count
            FROM gpu_metric_rollups
            WHERE bucket_seconds = ?1
            "#,
        );
        let mut values: Vec<rusqlite::types::Value> = vec![bucket_seconds.into()];
        append_filter(&mut sql, &mut values, "node_id", node_id);
        append_filter(&mut sql, &mut values, "gpu_uuid", gpu_uuid);
        append_time_filter(&mut sql, &mut values, "bucket_start >= ", since);
        append_time_filter(&mut sql, &mut values, "bucket_start <= ", until);
        sql.push_str(" ORDER BY bucket_start ASC LIMIT ?");
        values.push(limit.into());
        let mut stmt = self.connection()?.prepare(&sql)?;
        let rows = stmt
            .query_map(rusqlite::params_from_iter(values), history_row_from_rollup)?
            .collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    pub fn query_tasks(
        &self,
        user: Option<&str>,
        status: Option<&str>,
        limit: i64,
    ) -> Result<Vec<Value>, DbError> {
        let mut sql = String::from(
            r#"
            SELECT session_id, node_id, pid, process_start_time, user, task_name,
                   ppid, parent_start_time, process_name, exe, cmdline_hash,
                   first_seen_at, last_seen_at, duration_seconds, status, sample_count
            FROM process_sessions
            WHERE 1 = 1
            "#,
        );
        let mut values: Vec<rusqlite::types::Value> = vec![];
        append_filter(&mut sql, &mut values, "user", user);
        append_filter(&mut sql, &mut values, "status", status);
        sql.push_str(" ORDER BY last_seen_at DESC LIMIT ?");
        values.push(limit.into());
        let mut stmt = self.connection()?.prepare(&sql)?;
        let rows = stmt
            .query_map(rusqlite::params_from_iter(values), task_row)?
            .collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    pub fn query_users(&self) -> Result<Vec<Value>, DbError> {
        let mut stmt = self.connection()?.prepare(
            r#"
            SELECT user, COUNT(*) AS task_count, SUM(duration_seconds) AS total_duration_seconds,
                   MAX(last_seen_at) AS last_seen_at
            FROM process_sessions
            WHERE user IS NOT NULL
            GROUP BY user
            ORDER BY last_seen_at DESC
            "#,
        )?;
        let rows = stmt
            .query_map([], |row| {
                Ok(json!({
                    "user": row.get::<_, String>("user")?,
                    "task_count": row.get::<_, i64>("task_count")?,
                    "total_duration_seconds": row.get::<_, f64>("total_duration_seconds")?,
                    "last_seen_at": row.get::<_, f64>("last_seen_at")?,
                }))
            })?
            .collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    pub fn scalar_i64(&self, sql: &str) -> Result<i64, DbError> {
        Ok(self
            .connection()?
            .query_row(sql, [], |row| row.get(0))
            .optional()?
            .unwrap_or(0))
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct RollupRow {
    pub bucket_start: f64,
    pub bucket_seconds: i64,
    pub node_id: String,
    pub gpu_uuid: String,
    pub avg_gpu_utilization: f64,
    pub max_gpu_utilization: f64,
    pub avg_memory_used_mb: f64,
    pub max_memory_used_mb: i64,
    pub avg_power_watts: f64,
    pub max_power_watts: f64,
    pub avg_temperature_c: f64,
    pub max_temperature_c: i64,
    pub sample_count: i64,
}

impl RollupRow {
    fn from_row(row: &Row<'_>, bucket_seconds: i64) -> rusqlite::Result<Self> {
        Ok(Self {
            bucket_start: row.get("bucket_start")?,
            bucket_seconds,
            node_id: row.get("node_id")?,
            gpu_uuid: row.get("gpu_uuid")?,
            avg_gpu_utilization: row.get("avg_gpu_utilization")?,
            max_gpu_utilization: row.get("max_gpu_utilization")?,
            avg_memory_used_mb: row.get("avg_memory_used_mb")?,
            max_memory_used_mb: row.get("max_memory_used_mb")?,
            avg_power_watts: row.get("avg_power_watts")?,
            max_power_watts: row.get("max_power_watts")?,
            avg_temperature_c: row.get("avg_temperature_c")?,
            max_temperature_c: row.get("max_temperature_c")?,
            sample_count: row.get("sample_count")?,
        })
    }
}

fn source_bucket(bucket_seconds: i64) -> Option<i64> {
    match bucket_seconds {
        ROLLUP_2M => Some(ROLLUP_20S),
        ROLLUP_1H => Some(ROLLUP_2M),
        _ => None,
    }
}

fn normalize_history_bucket(bucket_seconds: i64) -> Result<i64, DbError> {
    if rollup_retention_seconds(bucket_seconds).is_some() {
        Ok(bucket_seconds)
    } else {
        Err(DbError::UnsupportedHistoryBucket(bucket_seconds))
    }
}

fn select_history_bucket(since: Option<f64>, until: Option<f64>) -> i64 {
    let Some(since) = since else {
        return ROLLUP_20S;
    };
    let end = until.unwrap_or_else(unix_now);
    let span = (end - since).max(0.0);
    if span <= ROLLUP_20S_RETENTION_SECONDS {
        ROLLUP_20S
    } else if span <= ROLLUP_2M_RETENTION_SECONDS {
        ROLLUP_2M
    } else {
        ROLLUP_1H
    }
}

fn rollup_retention_seconds(bucket_seconds: i64) -> Option<f64> {
    match bucket_seconds {
        ROLLUP_20S => Some(ROLLUP_20S_RETENTION_SECONDS),
        ROLLUP_2M => Some(ROLLUP_2M_RETENTION_SECONDS),
        ROLLUP_1H => Some(ROLLUP_1H_RETENTION_SECONDS),
        _ => None,
    }
}

fn append_filter(
    sql: &mut String,
    values: &mut Vec<rusqlite::types::Value>,
    column: &str,
    value: Option<&str>,
) {
    if let Some(value) = value.filter(|value| !value.is_empty()) {
        sql.push_str(" AND ");
        sql.push_str(column);
        sql.push_str(" = ?");
        values.push(value.to_string().into());
    }
}

fn append_time_filter(
    sql: &mut String,
    values: &mut Vec<rusqlite::types::Value>,
    expression: &str,
    value: Option<f64>,
) {
    if let Some(value) = value {
        sql.push_str(" AND ");
        sql.push_str(expression);
        sql.push('?');
        values.push(value.into());
    }
}

fn history_row_from_rollup(row: &Row<'_>) -> rusqlite::Result<Value> {
    let bucket_start = row.get::<_, f64>("bucket_start")?;
    let avg_gpu_utilization = row.get::<_, f64>("avg_gpu_utilization")?;
    let avg_memory_used_mb = row.get::<_, f64>("avg_memory_used_mb")?;
    let avg_power_watts = row.get::<_, f64>("avg_power_watts")?;
    let avg_temperature_c = row.get::<_, f64>("avg_temperature_c")?;
    Ok(json!({
        "sampled_at": bucket_start,
        "bucket_start": bucket_start,
        "bucket_seconds": row.get::<_, i64>("bucket_seconds")?,
        "node_id": row.get::<_, String>("node_id")?,
        "gpu_uuid": row.get::<_, String>("gpu_uuid")?,
        "utilization_gpu": avg_gpu_utilization,
        "memory_used_mb": avg_memory_used_mb,
        "power_watts": avg_power_watts,
        "temperature_c": avg_temperature_c,
        "avg_gpu_utilization": avg_gpu_utilization,
        "max_gpu_utilization": row.get::<_, f64>("max_gpu_utilization")?,
        "avg_memory_used_mb": avg_memory_used_mb,
        "max_memory_used_mb": row.get::<_, i64>("max_memory_used_mb")?,
        "avg_power_watts": avg_power_watts,
        "max_power_watts": row.get::<_, f64>("max_power_watts")?,
        "avg_temperature_c": avg_temperature_c,
        "max_temperature_c": row.get::<_, i64>("max_temperature_c")?,
        "sample_count": row.get::<_, i64>("sample_count")?,
    }))
}

fn task_row(row: &Row<'_>) -> rusqlite::Result<Value> {
    Ok(json!({
        "session_id": row.get::<_, String>("session_id")?,
        "node_id": row.get::<_, String>("node_id")?,
        "pid": row.get::<_, i64>("pid")?,
        "process_start_time": row.get::<_, Option<f64>>("process_start_time")?,
        "user": row.get::<_, Option<String>>("user")?,
        "task_name": row.get::<_, String>("task_name")?,
        "ppid": row.get::<_, Option<i64>>("ppid")?,
        "parent_start_time": row.get::<_, Option<f64>>("parent_start_time")?,
        "process_name": row.get::<_, String>("process_name")?,
        "exe": row.get::<_, Option<String>>("exe")?,
        "cmdline_hash": row.get::<_, Option<String>>("cmdline_hash")?,
        "first_seen_at": row.get::<_, f64>("first_seen_at")?,
        "last_seen_at": row.get::<_, f64>("last_seen_at")?,
        "duration_seconds": row.get::<_, f64>("duration_seconds")?,
        "status": row.get::<_, String>("status")?,
        "sample_count": row.get::<_, i64>("sample_count")?,
    }))
}

fn unix_now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .unwrap_or(0.0)
}
