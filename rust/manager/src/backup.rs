use rusqlite::{Connection, OpenFlags, backup::Backup};
use std::fs::{self, OpenOptions};
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;
use std::time::Duration;

pub fn run(source: &Path, destination: &Path) -> Result<(), crate::Error> {
    let source = Connection::open_with_flags(source, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    source.busy_timeout(Duration::from_secs(5))?;
    // A previously retained backup must survive repeated invocations.
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(destination)?;
    let result = (|| {
        let mut copy = Connection::open_with_flags(destination, OpenFlags::SQLITE_OPEN_READ_WRITE)?;
        let backup = Backup::new(&source, &mut copy)?;
        backup.run_to_completion(1024, Duration::from_millis(2), None)
    })();
    if result.is_err() {
        let _ = fs::remove_file(destination);
    }
    Ok(result?)
}
