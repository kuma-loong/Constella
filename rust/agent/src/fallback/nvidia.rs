use super::Reading;
use constella_protocol::Gpu;
use std::borrow::Cow;
use std::io::Cursor;

pub const GPU_QUERY: &str = concat!(
    "--query-gpu=index,uuid,name,pci.bus_id,driver_version,temperature.gpu,",
    "utilization.gpu,utilization.memory,memory.total,memory.used,memory.free,power.draw,power.limit,",
    "clocks.sm,clocks.mem,pstate,compute_mode,mig.mode.current"
);
pub const PROCESS_QUERY: &str = "--query-compute-apps=gpu_uuid,pid,process_name,used_memory";
pub const CSV_FORMAT: &str = "--format=csv,noheader,nounits";

fn rows(input: &str) -> csv::StringRecordsIntoIter<Cursor<Cow<'_, [u8]>>> {
    csv::ReaderBuilder::new()
        .has_headers(false)
        .flexible(true)
        .trim(csv::Trim::All)
        .from_reader(Cursor::new(without_padding(input.as_bytes())))
        .into_records()
}

// nvidia-smi pads fields after each comma. CSV quoting begins at the first character.
fn without_padding(input: &[u8]) -> Cow<'_, [u8]> {
    if !input.contains(&b'"') {
        return Cow::Borrowed(input);
    }
    let mut quoted = false;
    let mut boundary = true;
    let mut result = Vec::with_capacity(input.len());
    for &byte in input {
        if boundary && matches!(byte, b' ' | b'\t') {
            continue;
        }
        if byte == b'"' {
            quoted = !quoted;
        }
        boundary = !quoted && matches!(byte, b',' | b'\n');
        result.push(byte);
    }
    Cow::Owned(result)
}

fn text(data: &str) -> Option<String> {
    (!data.is_empty() && !matches!(data, "N/A" | "[N/A]" | "Not Supported" | "[Not Supported]"))
        .then(|| data.to_owned())
}

pub fn parse(input: &str) -> Result<(Vec<Gpu>, Option<String>), crate::Error> {
    let mut driver = None;
    let mut gpus = Vec::new();
    for row in rows(input) {
        let row = row?;
        let val = |index| row.get(index).unwrap_or_default();
        let Ok(index) = val(0).parse() else {
            continue;
        };
        let mut gpu = Gpu {
            index,
            uuid: text(val(1)).unwrap_or_else(|| "unknown".into()),
            name: text(val(2)).unwrap_or_else(|| "unknown".into()),
            pci_bus_id: text(val(3)),
            device_type: "nvidia".into(),
            pstate: text(val(15)),
            compute_mode: text(val(16)),
            mig_mode: text(val(17)),
            clock_sm_mhz: val(13).parse().ok(),
            clock_mem_mhz: val(14).parse().ok(),
            ..Gpu::default()
        };
        if driver.is_none() {
            driver = text(val(4));
        }

        let telemetry = &mut gpu.telemetry_errors;
        let mut number = |column, field: &'static str| {
            if let Some(number) = val(column)
                .parse::<f64>()
                .ok()
                .filter(|number| number.is_finite() && *number >= 0.0)
            {
                number
            } else {
                telemetry.insert(field.to_owned(), "nvidia-smi reading unavailable".into());
                0.0
            }
        };
        gpu.temperature_c = number(5, "temperature_c") as u32;
        gpu.utilization_gpu = number(6, "utilization_gpu") as u32;
        gpu.utilization_mem = number(7, "utilization_mem") as u32;
        gpu.memory_total_mb = number(8, "memory_total_mb") as u64;
        gpu.memory_used_mb = number(9, "memory_used_mb") as u64;
        gpu.memory_free_mb = number(10, "memory_free_mb") as u64;
        gpu.power_watts = number(11, "power_watts");
        gpu.power_limit_watts = number(12, "power_limit_watts");
        gpus.push(gpu);
    }
    Ok((gpus, driver))
}

pub(super) fn processes(input: &str) -> Result<Vec<Reading>, crate::Error> {
    let mut result = Vec::new();
    for row in rows(input) {
        let row = row?;
        if row.len() < 4 {
            continue;
        }
        if let Ok(pid) = row[1].parse::<u32>()
            && pid != 0
        {
            result.push(Reading {
                uuid: row[0].to_owned(),
                pid,
                name: text(&row[2]),
                memory: row[3].parse().unwrap_or_default(),
            });
        }
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quoted_device_names_and_missing_readings_keep_their_columns() {
        let (gpus, version) = parse(
            r#"0, GPU-a, "Test board, 40GB", 0000:01:00.0, 535.1, 31, 67, 22, 40000, 300, 39700, N/A, 250, 1250, 900, P0, Default, N/A"#,
        ).unwrap();
        assert_eq!(version.as_deref(), Some("535.1"));
        let gpu = &gpus[0];
        assert_eq!(gpu.name, "Test board, 40GB");
        assert_eq!(gpu.utilization_gpu, 67);
        assert_eq!(gpu.memory_used_mb, 300);
        assert_eq!(gpu.power_limit_watts, 250.0);
        assert!(gpu.telemetry_errors.contains_key("power_watts"));
    }
}
