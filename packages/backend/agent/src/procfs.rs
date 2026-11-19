use constella_protocol::Process;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::ffi::CStr;
use std::fs;
use std::os::unix::fs::MetadataExt;
use std::path::Path;

pub struct Procfs {
    boot_time: Option<f64>,
    ticks_per_second: f64,
    include_cmdline: bool,
}

impl Procfs {
    pub fn new() -> Self {
        let boot_time = fs::read_to_string("/proc/stat").ok().and_then(|data| {
            data.lines()
                .find_map(|line| line.strip_prefix("btime ")?.parse().ok())
        });
        // SAFETY: sysconf has no pointer arguments.
        let ticks = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
        let mode = std::env::var("CONSTELLA_PROCESS_DETAIL_MODE").unwrap_or_default();
        Self {
            boot_time: boot_time.filter(|_| ticks > 0),
            ticks_per_second: ticks as f64,
            include_cmdline: !mode.trim().eq_ignore_ascii_case("names"),
        }
    }

    pub fn read(&self, pid: u32, now: f64, users: &mut HashMap<u32, Option<String>>) -> Process {
        let directory = format!("/proc/{pid}");
        let path = Path::new(&directory);
        let stat = read_stat(path);
        let ppid = stat.as_ref().map(|stat| stat.ppid);
        let process_start_time = self.start_time(stat.as_ref());
        let parent_start_time = ppid.filter(|ppid| *ppid != 0).and_then(|ppid| {
            self.start_time(read_stat(Path::new(&format!("/proc/{ppid}"))).as_ref())
        });
        let user_uid = fs::metadata(path).ok().map(|metadata| metadata.uid());
        let user =
            user_uid.and_then(|uid| users.entry(uid).or_insert_with(|| username(uid)).clone());
        let exe = fs::read_link(path.join("exe"))
            .ok()
            .map(|path| path.to_string_lossy().into_owned());
        let (cmdline, detail_status, detail_error) = if !self.include_cmdline {
            (None, "names", None)
        } else {
            match read_cmdline(path) {
                Ok(Some(value)) => (Some(value), "ok", None),
                Ok(None) => (None, "unavailable", None),
                Err(error) => (None, "unavailable", Some(error.to_string())),
            }
        };
        let name = stat.map(|stat| stat.name).unwrap_or_else(|| "?".into());
        let task_name = cmdline
            .as_deref()
            .and_then(task_from_cmdline)
            .or_else(|| exe.as_deref().map(basename))
            .unwrap_or(&name)
            .to_owned();
        let cmdline_hash = cmdline.as_ref().map(|value| {
            let digest = Sha256::digest(value.as_bytes());
            format!(
                "{:016x}",
                u64::from_be_bytes(digest[..8].try_into().unwrap())
            )
        });
        Process {
            pid,
            ppid,
            name,
            user_uid,
            user,
            task_name: Some(task_name),
            exe,
            cmdline,
            cmdline_hash,
            process_start_time,
            parent_start_time,
            runtime_seconds: process_start_time.map(|started| (now - started).max(0.0) as u64),
            detail_status: detail_status.into(),
            detail_error,
            ..Process::default()
        }
    }

    fn start_time(&self, stat: Option<&Stat>) -> Option<f64> {
        Some(self.boot_time? + stat?.start_ticks as f64 / self.ticks_per_second)
    }
}

fn read_cmdline(path: &Path) -> std::io::Result<Option<String>> {
    use std::io::Read;
    let mut data = Vec::new();
    fs::File::open(path.join("cmdline"))?
        .take(4096)
        .read_to_end(&mut data)?;
    let value = String::from_utf8_lossy(&data).replace('\0', " ");
    Ok((!value.trim().is_empty()).then(|| value.trim().to_owned()))
}

struct Stat {
    name: String,
    ppid: u32,
    start_ticks: u64,
}

fn read_stat(path: &Path) -> Option<Stat> {
    let value = fs::read(path.join("stat")).ok()?;
    parse_stat(&String::from_utf8_lossy(&value))
}

