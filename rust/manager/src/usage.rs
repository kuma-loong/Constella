use std::collections::BTreeMap;

#[derive(Default)]
pub struct CardUsage {
    intervals: BTreeMap<(String, String), Vec<(f64, f64)>>,
}

impl CardUsage {
    pub fn add(&mut self, card: &str, model: &str, start: f64, end: f64) {
        if end > start {
            self.intervals
                .entry((card.to_owned(), model.to_owned()))
                .or_default()
                .push((start, end));
        }
    }

    pub fn totals(self) -> BTreeMap<String, f64> {
        let mut result = BTreeMap::new();
        for ((_, model), intervals) in self.intervals {
            let seconds = merge_intervals(intervals)
                .into_iter()
                .map(|(a, b)| b - a)
                .sum::<f64>();
            *result.entry(model).or_insert(0.0) += seconds;
        }
        result
    }
}

pub fn gpu_weight(model: &str) -> f64 {
    let unified = model
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_ascii_uppercase();
    [
        ("H100", 1.0),
        ("PRO 6000", 0.9),
        ("PRO 5000", 0.8),
        ("RTX 4090", 0.75),
    ]
    .into_iter()
    .find_map(|(known, weight)| unified.contains(known).then_some(weight))
    .unwrap_or(1.0)
}

pub fn hours(models: &BTreeMap<String, f64>) -> (f64, f64) {
    let mut total = 0.0;
    let mut weighted = 0.0;
    for (model, seconds) in models {
        total += seconds;
        weighted += seconds * gpu_weight(model);
    }
    (total / 3600.0, weighted / 3600.0)
}

pub fn merge_intervals(mut intervals: Vec<(f64, f64)>) -> Vec<(f64, f64)> {
    intervals.sort_unstable_by(|a, b| a.0.total_cmp(&b.0));
    let mut merged: Vec<(f64, f64)> = Vec::new();
    for (start, end) in intervals {
        if start >= end {
            continue;
        }
        if let Some(last) = merged.last_mut().filter(|last| start <= last.1) {
            last.1 = last.1.max(end);
        } else {
            merged.push((start, end));
        }
    }
    merged
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn overlapping_processes_on_one_physical_card_are_counted_once() {
        let mut usage = CardUsage::default();
        usage.add("node:ascend:0", "Ascend", 0.0, 8.0);
        usage.add("node:ascend:0", "Ascend", 5.0, 10.0);
        usage.add("node:ascend:1", "Ascend", 5.0, 10.0);
        assert_eq!(usage.totals()["Ascend"], 15.0);
    }
}
