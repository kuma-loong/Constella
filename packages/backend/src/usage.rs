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

#[derive(Clone, serde::Serialize, serde::Deserialize)]
pub struct Segment {
    pub start: f64,
    pub end: f64,
    pub count: u32,
}

pub fn segments<'a>(
    records: impl Iterator<Item = (&'a str, f64, f64)>,
    start: f64,
    end: f64,
) -> Vec<Segment> {
    let mut cards = BTreeMap::<&str, Vec<_>>::new();
    for (card, left, right) in records {
        let (left, right) = (left.max(start), right.min(end));
        if left < right {
            cards.entry(card).or_default().push((left, right));
        }
    }
    let mut events = Vec::new();
    for intervals in cards.into_values() {
        for (a, b) in merge_intervals(intervals) {
            events.extend([(a, 1), (b, -1)]);
        }
    }
    events.sort_unstable_by(|a, b| a.0.total_cmp(&b.0));
    let mut segments: Vec<Segment> = Vec::new();
    let mut active = 0_i32;
    let mut previous = start;
    for (at, delta) in events {
        if active > 0 && at > previous {
            let count = active as u32;
            if let Some(last) = segments
                .last_mut()
                .filter(|last| last.end == previous && last.count == count)
            {
                last.end = at;
            } else {
                segments.push(Segment {
                    start: previous,
                    end: at,
                    count,
                });
            }
        }
        previous = at;
        active += delta;
    }
    segments
}