fn parse_stat(value: &str) -> Option<Stat> {
    let (_, remainder) = value.split_once('(')?;
    let (name, fields) = remainder.rsplit_once(')')?;
    let mut fields = fields.split_ascii_whitespace();
    let ppid = fields.nth(1)?.parse().ok()?;
    let start_ticks = fields.nth(17)?.parse().ok()?;
    Some(Stat {
        name: name.to_owned(),
        ppid,
        start_ticks,
    })
}

fn basename(value: &str) -> &str {
    value.rsplit('/').next().unwrap_or(value)
}

fn task_from_cmdline(value: &str) -> Option<&str> {
    let parts = || value.split_whitespace().map(basename);
    parts()
        .find(|part| {
            [".py", ".sh", ".pl", ".R", ".ipynb"]
                .iter()
                .any(|suffix| part.ends_with(suffix))
        })
        .or_else(|| {
            parts().find(|part| {
                matches!(
                    *part,
                    "torchrun" | "accelerate" | "python" | "python3" | "uvicorn"
                )
            })
        })
        .or_else(|| parts().next())
}

fn username(uid: u32) -> Option<String> {
    let mut buffer = vec![0; 4096];
    let mut pwd = std::mem::MaybeUninit::<libc::passwd>::uninit();
    loop {
        let mut result = std::ptr::null_mut();
        // SAFETY: all outputs have writable storage for the duration of the call.
        let code = unsafe {
            libc::getpwuid_r(
                uid,
                pwd.as_mut_ptr(),
                buffer.as_mut_ptr(),
                buffer.len(),
                &mut result,
            )
        };
        if code == libc::ERANGE && buffer.len() < 64 * 1024 {
            buffer.resize(buffer.len() * 2, 0);
            continue;
        }
        if code != 0 || result.is_null() {
            return None;
        }
        // SAFETY: successful getpwuid_r returns a passwd with a NUL-terminated name in buffer.
        return Some(
            unsafe { CStr::from_ptr((*result).pw_name) }
                .to_string_lossy()
                .into_owned(),
        );
    }
}

pub fn excluded(process: &Process) -> bool {
    matches!(process.user.as_deref(), Some("gdm" | "Debian-gdm"))
        || [Some(process.name.as_str()), process.exe.as_deref()]
            .into_iter()
            .flatten()
            .any(|name| {
                matches!(
                    basename(name),
                    "X" | "Xorg"
                        | "Xwayland"
                        | "gnome-shell"
                        | "kwin_x11"
                        | "kwin_wayland"
                        | "plasmashell"
                        | "mutter"
                        | "cinnamon"
                        | "muffin"
                        | "xfwm4"
                        | "picom"
                        | "compton"
                )
            })
}

pub fn aggregate(gpu: &mut constella_protocol::Gpu, owner: &str) {
    let mut grouped = std::collections::BTreeMap::<String, constella_protocol::OtherUser>::new();
    gpu.processes.retain(|process| {
        let username = process.user.as_deref().unwrap_or("?");
        if username == owner && !owner.is_empty() && process.user.is_some() {
            return true;
        }
        let stats =
            grouped
                .entry(username.to_owned())
                .or_insert_with(|| constella_protocol::OtherUser {
                    user: username.to_owned(),
                    ..Default::default()
                });
        stats.process_count += 1;
        stats.total_memory_mb = stats.total_memory_mb.saturating_add(process.gpu_memory_mb);
        stats.runtime_seconds = stats.runtime_seconds.max(process.runtime_seconds);
        false
    });
    gpu.other_users = grouped.into_values().collect();
    gpu.other_users
        .sort_by_key(|user| std::cmp::Reverse(user.total_memory_mb));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_stat_fields_when_comm_has_spaces_and_parens() {
        let stat = parse_stat("321 (a) (b)) R 12 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 9100")
            .unwrap();
        assert_eq!(stat.name, "a) (b)");
        assert_eq!(stat.ppid, 12);
        assert_eq!(stat.start_ticks, 9100);
    }
}
