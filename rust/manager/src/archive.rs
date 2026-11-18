use constella_protocol::NVIDIA_GPM_METRICS;
use rusqlite::{Connection, OptionalExtension, Transaction, params};

const DAY: f64 = 86_400.0;
const LEVELS: [(i64, f64); 3] = [(20, 7.0 * DAY), (120, 60.0 * DAY), (3600, 365.0 * DAY)];
const BASIC_STEMS: [&str; 4] = [
    "gpu_utilization",
    "memory_used_mb",
    "power_watts",
    "temperature_c",
];

pub fn initialize(tx: &Transaction<'_>) -> rusqlite::Result<()> {
    tx.execute_batch(
        "CREATE TABLE IF NOT EXISTS constella_archive_cursor (
        table_name TEXT NOT NULL,
        target_seconds INTEGER NOT NULL,
        next_bucket REAL NOT NULL,
        PRIMARY KEY (table_name, target_seconds)
    );",
    )
}

struct Edition {
    name: &'static str,
    sql: String,
}

impl Edition {
    fn new(
        name: &'static str,
        weights: &'static str,
        metrics: Vec<&'static str>,
        independent_counts: bool,
    ) -> Self {
        let mut columns = format!("bucket_start, bucket_seconds, node_id, gpu_uuid, {weights}");
        let mut selected = format!(
            "CAST(bucket_start / ?1 AS INTEGER) * ?1,
            ?1, node_id, gpu_uuid, SUM({weights})"
        );
        let mut updates = format!("{weights} = excluded.{weights}");
        for metric in &metrics {
            let count = if independent_counts {
                format!("{metric}_count")
            } else {
                weights.to_owned()
            };
            let avg = format!("avg_{metric}");
            let max = format!("max_{metric}");
            columns.push_str(&format!(", {avg}, {max}"));
            selected.push_str(&format!(
                ", SUM({avg} * {count}) / NULLIF(SUM({count}), 0), MAX({max})"
            ));
            updates.push_str(&format!(", {avg} = excluded.{avg}, {max} = excluded.{max}"));
            if independent_counts {
                columns.push_str(&format!(", {count}"));
                selected.push_str(&format!(", SUM({count})"));
                updates.push_str(&format!(", {count} = excluded.{count}"));
            }
        }
        let sql = format!(
            "INSERT INTO {name} ({columns})
            SELECT {selected} FROM {name}
            WHERE bucket_seconds = ?2 AND bucket_start >= ?3 AND bucket_start < ?4
            GROUP BY CAST(bucket_start / ?1 AS INTEGER), node_id, gpu_uuid
            HAVING SUM({weights}) > 0
            ON CONFLICT(bucket_start, bucket_seconds, node_id, gpu_uuid) DO UPDATE SET {updates}"
        );
        Self { name, sql }
    }

