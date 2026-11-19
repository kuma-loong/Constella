use rusqlite::{Connection, OpenFlags, TransactionBehavior};
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::time::Duration;

pub fn migrate(path: &Path) -> Result<(), crate::Error> {
    if !path.is_file() {
        return Err("SQLite migration requires an existing CONSTELLA_LAB_DB_PATH".into());
    }
    open(path)?;
    Ok(())
}

pub fn open(path: &Path) -> Result<Connection, crate::Error> {
    use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(parent)?;
    }
    let file = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .open(path)?;
    file.set_permissions(std::fs::Permissions::from_mode(0o600))?;
    let mut conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_WRITE)?;
    conn.busy_timeout(Duration::from_secs(5))?;
    conn.execute_batch("PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL;")?;
    initialize(&mut conn)?;
    Ok(conn)
}

fn initialize(conn: &mut Connection) -> rusqlite::Result<()> {
    let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
    tx.execute_batch(include_str!("lab_schema.sql"))?;
    let lacks_onboarding = !tx.query_row(
        "SELECT EXISTS(
            SELECT 1 FROM pragma_table_info('lab_users') WHERE name = 'onboarding_completed_at'
        )",
        [],
        |row| row.get::<_, bool>(0),
    )?;
    if lacks_onboarding {
        tx.execute_batch(
            "
            ALTER TABLE lab_users ADD COLUMN onboarding_completed_at REAL;
            UPDATE lab_users SET onboarding_completed_at = (
                SELECT MIN(valid_from) FROM lab_account_bindings WHERE user_id = lab_users.id
            ) WHERE EXISTS (
                SELECT 1 FROM lab_account_bindings WHERE user_id = lab_users.id
            );
        ",
        )?;
    }
    tx.execute(
        "INSERT OR IGNORE INTO lab_schema_migrations(version, applied_at) VALUES (2, ?)",
        [constella_protocol::unix_time()],
    )?;
    tx.commit()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backfills_onboarding_from_the_first_binding_only_on_legacy_schema() {
        let mut conn = Connection::open_in_memory().unwrap();
        initialize(&mut conn).unwrap();
        conn.execute_batch("
            ALTER TABLE lab_users DROP COLUMN onboarding_completed_at;
            DELETE FROM lab_schema_migrations WHERE version = 2;
            INSERT INTO lab_users VALUES
                ('user', 'issuer', 'subject', 'a@example.com', 'a@example.com', NULL, 'member', 'active', 10, 10, 10);
            INSERT INTO lab_account_bindings VALUES
                ('old', 'user', 'node', 1000, NULL, 'account', 'self_claimed', 'revoked', 12, 13, 'user', 'user', NULL),
                ('new', 'user', 'node', 1001, NULL, 'account', 'self_claimed', 'active', 14, NULL, 'user', NULL, NULL);
        ").unwrap();
        initialize(&mut conn).unwrap();
        initialize(&mut conn).unwrap();
        let onboarding: f64 = conn
            .query_row("SELECT onboarding_completed_at FROM lab_users", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(onboarding, 12.0);
    }
}
