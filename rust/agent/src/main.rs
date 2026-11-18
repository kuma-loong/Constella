mod ascend;
mod control;
mod fallback;
mod gpm;
mod nvidia;
mod procfs;

use constella_protocol::{
    AgentMessage, ControlRequest, ControlResponse, MAX_SAMPLE_BYTES, ManagerMessage, Sample,
    Settings, VERSION, token_from_env, unix_time,
};
use futures_util::{SinkExt, StreamExt};
use std::env;
use std::os::unix::process::CommandExt;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::net::TcpStream;
use tokio::sync::{Semaphore, mpsc};
use tokio_tungstenite::tungstenite::{
    Message,
    client::IntoClientRequest,
    http::{HeaderMap, HeaderValue},
};
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream};

type Error = Box<dyn std::error::Error + Send + Sync>;
type Socket = WebSocketStream<MaybeTlsStream<TcpStream>>;

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Error> {
    let mode = env::args().nth(1);
    if mode.as_deref() == Some("--version") {
        println!("constella-agent {}", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }
    if mode
        .as_deref()
        .is_some_and(|arg| !matches!(arg, "--sample-once" | "--help"))
    {
        return Err("expected --sample-once, --help, or no arguments".into());
    }
    if mode.as_deref() == Some("--help") {
        println!(
            "Usage: constella-agent [--sample-once]\nSet CONSTELLA_MANAGER_URL and CONSTELLA_AGENT_TOKEN_FILE for streaming."
        );
        return Ok(());
    }
    let device = env::var("CONSTELLA_DEVICE_TYPE").unwrap_or_else(|_| "nvidia".into());
    let backend = match device.as_str() {
        "nvidia" => Backend::Nvidia,
        "ascend" => Backend::Ascend,
        _ => return Err(format!("unsupported accelerator backend: {device}").into()),
    };
    let settings = Settings::from_env()?.effective();
    let process_mode = env::var("CONSTELLA_PROCESS_DETAIL_MODE").unwrap_or_default();
    let aggregate = process_mode
        .trim()
        .eq_ignore_ascii_case("aggregate")
        .then(|| env::var("USER").unwrap_or_default());
    let mut sampler = Sampler {
        aggregate,
        backend,
        active: None,
        fallback: None,
    };
    if mode.as_deref() == Some("--sample-once") {
        let sample = sampler.sample(settings).await;
        println!("{}", serde_json::to_string(&sample)?);
        return if let Some(error) = sample.error {
            Err(error.into())
        } else {
            Ok(())
        };
    }

    let token = token_from_env("CONSTELLA_AGENT_TOKEN")?.ok_or("agent token is required")?;
    let url = manager_url(&env::var("CONSTELLA_MANAGER_URL")?)?;
    let hostname = std::fs::read_to_string("/proc/sys/kernel/hostname")?
        .trim()
        .to_owned();
    let node_id = env::var("CONSTELLA_NODE_ID").unwrap_or_else(|_| hostname.clone());
    if node_id.is_empty() || node_id.len() > 128 {
        return Err("node ID must contain 1 to 128 bytes".into());
    }
    let hello = AgentMessage::Hello {
        version: VERSION,
        node_id,
        hostname,
        agent_version: env!("CARGO_PKG_VERSION").into(),
        instance_id: Some(uuid::Uuid::new_v4().simple().to_string()),
    };
    let mut headers = access_headers(&url)?;
    headers.insert("authorization", secret_header(&format!("Bearer {token}"))?);
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    let shutdown = async {
        tokio::select! {
            result = tokio::signal::ctrl_c() => result,
            _ = term.recv() => Ok(()),
        }
    };
    tokio::pin!(shutdown);
    let lookup_slot = Arc::new(Semaphore::new(1));
    let mut retry_seconds = 1;
    let mut seq = 0;
    loop {
        let started = Instant::now();
        let connect = async {
            let mut request = url.as_str().into_client_request()?;
            request.headers_mut().extend(headers.clone());
            let (mut socket, _) = tokio::time::timeout(
                Duration::from_secs(10),
                tokio_tungstenite::connect_async(request),
            )
            .await??;
            send(&mut socket, &hello).await?;
            stream(
                &mut socket,
                &mut sampler,
                &mut seq,
                settings,
                lookup_slot.clone(),
            )
            .await
        };
        tokio::select! {
            result = connect => match result {
                Ok(true) => {
                    drop(sampler);
                    let binary = env::current_exe()?;
                    return Err(std::process::Command::new(binary).args(env::args_os().skip(1)).exec().into());
                }
                Ok(false) => {},
                Err(error) => eprintln!("agent connection: {error}"),
            },
            result = &mut shutdown => return Ok(result?),
        }
        if started.elapsed() >= Duration::from_secs(30) {
            retry_seconds = 1;
        }
        tokio::select! {
            _ = tokio::time::sleep(Duration::from_secs(retry_seconds)) => {},
            result = &mut shutdown => return Ok(result?),
        }
        retry_seconds = (retry_seconds * 2).min(30);
    }
}

fn access_headers(url: &str) -> Result<HeaderMap, Error> {
    let id = token_from_env("CONSTELLA_ACCESS_CLIENT_ID")?;
    let secret = token_from_env("CONSTELLA_ACCESS_CLIENT_SECRET")?;
    let mut headers = HeaderMap::new();
    match (id, secret) {
        (None, None) => {}
        (Some(id), Some(secret)) if url.starts_with("wss://") => {
            headers.insert("cf-access-client-id", secret_header(&id)?);
            headers.insert("cf-access-client-secret", secret_header(&secret)?);
        }
        (Some(_), Some(_)) => {
            return Err("Cloudflare Access credentials require HTTPS or WSS".into());
        }
        _ => return Err("Cloudflare Access requires both a client ID and a client secret".into()),
    }
    Ok(headers)
}

fn secret_header(value: &str) -> Result<HeaderValue, Error> {
    let mut header = HeaderValue::from_str(value)?;
    header.set_sensitive(true);
    Ok(header)
}

fn manager_url(url: &str) -> Result<String, Error> {
    let (scheme, address) = url
        .split_once("://")
        .ok_or("manager URL requires a scheme")?;
    let scheme = match scheme {
        "http" | "ws" => "ws",
        "https" | "wss" => "wss",
        _ => return Err("manager URL must use HTTP(S) or WS(S)".into()),
    };
    let address = address.trim_end_matches('/');
    let suffix = if address.ends_with("/api/agents/ws") {
        ""
    } else {
        "/api/agents/ws"
    };
    Ok(format!("{scheme}://{address}{suffix}"))
}

async fn stream(
    socket: &mut Socket,
    sampler: &mut Sampler,
    seq: &mut u64,
    mut settings: Settings,
    lookup_slot: Arc<Semaphore>,
) -> Result<bool, Error> {
    let (control_tx, mut control_rx) = mpsc::channel(2);
    let mut tick = interval(settings);
    loop {
        tokio::select! {
            _ = tick.tick() => {
                *seq += 1;
                let sample = Box::new(sampler.sample(settings).await);
                send(socket, &AgentMessage::Sample { seq: *seq, sample }).await?;
            }
            message = socket.next() => match message.transpose()? {
                Some(Message::Text(text)) => match serde_json::from_str(&text)? {
                    ManagerMessage::Config { settings: requested } => {
                        if let Some(next) = Settings::new(requested.refresh_interval, requested.process_interval) {
                            if next.refresh_interval != settings.refresh_interval {
                                tick = interval(next);
                            }
                            settings = next.effective();
                        }
                    }
                    ManagerMessage::Control { id, request: ControlRequest::Restart } => {
                        send(socket, &AgentMessage::ControlResponse { id, response: ControlResponse::RestartAccepted }).await?;
                        return Ok(true);
                    }
                    ManagerMessage::Control { id, request: ControlRequest::AccountLookup { username } } => {
                        let control_tx = control_tx.clone();
                        let slot = lookup_slot.clone();
                        tokio::spawn(async move {
                            let reply = control::account(id, username, slot).await;
                            let _ = control_tx.try_send(reply);
                        });
                    }
                },
                Some(Message::Ping(_)) => socket.flush().await?,
                Some(Message::Close(_)) | None => return Ok(false),
                _ => {}
            },
            reply = control_rx.recv() => {
                if let Some(reply) = reply {
                    send(socket, &reply).await?;
                }
            }
        }
    }
}

fn interval(settings: Settings) -> tokio::time::Interval {
    let mut tick = tokio::time::interval(Duration::from_secs_f64(settings.refresh_interval));
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    tick
}

async fn send(socket: &mut Socket, message: &AgentMessage) -> Result<(), Error> {
    let text = serde_json::to_string(message)?;
    if text.len() > MAX_SAMPLE_BYTES {
        return Err("sample exceeds the protocol message limit".into());
    }
    tokio::time::timeout(
        Duration::from_secs(5),
        socket.send(Message::Text(text.into())),
    )
    .await??;
    Ok(())
}

enum Backend {
    Nvidia,
    Ascend,
}

enum ActiveSampler {
    Nvidia(Box<nvidia::Nvidia>),
    Ascend(Box<ascend::Ascend>),
}

struct Sampler {
    aggregate: Option<String>,
    backend: Backend,
    active: Option<ActiveSampler>,
    fallback: Option<(Instant, String, fallback::Fallback)>,
}

impl Sampler {
    async fn sample(&mut self, settings: Settings) -> Sample {
        let mut sample = self.raw_sample(settings).await;
        if let Some(user) = self.aggregate.as_ref() {
            for gpu in &mut sample.gpus {
                procfs::aggregate(gpu, user);
            }
        }
        sample
    }

    async fn raw_sample(&mut self, settings: Settings) -> Sample {
        let start = Instant::now();
        if self
            .fallback
            .as_ref()
            .is_none_or(|(retry, _, _)| start >= *retry)
        {
            match self.read(settings) {
                Ok(sample) => {
                    self.fallback = None;
                    return sample;
                }
                Err(error) => {
                    self.active = None;
                    eprintln!("direct driver sampling failed: {error}");
                    let retry = Instant::now() + Duration::from_secs(120);
                    if let Some((next, message, _)) = &mut self.fallback {
                        *next = retry;
                        *message = error.to_string();
                    } else {
                        self.fallback = Some((retry, error.to_string(), fallback::Fallback::new()));
                    }
                }
            }
        }
        let (_, direct_error, fallback) =
            self.fallback.as_mut().expect("driver fallback installed");
        let ascend = matches!(self.backend, Backend::Ascend);
        fallback.sample(ascend, settings.process_interval).await.unwrap_or_else(|error| Sample {
            sampled_at: unix_time(),
            source: "none".into(),
            error: Some(format!("driver sampling failed: {direct_error}; fall back to system tool failed: {error}")),
            elapsed_ms: start.elapsed().as_secs_f64() * 1000.0,
            ..Sample::default()
        })
    }

    fn read(&mut self, settings: Settings) -> Result<Sample, Error> {
        if self.active.is_none() {
            self.active = Some(match self.backend {
                Backend::Nvidia => ActiveSampler::Nvidia(Box::new(nvidia::Nvidia::new()?)),
                Backend::Ascend => ActiveSampler::Ascend(Box::new(ascend::Ascend::new()?)),
            });
        }
        match self.active.as_mut().unwrap() {
            ActiveSampler::Nvidia(sampler) => Ok(sampler.sample(settings.process_interval)?),
            ActiveSampler::Ascend(sampler) => sampler.sample(settings.process_interval),
        }
    }
}
