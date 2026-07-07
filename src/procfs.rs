use std::ffi::CStr;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::ptr;
use std::time::{SystemTime, UNIX_EPOCH};

pub fn process_parent_pid(pid: i64) -> Option<i64> {
    process_parent_pid_at(Path::new("/proc"), pid)
}

pub fn process_parent_pid_at(proc_root: &Path, pid: i64) -> Option<i64> {
    let stat = fs::read_to_string(proc_root.join(pid.to_string()).join("stat")).ok()?;
    parent_pid_from_stat(&stat)
}

pub fn parent_pid_from_stat(stat: &str) -> Option<i64> {
    stat_fields_after_comm(stat)?.get(1)?.parse().ok()
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

pub fn process_user(pid: i64) -> Option<String> {
    process_uid(pid).and_then(uid_to_username)
}

pub fn process_uid(pid: i64) -> Option<u32> {
    process_uid_at(Path::new("/proc"), pid)
}

pub fn process_uid_at(proc_root: &Path, pid: i64) -> Option<u32> {
    let status = fs::read_to_string(proc_root.join(pid.to_string()).join("status")).ok()?;
    uid_from_status(&status)
}

pub fn uid_from_status(status: &str) -> Option<u32> {
    status.lines().find_map(|line| {
        let rest = line.strip_prefix("Uid:")?;
        rest.split_whitespace().next()?.parse().ok()
    })
}

pub fn uid_to_username(uid: u32) -> Option<String> {
    #[cfg(unix)]
    {
        let mut pwd = std::mem::MaybeUninit::<libc::passwd>::uninit();
        let mut result = ptr::null_mut();
        let mut buffer = vec![0u8; passwd_buffer_size()];
        let rc = unsafe {
            libc::getpwuid_r(
                uid,
                pwd.as_mut_ptr(),
                buffer.as_mut_ptr().cast::<libc::c_char>(),
                buffer.len(),
                &mut result,
            )
        };
        if rc != 0 || result.is_null() {
            return Some(uid.to_string());
        }
        let pwd = unsafe { pwd.assume_init() };
        let name = unsafe { CStr::from_ptr(pwd.pw_name) }
            .to_string_lossy()
            .trim()
            .to_string();
        if name.is_empty() {
            Some(uid.to_string())
        } else {
            Some(name)
        }
    }
    #[cfg(not(unix))]
    {
        Some(uid.to_string())
    }
}

pub fn process_start_time_seconds(pid: i64) -> Option<f64> {
    process_start_time_seconds_at(Path::new("/proc"), pid)
}

pub fn process_start_time_seconds_at(proc_root: &Path, pid: i64) -> Option<f64> {
    let boot_time = boot_time_seconds_at(proc_root)?;
    let stat = fs::read_to_string(proc_root.join(pid.to_string()).join("stat")).ok()?;
    let fields = stat_fields_after_comm(&stat)?;
    let start_ticks: f64 = fields.get(19)?.parse::<u64>().ok()? as f64;
    Some(boot_time + start_ticks / clock_ticks_per_second())
}

pub fn process_runtime_seconds(pid: i64) -> Option<i64> {
    let started_at = process_start_time_seconds(pid)?;
    Some((unix_now() - started_at).max(0.0) as i64)
}

fn stat_fields_after_comm(stat: &str) -> Option<Vec<&str>> {
    let comm_end = stat.rfind(')')?;
    Some(stat.get(comm_end + 2..)?.split_whitespace().collect())
}

fn boot_time_seconds_at(proc_root: &Path) -> Option<f64> {
    let stat = fs::read_to_string(proc_root.join("stat")).ok()?;
    stat.lines().find_map(|line| {
        let rest = line.strip_prefix("btime ")?;
        rest.split_whitespace().next()?.parse::<f64>().ok()
    })
}

fn clock_ticks_per_second() -> f64 {
    #[cfg(unix)]
    {
        let ticks = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
        if ticks > 0 {
            return ticks as f64;
        }
    }
    100.0
}

fn passwd_buffer_size() -> usize {
    #[cfg(unix)]
    {
        let size = unsafe { libc::sysconf(libc::_SC_GETPW_R_SIZE_MAX) };
        if size > 0 {
            return size as usize;
        }
    }
    16 * 1024
}

fn unix_now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .unwrap_or(0.0)
}
