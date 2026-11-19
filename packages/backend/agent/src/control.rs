use constella_protocol::{Account, AgentMessage, ControlResponse};
use std::ffi::{CStr, CString};
use std::mem::MaybeUninit;
use std::sync::Arc;
use tokio::sync::Semaphore;

pub async fn account(id: u64, username: String, slot: Arc<Semaphore>) -> AgentMessage {
    let response = match slot.try_acquire_owned() {
        Ok(permit) => {
            let lookup = tokio::task::spawn_blocking(move || {
                let _permit = permit;
                linux_account(&username)
            });
            match tokio::time::timeout(std::time::Duration::from_secs(3), lookup).await {
                Ok(Ok(Ok(account))) => ControlResponse::Account { account },
                _ => ControlResponse::Rejected,
            }
        }
        Err(_) => ControlResponse::Rejected,
    };
    AgentMessage::ControlResponse { id, response }
}

fn linux_account(username: &str) -> std::io::Result<Option<Account>> {
    if username.is_empty() || username.len() > 256 {
        return Err(std::io::ErrorKind::InvalidInput.into());
    }
    let username = CString::new(username)?;
    let mut buffer = vec![0; 4096];
    loop {
        let mut record = MaybeUninit::<libc::passwd>::uninit();
        let mut result = std::ptr::null_mut();
        // The record and buffer stay live while all returned strings are copied.
        let status = unsafe {
            libc::getpwnam_r(
                username.as_ptr(),
                record.as_mut_ptr(),
                buffer.as_mut_ptr().cast(),
                buffer.len(),
                &mut result,
            )
        };
        if status == libc::ERANGE && buffer.len() < 65536 {
            buffer.resize(buffer.len() * 2, 0);
            continue;
        }
        if status != 0 {
            return Err(std::io::Error::from_raw_os_error(status));
        }
        if result.is_null() {
            return Ok(None);
        }
        // getpwnam_r returns the initialized record and NUL-terminated fields on success.
        let account = unsafe {
            Account {
                canonical_username: CStr::from_ptr((*result).pw_name)
                    .to_string_lossy()
                    .into_owned(),
                uid: (*result).pw_uid,
                gid: (*result).pw_gid,
                shell: CStr::from_ptr((*result).pw_shell)
                    .to_string_lossy()
                    .into_owned(),
            }
        };
        return Ok(Some(account));
    }
}
