use axum::body::Bytes;
use axum::http::StatusCode;
use std::collections::HashMap;
use std::future::Future;
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tokio::sync::watch;

type Result = std::result::Result<Bytes, StatusCode>;

const TTL: Duration = Duration::from_secs(60);
const MAX_BYTES: usize = 32 * 1024 * 1024;
const MAX_ENTRIES: usize = 128;
const MAX_PENDING: usize = 16;

struct Entry {
    body: Bytes,
    expires: Instant,
    used: Instant,
}

#[derive(Default)]
struct State {
    entries: HashMap<String, Entry>,
    pending: HashMap<String, watch::Receiver<Option<Result>>>,
    bytes: usize,
}

impl State {
    fn remove(&mut self, key: &str) {
        if let Some(entry) = self.entries.remove(key) {
            self.bytes -= entry.body.len();
        }
    }

    fn insert(&mut self, key: String, body: Bytes, now: Instant) {
        self.entries.retain(|_, entry| {
            if entry.expires > now {
                return true;
            }
            self.bytes -= entry.body.len();
            false
        });
        if body.len() > MAX_BYTES {
            return;
        }
        while self.bytes + body.len() > MAX_BYTES || self.entries.len() >= MAX_ENTRIES {
            let Some(oldest) = self
                .entries
                .iter()
                .min_by_key(|(_, entry)| entry.used)
                .map(|(key, _)| key.clone())
            else {
                break;
            };
            self.remove(&oldest);
        }
        self.bytes += body.len();
        self.entries.insert(
            key,
            Entry {
                body,
                expires: now + TTL,
                used: now,
            },
        );
    }
}

#[derive(Default)]
pub struct ResultCache(Mutex<State>);

impl ResultCache {
    pub async fn read(
        self: &std::sync::Arc<Self>,
        key: String,
        load: impl Future<Output = Result> + Send + 'static,
    ) -> Result {
        let mut receiver = {
            let mut state = self.0.lock().unwrap();
            let now = Instant::now();
            if let Some(entry) = state.entries.get_mut(&key) {
                if entry.expires > now {
                    entry.used = now;
                    return Ok(entry.body.clone());
                }
                state.remove(&key);
            }
            if let Some(receiver) = state.pending.get(&key) {
                receiver.clone()
            } else {
                if state.pending.len() >= MAX_PENDING {
                    return Err(StatusCode::SERVICE_UNAVAILABLE);
                }
                let (sender, receiver) = watch::channel(None);
                state.pending.insert(key.clone(), receiver.clone());
                let cache = self.clone();
                tokio::spawn(async move {
                    let output = tokio::spawn(load)
                        .await
                        .unwrap_or(Err(StatusCode::INTERNAL_SERVER_ERROR));
                    let mut state = cache.0.lock().unwrap();
                    state.pending.remove(&key);
                    if let Ok(body) = &output {
                        state.insert(key, body.clone(), Instant::now());
                    }
                    sender.send_replace(Some(output));
                });
                receiver
            }
        };
        receiver
            .wait_for(Option::is_some)
            .await
            .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
            .clone()
            .expect("finished cache entry has a result")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use tokio::sync::Notify;

    #[tokio::test]
    async fn an_aborted_waiter_does_not_cancel_shared_cache_fill() {
        let cache = Arc::new(ResultCache::default());
        let started = Arc::new(Notify::new());
        let finish = Arc::new(Notify::new());
        let first = tokio::spawn({
            let cache = cache.clone();
            let started = started.clone();
            let finish = finish.clone();
            async move {
                cache
                    .read("node".to_owned(), async move {
                        started.notify_one();
                        finish.notified().await;
                        Ok(Bytes::from_static(b"shared"))
                    })
                    .await
            }
        });
        started.notified().await;
        first.abort();
        assert!(first.await.unwrap_err().is_cancelled());
        finish.notify_one();
        let report = cache
            .read("node".to_owned(), async {
                panic!("overlapping requests must reuse the existing loader")
            })
            .await
            .unwrap();
        assert_eq!(report.as_ref(), b"shared");
        let cached = cache
            .read("node".to_owned(), async {
                panic!("subsequent requests must reuse the cached result")
            })
            .await
            .unwrap();
        assert_eq!(report, cached);
    }
}