    fn advance(
        &self,
        tx: &Transaction<'_>,
        level: usize,
        now: f64,
        ready: f64,
    ) -> rusqlite::Result<f64> {
        let (source, retention) = LEVELS[level];
        let target = LEVELS[level + 1].0;
        let target_size = target as f64;
        let cutoff = boundary(now - target_size, target).min(boundary(ready, target));
        let earliest = boundary(now - retention, target);
        let previous = tx.query_row(
            "SELECT next_bucket FROM constella_archive_cursor WHERE table_name = ? AND target_seconds = ?",
            params![self.name, target], |row| row.get::<_, f64>(0),
        ).optional()?;
        let start = match previous {
            Some(cursor) => cursor,
            None => {
                // An older Manager may have pruned the first partial source window.
                let first = tx
                    .query_row(
                        &format!(
                    "SELECT bucket_start FROM {} WHERE bucket_seconds = ? AND bucket_start >= ?
                     ORDER BY bucket_start LIMIT 1", self.name,
                ),
                        params![source, earliest],
                        |row| row.get::<_, f64>(0),
                    )
                    .optional()?;
                let initial = (earliest + target_size).min(cutoff);
                first.map_or(cutoff, |at| boundary(at, target).max(initial).min(cutoff))
            }
        };
        if start > cutoff {
            return Ok(start);
        }
        let chunk = if level == 0 { 3_600.0 } else { DAY };
        let next = (start + chunk).min(cutoff);
        if next > start {
            tx.prepare_cached(&self.sql)?
                .execute(params![target, source, start, next])?;
        }
        if previous != Some(next) {
            tx.prepare_cached("INSERT INTO constella_archive_cursor VALUES (?, ?, ?)
                ON CONFLICT(table_name, target_seconds) DO UPDATE SET next_bucket = excluded.next_bucket")?
                .execute(params![self.name, target, next])?;
        }
        Ok(next)
    }

    fn prune(
        &self,
        tx: &Transaction<'_>,
        now: f64,
        middle: f64,
        hour: f64,
    ) -> rusqlite::Result<()> {
        let sql = format!(
            "DELETE FROM {} WHERE rowid IN (
            SELECT rowid FROM {} WHERE bucket_seconds = ? AND bucket_start < ? LIMIT 20000
        )",
            self.name, self.name
        );
        let mut stmt = tx.prepare_cached(&sql)?;
        for ((bucket, retention), ready) in LEVELS.into_iter().zip([middle, hour, f64::INFINITY]) {
            // Only delete source samples whose complete destination buckets can be rebuilt.
            let cutoff = if bucket == 3600 {
                now - retention
            } else {
                let target = if bucket == 20 { 120 } else { 3600 };
                boundary(now - retention, target).min(ready)
            };
            stmt.execute(params![bucket, cutoff])?;
        }
        Ok(())
    }
}

fn boundary(time: f64, width: i64) -> f64 {
    (time / width as f64).floor() * width as f64
}

pub struct Archive {
    basic: Edition,
    gpm: Edition,
    next_prune: f64,
    next_close: f64,
}

impl Archive {
    pub fn new() -> Self {
        Self {
            basic: Edition::new(
                "gpu_metric_rollups",
                "sample_count",
                BASIC_STEMS.to_vec(),
                false,
            ),
            gpm: Edition::new(
                "nvidia_gpm_rollups",
                "expected_count",
                NVIDIA_GPM_METRICS
                    .iter()
                    .map(|(_, name)| name.strip_prefix("nvidia.gpm.").unwrap())
                    .collect(),
                true,
            ),
            next_prune: 0.0,
            next_close: 0.0,
        }
    }

