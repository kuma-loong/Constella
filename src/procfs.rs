use std::fs;
use std::io;
use std::path::{Path, PathBuf};

pub fn process_parent_pid(pid: i64) -> Option<i64> {
    process_parent_pid_at(Path::new("/proc"), pid)
}

pub fn process_parent_pid_at(proc_root: &Path, pid: i64) -> Option<i64> {
    let stat = fs::read_to_string(proc_root.join(pid.to_string()).join("stat")).ok()?;
    parent_pid_from_stat(&stat)
}

pub fn parent_pid_from_stat(stat: &str) -> Option<i64> {
    let comm_end = stat.rfind(')')?;
    let fields_after_comm: Vec<&str> = stat.get(comm_end + 2..)?.split_whitespace().collect();
    fields_after_comm.get(1)?.parse().ok()
}

pub fn process_cmdline(pid: i64) -> (Option<String>, String) {
    process_cmdline_at(Path::new("/proc"), pid)
}

pub fn process_cmdline_at(proc_root: &Path, pid: i64) -> (Option<String>, String) {
    match fs::read(proc_root.join(pid.to_string()).join("cmdline")) {
        Ok(raw) if raw.is_empty() => (None, "empty".to_string()),
        Ok(raw) => {
            let replaced: Vec<u8> = raw
                .into_iter()
                .map(|byte| if byte == 0 { b' ' } else { byte })
                .collect();
            let text = String::from_utf8_lossy(&replaced).trim().to_string();
            if text.is_empty() {
                (None, "empty".to_string())
            } else {
                (Some(text), "ok".to_string())
            }
        }
        Err(error) if error.kind() == io::ErrorKind::PermissionDenied => {
            (None, "permission_denied".to_string())
        }
        Err(_) => (None, "unavailable".to_string()),
    }
}

pub fn process_exe(pid: i64) -> Option<String> {
    fs::read_link(PathBuf::from("/proc").join(pid.to_string()).join("exe"))
        .ok()
        .map(|path| path.to_string_lossy().to_string())
}
