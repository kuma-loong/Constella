use crate::procfs::{Procfs, excluded};
use constella_protocol::{Process, Sample, unix_time};
use std::collections::HashMap;
use std::process::Stdio;
use std::time::{Duration, Instant};
use tokio::io::AsyncReadExt;
use tokio::process::Command;

mod ascend;
mod nvidia;

type Processes = HashMap<String, Vec<Process>>;

pub struct Fallback {
    procfs: Procfs,
    processes: Processes,
    processes_at: Option<Instant>,
    process_error: bool,
}

impl Fallback {
    pub fn new() -> Self {
        Self {
            procfs: Procfs::new(),
            processes: HashMap::new(),
            processes_at: None,
            process_error: false,
        }
    }

    pub async fn sample(
        &mut self,
        ascend: bool,
        process_interval: f64,
    ) -> Result<Sample, crate::Error> {
        let start = Instant::now();
        let at = unix_time();
        let (mut gpus, driver_version, command) = if ascend {
            let output = output("npu-smi", &["info"]).await?;
            let (gpus, driver, processes) = ascend::parse(&output);
            if self.process_due(process_interval) {
                self.processes_at = Some(start);
                self.processes = self.enrich(processes, at);
                self.process_error = false;
            }
            (gpus, driver, "npu-smi")
        } else {
            let command = "nvidia-smi";
            let raw = output(command, &[nvidia::GPU_QUERY, nvidia::CSV_FORMAT]).await?;
            let (gpus, driver) = nvidia::parse(&raw)?;
            if self.process_due(process_interval) {
                self.processes_at = Some(start);
                match output(command, &[nvidia::PROCESS_QUERY, nvidia::CSV_FORMAT])
                    .await
                    .and_then(|text| nvidia::processes(&text))
                {
                    Ok(processes) => {
                        self.processes = self.enrich(processes, at);
                        self.process_error = false;
                    }
                    Err(_) => {
                        self.processes.clear();
                        self.process_error = true;
                    }
                }
            }
            (gpus, driver, command)
        };
        if gpus.is_empty() {
            return Err(format!("{command} returned no supported devices").into());
        }
        for gpu in &mut gpus {
            if let Some(processes) = self.processes.get(&gpu.uuid) {
                gpu.processes.clone_from(processes);
            }
            if self.process_error {
                gpu.telemetry_errors.insert(
                    "processes".into(),
                    format!("{command} could not read compute processes"),
                );
            }
        }
        Ok(Sample {
            source: command.into(),
            sampled_at: at,
            elapsed_ms: start.elapsed().as_secs_f64() * 1000.0,
            gpus,
            driver_version,
            ..Sample::default()
        })
    }

    fn process_due(&self, interval: f64) -> bool {
        self.processes_at
            .is_none_or(|at| at.elapsed().as_secs_f64() >= interval)
    }

    fn enrich(&self, readings: Vec<Reading>, at: f64) -> Processes {
        let mut users = HashMap::new();
        let mut details = HashMap::new();
        let mut all = Processes::new();
        for reading in readings {
            let mut process = details
                .entry(reading.pid)
                .or_insert_with(|| self.procfs.read(reading.pid, at, &mut users))
                .clone();
            if process.name == "?"
                && let Some(name) = reading.name
            {
                if process.task_name.as_deref() == Some("?") {
                    process.task_name = Some(name.clone());
                }
                process.name = name;
            }
            process.gpu_memory_mb = reading.memory;
            process.kind = "compute".into();
            if !excluded(&process) {
                all.entry(reading.uuid).or_default().push(process);
            }
        }
        all
    }
}

struct Reading {
    uuid: String,
    pid: u32,
    name: Option<String>,
    memory: u64,
}

async fn output(command: &str, args: &[&str]) -> Result<String, crate::Error> {
    const LIMIT: u64 = 2 * 1024 * 1024;
    let mut child = Command::new(command)
        .args(args)
        .kill_on_drop(true)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()?;
    let mut stdout = child
        .stdout
        .take()
        .expect("child stdout is piped")
        .take(LIMIT + 1);
    let mut data = Vec::new();
    tokio::time::timeout(Duration::from_secs(3), async {
        stdout.read_to_end(&mut data).await?;
        if data.len() as u64 > LIMIT {
            return Err(format!("{command} returned oversized output").into());
        }
        if !child.wait().await?.success() {
            return Err(format!("{command} returned a failed exit status").into());
        }
        Ok::<_, crate::Error>(String::from_utf8_lossy(&data).into_owned())
    })
    .await?
}
