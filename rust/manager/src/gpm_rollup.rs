use constella_protocol::{NVIDIA_GPM_METRICS, NVIDIA_GPM_PROFILE, Performance};
use rusqlite::types::Value;
use rusqlite::{CachedStatement, Transaction, params_from_iter};

const TABLE: &str = "nvidia_gpm_rollups";

pub fn enabled() -> bool {
    std::env::var("CONSTELLA_NVIDIA_GPM_ROLLUP")
        .ok()
        .is_none_or(|setting| {
            !matches!(
                setting.trim().to_ascii_lowercase().as_str(),
                "0" | "false" | "no" | "off"
            )
        })
}

fn stems() -> impl Iterator<Item = &'static str> {
    NVIDIA_GPM_METRICS
        .iter()
        .map(|(_, name)| name.strip_prefix("nvidia.gpm.").unwrap())
}

pub fn initialize(tx: &Transaction<'_>) -> rusqlite::Result<()> {
    tx.execute_batch(
        "
        CREATE TABLE IF NOT EXISTS nvidia_gpm_rollups (
          bucket_start REAL NOT NULL,
          bucket_seconds INTEGER NOT NULL,
          node_id TEXT NOT NULL,
          gpu_uuid TEXT NOT NULL,
          expected_count INTEGER NOT NULL,
          PRIMARY KEY(bucket_start, bucket_seconds, node_id, gpu_uuid)
        );
        CREATE INDEX IF NOT EXISTS idx_nvidia_gpm_rollups_node_bucket_time
        ON nvidia_gpm_rollups(node_id, bucket_seconds, bucket_start);
    ",
    )?;
    let mut info = tx.prepare("PRAGMA table_info(nvidia_gpm_rollups)")?;
    let columns = info
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<rusqlite::Result<std::collections::HashSet<_>>>()?;
    for stem in stems() {
        for (column, column_type) in [
            (format!("avg_{stem}"), "REAL"),
            (format!("max_{stem}"), "REAL"),
            (format!("{stem}_count"), "INTEGER NOT NULL DEFAULT 0"),
        ] {
            if !columns.contains(&column) {
                tx.execute_batch(&format!(
                    "ALTER TABLE {TABLE} ADD COLUMN {column} {column_type}"
                ))?;
            }
        }
    }
    Ok(())
}

pub fn statement() -> String {
    let mut columns = String::from("bucket_start,bucket_seconds,node_id,gpu_uuid,expected_count");
    let mut values = String::from("?1,20,?2,?3,1");
    let mut updates = String::from("expected_count=expected_count+1");
    for (index, stem) in stems().enumerate() {
        let value = format!("?{}", index + 4);
        columns.push_str(&format!(",avg_{stem},max_{stem},{stem}_count"));
        values.push_str(&format!(",{value},{value},{value} IS NOT NULL"));
        updates.push_str(&format!(
            ",avg_{stem}=CASE WHEN {value} IS NULL THEN avg_{stem}
                WHEN {stem}_count=0 THEN {value}
                ELSE avg_{stem}+({value}-avg_{stem})/({stem}_count+1) END,
             max_{stem}=CASE WHEN {value} IS NULL THEN max_{stem}
                WHEN {stem}_count=0 THEN {value} ELSE MAX(max_{stem},{value}) END,
             {stem}_count={stem}_count+({value} IS NOT NULL)"
        ));
    }
    format!(
        "INSERT INTO {TABLE} ({columns}) VALUES ({values})
        ON CONFLICT(bucket_start,bucket_seconds,node_id,gpu_uuid) DO UPDATE SET {updates}"
    )
}

pub fn write(
    statement: &mut CachedStatement<'_>,
    bucket: f64,
    node: &str,
    uuid: &str,
    sample: &Performance,
) -> rusqlite::Result<()> {
    if sample.profile != NVIDIA_GPM_PROFILE || sample.status == "unsupported" {
        return Ok(());
    }
    let readings = NVIDIA_GPM_METRICS.iter().map(|(_, name)| {
        let maximum = if name.ends_with("_per_second") {
            f32::MAX as f64
        } else {
            100.0
        };
        let value = sample
            .metrics
            .get(*name)
            .copied()
            .filter(|value| sample.status == "available" && (0.0..=maximum).contains(value));
        value.map_or(Value::Null, Value::Real)
    });
    statement.execute(params_from_iter(
        [
            Value::Real(bucket),
            Value::Text(node.into()),
            Value::Text(uuid.into()),
        ]
        .into_iter()
        .chain(readings),
    ))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    #[test]
    fn gaps_increase_expected_count_without_diluting_average() {
        let mut conn = Connection::open_in_memory().unwrap();
        let tx = conn.transaction().unwrap();
        initialize(&tx).unwrap();
        let mut stmt = tx.prepare_cached(&statement()).unwrap();
        let mut perf = Performance {
            profile: NVIDIA_GPM_PROFILE.into(),
            status: "warming".into(),
            sampled_at: 100.0,
            interval_ms: None,
            metrics: Default::default(),
            supported_metrics: Vec::new(),
            invalid_metrics: Vec::new(),
            error: None,
        };
        write(&mut stmt, 100.0, "node", "gpu", &perf).unwrap();
        perf.status = "available".into();
        for value in [0.0, 100.0] {
            perf.metrics.insert("nvidia.gpm.sm_active".into(), value);
            write(&mut stmt, 100.0, "node", "gpu", &perf).unwrap();
        }
        let row: (i64, f64, f64, i64) = tx.query_row(
            "SELECT expected_count,avg_sm_active,max_sm_active,sm_active_count FROM nvidia_gpm_rollups",
            [], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
        ).unwrap();
        assert_eq!(row, (3, 50.0, 100.0, 2));
    }
}
