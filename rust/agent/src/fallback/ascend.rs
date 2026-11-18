use super::Reading;
use constella_protocol::Gpu;
use regex::Regex;
use std::collections::BTreeMap;
use std::sync::LazyLock;

static MEMORY: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"([0-9.]+)\s*/\s*([0-9.]+)").unwrap());

pub(super) fn parse(input: &str) -> (Vec<Gpu>, Option<String>, Vec<Reading>) {
    let driver = input.lines().find_map(|line| {
        let (_, value) = line.split_once("Version:")?;
        line.contains("npu-smi")
            .then(|| value.split_whitespace().next().map(str::to_owned))
            .flatten()
    });
    let mut gpus = BTreeMap::new();
    let mut uuid_by_chip = BTreeMap::new();
    let mut lines = input.lines().peekable();
    let mut readings = Vec::new();
    let mut processes = false;
    while let Some(line) = lines.next() {
        if line.contains("Process id") && line.contains("Process name") {
            processes = true;
            continue;
        }
        let Some(line) = line
            .trim()
            .strip_prefix('|')
            .and_then(|line| line.strip_suffix('|'))
        else {
            continue;
        };
        let columns: Vec<_> = line.split('|').map(str::trim).collect();
        if !processes {
            let Some((card, model)) = device(&columns) else {
                continue;
            };
            let detail = lines.peek().map_or("", |line| line.trim());
            let values: Vec<_> = detail.trim_matches('|').split('|').map(str::trim).collect();
            let mut topology = values[0]
                .split_whitespace()
                .filter_map(|part| part.parse::<u32>().ok());
            let die = topology.next().unwrap_or_default();
            let physical_id = topology.next().unwrap_or(card);
            let uuid = if gpus.keys().any(|&(id, _, _)| id == physical_id) {
                format!("ascend-{physical_id}-{card}-{die}")
            } else {
                format!("ascend-{physical_id}")
            };
            let health = columns[1];
            let mut readings = columns[2].split_whitespace();
            let power = readings.next().and_then(|part| part.parse::<f64>().ok());
            let temperature = readings.next().and_then(|part| part.parse::<u32>().ok());
            let metrics = values.last().copied().unwrap_or_default();
            let utilization = metrics
                .split_whitespace()
                .next()
                .and_then(|part| part.parse::<u32>().ok())
                .unwrap_or_default();
            let memory = MEMORY.captures_iter(metrics).last().map(|caps| {
                (
                    caps[1].parse::<f64>().unwrap_or_default() as u64,
                    caps[2].parse::<f64>().unwrap_or_default() as u64,
                )
            });
            let mut gpu = Gpu {
                card_id: Some(card.to_string()),
                die_id: Some(die),
                uuid: uuid.clone(),
                name: model.to_owned(),
                device_type: "ascend".into(),
                pci_bus_id: values
                    .get(1)
                    .filter(|bus| !bus.is_empty() && **bus != "NA")
                    .map(|bus| (*bus).to_owned()),
                utilization_gpu: utilization,
                power_watts: power.unwrap_or_default(),
                power_limit_watts: rated_watts(model),
                temperature_c: temperature.unwrap_or_default(),
                error: (!matches!(health.to_ascii_uppercase().as_str(), "OK" | "NORMAL"))
                    .then(|| health.to_owned()),
                ..Gpu::default()
            };
            if let Some((used, total)) = memory {
                gpu.memory_used_mb = used;
                gpu.memory_total_mb = total;
                gpu.memory_free_mb = total.saturating_sub(used);
            }
            uuid_by_chip.insert((card, die), uuid);
            gpus.insert((physical_id, card, die), gpu);
        } else if let [location, pid, name, memory, ..] = columns.as_slice() {
            let mut location = location
                .split_whitespace()
                .filter_map(|part| part.parse::<u32>().ok());
            if let (Some(card), Some(die), Ok(pid), Ok(memory)) = (
                location.next(),
                location.next(),
                pid.parse::<u32>(),
                memory
                    .split_whitespace()
                    .next()
                    .unwrap_or_default()
                    .parse::<u64>(),
            ) && pid != 0
                && let Some(uuid) = uuid_by_chip.get(&(card, die))
            {
                readings.push(Reading {
                    uuid: uuid.clone(),
                    pid,
                    name: Some((*name).to_owned()),
                    memory,
                });
            }
        }
    }
    let gpus = gpus
        .into_values()
        .enumerate()
        .map(|(index, mut gpu)| {
            gpu.index = index as u32;
            gpu
        })
        .collect();
    (gpus, driver, readings)
}

fn device<'a>(columns: &[&'a str]) -> Option<(u32, &'a str)> {
    if columns.len() != 3 {
        return None;
    }
    let mut head = columns[0].split_whitespace();
    let card = head.next()?.parse().ok()?;
    let model = head.next()?;
    if model.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    Some((card, model))
}

fn rated_watts(model: &str) -> f64 {
    [
        ("310P1", 90.0),
        ("310P3", 72.0),
        ("910B1", 430.0),
        ("910B2", 420.0),
        ("910B3", 350.0),
        ("910A", 310.0),
        ("910B", 265.0),
        ("910C", 350.0),
    ]
    .into_iter()
    .find_map(|(kind, watts)| model.contains(kind).then_some(watts))
    .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_multiple_dies_on_a_card_and_attributes_processes() {
        let (gpus, version, processes) = parse(
            "
| npu-smi 24.1.0.1 Version: 24.1.0.1 |
| 1 310P3 | OK | NA 62 |
| 0 0 | 0000:01:00.0 | 0 16302/ 44280 |
| 1 310P3 | OK | NA 62 |
| 1 1 | 0000:01:00.0 | 0 15543/ 43693 |
| NPU Chip | Process id | Process name | Process memory(MB) |
| 1 0 | 3277562 | mindie_llm_back | 14513 |
| 1 1 | 3277565 | mindie_llm_back | 14513 |
",
        );
        assert_eq!(version.as_deref(), Some("24.1.0.1"));
        assert_eq!(gpus.len(), 2);
        assert_eq!(gpus[0].memory_used_mb, 16_302);
        assert_eq!(gpus[1].memory_used_mb, 15_543);
        assert_eq!(gpus[1].die_id, Some(1));
        assert_eq!(processes[1].uuid, gpus[1].uuid);
        assert_eq!(processes[1].memory, 14_513);
    }
}
