use std::collections::{HashMap, VecDeque};
use std::sync::Mutex;
use std::time::{Duration, Instant};

type LimitsBySubject = HashMap<(String, &'static str), VecDeque<(Instant, usize)>>;

#[derive(Default)]
pub struct Limits(Mutex<LimitsBySubject>);

impl Limits {
    pub fn consume(
        &self,
        subject: &str,
        count: usize,
        rules: &[(&'static str, usize, u64)],
    ) -> bool {
        let now = Instant::now();
        let mut state = self.0.lock().unwrap();
        if state.len() >= 1024 {
            state.retain(|_, events| events.back().is_some_and(|(until, _)| *until > now));
        }
        for (name, limit, _) in rules {
            let events = state.entry((subject.to_owned(), *name)).or_default();
            while events.front().is_some_and(|(at, _)| *at <= now) {
                events.pop_front();
            }
            if events.iter().map(|(_, count)| count).sum::<usize>() + count > *limit {
                return false;
            }
        }
        for (name, _, seconds) in rules {
            state
                .entry((subject.to_owned(), *name))
                .or_default()
                .push_back((now + Duration::from_secs(*seconds), count));
        }
        true
    }
}
