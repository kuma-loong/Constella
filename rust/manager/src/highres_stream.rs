use crate::{App, authorized};
use axum::extract::State;
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use constella_protocol::Sample;
use serde_json::json;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
use tokio::sync::broadcast;

const CAPACITY: usize = 256;

pub struct Stream {
    tx: broadcast::Sender<axum::extract::ws::Utf8Bytes>,
    published: AtomicU64,
    dropped: AtomicU64,
}

impl Default for Stream {
    fn default() -> Self {
        Self {
            tx: broadcast::channel(CAPACITY).0,
            published: AtomicU64::new(0),
            dropped: AtomicU64::new(0),
        }
    }
}

impl Stream {
    pub fn publish(&self, node_id: &str, sample: &Sample, refresh_interval: f64) {
        self.published.fetch_add(1, Relaxed);
        if self.tx.receiver_count() == 0 {
            return;
        }
        let gpus: Vec<_> = sample
            .gpus
            .iter()
            .map(|gpu| {
                let mut value = json!({
                    "uuid": gpu.uuid, "gpu_index": gpu.index, "name": gpu.name,
                    "utilization_gpu": gpu.utilization_gpu, "utilization_mem": gpu.utilization_mem,
                    "memory_used_mb": gpu.memory_used_mb, "memory_total_mb": gpu.memory_total_mb,
                    "power_watts": gpu.power_watts, "temperature_c": gpu.temperature_c,
                });
                if !gpu.telemetry_errors.is_empty() {
                    value["telemetry_errors"] = json!(gpu.telemetry_errors);
                }
                if let Some(error) = &gpu.error {
                    value["error"] = json!(error);
                }
                if let Some(performance) = &gpu.performance {
                    value["performance"] = json!(performance);
                }
                value
            })
            .collect();
        let message = json!({
            "type": "gpu_sample", "node_id": node_id, "sampled_at": sample.sampled_at,
            "refresh_interval": refresh_interval, "gpus": gpus,
        });
        let _ = self.tx.send(message.to_string().into());
    }
}

pub async fn upgrade(
    ws: WebSocketUpgrade,
    State(app): State<Arc<App>>,
    headers: HeaderMap,
) -> Response {
    if (app.highres_token.is_some() || app.lab.is_some())
        && !authorized(&headers, app.highres_token.as_deref())
    {
        return StatusCode::UNAUTHORIZED.into_response();
    }
    ws.max_message_size(1024)
        .max_frame_size(1024)
        .on_upgrade(move |socket| crate::until_shutdown(app.clone(), publish(socket, app)))
}

async fn publish(mut socket: WebSocket, app: Arc<App>) {
    let stream = &app.highres_stream;
    let mut receiver = stream.tx.subscribe();
    let hello = json!({
        "type": "hello", "subscriber_count": stream.tx.receiver_count(),
        "published_messages": stream.published.load(Relaxed),
        "dropped_messages": stream.dropped.load(Relaxed), "queue_size": CAPACITY,
    });
    if !crate::send(&mut socket, hello.to_string()).await {
        return;
    }
    loop {
        tokio::select! {
            result = receiver.recv() => match result {
                Ok(message) => {
                    if !crate::send_text(&mut socket, message).await {
                        return;
                    }
                }
                Err(broadcast::error::RecvError::Lagged(count)) => {
                    stream.dropped.fetch_add(count, Relaxed);
                }
                Err(broadcast::error::RecvError::Closed) => return,
            },
            message = socket.recv() => match message {
                None | Some(Err(_)) | Some(Ok(Message::Close(_))) => return,
                _ => {}
            }
        }
    }
}