    pub fn maintain(&mut self, conn: &mut Connection, now: f64) -> rusqlite::Result<bool> {
        let tx = conn.transaction()?;
        let mut lagging = false;
        let prune = now >= self.next_prune;
        let close = now >= self.next_close;
        for edition in [&self.basic, &self.gpm] {
            let middle = edition.advance(&tx, 0, now, now)?;
            let hour = edition.advance(&tx, 1, now, middle)?;
            lagging |= middle < boundary(now - 120.0, 120) || hour < boundary(now - 3600.0, 3600);
            if prune {
                edition.prune(&tx, now, middle, hour)?;
            }
        }
        if prune {
            tx.execute(
                "DELETE FROM raw_snapshots WHERE sampled_at < ?",
                [now - DAY / 2.0],
            )?;
        }
        if close {
            tx.execute("UPDATE process_sessions SET status = 'ended', duration_seconds = last_seen_at - first_seen_at
                WHERE status = 'running' AND last_seen_at < ?", [now - 300.0])?;
        }
        tx.commit()?;
        if prune {
            self.next_prune = now + 600.0;
        }
        if close {
            self.next_close = now + 1800.0;
        }
        Ok(lagging)
    }
}

pub fn revisit(tx: &Transaction<'_>, at: f64, now: f64) -> rusqlite::Result<()> {
    let middle = boundary(at, 120);
    let hour = boundary(at, 3600);
    if middle >= boundary(now - 120.0, 120) || middle < boundary(now - 7.0 * DAY, 120) + 120.0 {
        return Ok(());
    }
    tx.prepare_cached("UPDATE constella_archive_cursor
        SET next_bucket = CASE target_seconds WHEN 120 THEN ?1 ELSE ?2 END
        WHERE (target_seconds = 120 AND next_bucket > ?1) OR (target_seconds = 3600 AND next_bucket > ?2)")?
        .execute(params![middle, hour])?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn schema(conn: &mut Connection) {
        let tx = conn.transaction().unwrap();
        tx.execute_batch(include_str!("sql/schema.sql")).unwrap();
        crate::gpm_rollup::initialize(&tx).unwrap();
        initialize(&tx).unwrap();
        tx.commit().unwrap();
    }

    fn basic(conn: &Connection, at: f64, seconds: i64, avg: f64, count: u64) {
        conn.execute(
            "INSERT INTO gpu_metric_rollups VALUES (?1, ?2, 'node', 'gpu',
              ?3, ?3, ?3, CAST(?3 AS INTEGER), ?3, ?3, ?3, CAST(?3 AS INTEGER), ?4)",
            params![at, seconds, avg, count],
        )
        .unwrap();
    }

    fn gpm(conn: &Connection, at: f64, avg: Option<f64>, count: u64) {
        conn.execute(
            "INSERT INTO nvidia_gpm_rollups
                (bucket_start,bucket_seconds,node_id,gpu_uuid,expected_count,avg_sm_active,max_sm_active,sm_active_count)
             VALUES (?1,20,'node','gpu',4,?2,?2,?3)",
            params![at, avg, count],
        ).unwrap();
    }

    #[test]
    fn promotes_weighted_history_and_replays_late_arrivals() {
        let mut conn = Connection::open_in_memory().unwrap();
        schema(&mut conn);
        let now = 100.0 * DAY;
        let start = now - 14_400.0;
        basic(&conn, start, 20, 10.0, 3);
        basic(&conn, start + 20.0, 20, 30.0, 1);
        gpm(&conn, start, Some(30.0), 3);
        gpm(&conn, start + 20.0, None, 0);
        let mut archive = Archive::new();
        for _ in 0..5 {
            if !archive.maintain(&mut conn, now).unwrap() {
                break;
            }
        }
        for span in [120, 3600] {
            let summarize = conn
                .query_row(
                    "SELECT avg_gpu_utilization,sample_count FROM gpu_metric_rollups
                 WHERE bucket_start = ? AND bucket_seconds = ?",
                    params![start, span],
                    |row| Ok((row.get::<_, f64>(0)?, row.get::<_, u64>(1)?)),
                )
                .unwrap();
            assert_eq!(summarize, (15.0, 4));
            let perf = conn
                .query_row(
                    "SELECT avg_sm_active,sm_active_count,expected_count FROM nvidia_gpm_rollups
                 WHERE bucket_start = ? AND bucket_seconds = ?",
                    params![start, span],
                    |row| {
                        Ok((
                            row.get::<_, f64>(0)?,
                            row.get::<_, u64>(1)?,
                            row.get::<_, u64>(2)?,
                        ))
                    },
                )
                .unwrap();
            assert_eq!(perf, (30.0, 3, 8));
        }

        basic(&conn, start + 40.0, 20, 35.0, 1);
        let tx = conn.transaction().unwrap();
        revisit(&tx, start + 40.0, now).unwrap();
        tx.commit().unwrap();
        for _ in 0..5 {
            if !archive.maintain(&mut conn, now).unwrap() {
                break;
            }
        }
        let row: (f64, i64) = conn.query_row(
            "SELECT avg_gpu_utilization,sample_count FROM gpu_metric_rollups WHERE bucket_seconds = 3600",
            [], |row| Ok((row.get(0)?,row.get(1)?)),
        ).unwrap();
        assert_eq!(row, (19.0, 5));
    }

    #[test]
    fn preserves_existing_coarse_history_at_partial_retention_boundary() {
        let mut conn = Connection::open_in_memory().unwrap();
        schema(&mut conn);
        let now = 100.0 * DAY + 60.0;
        let old = boundary(now - 7.0 * DAY, 120);
        basic(&conn, old + 60.0, 20, 10.0, 1);
        basic(&conn, old, 120, 55.0, 6);
        basic(&conn, now - 366.0, 20, 30.0, 1);
        Archive::new().maintain(&mut conn, now).unwrap();
        let avg: f64 = conn.query_row(
            "SELECT avg_gpu_utilization FROM gpu_metric_rollups WHERE bucket_start = ? AND bucket_seconds = 120",
            [old], |row| row.get(0),
        ).unwrap();
        assert_eq!(avg, 55.0);
    }
}
