use super::store;
use crate::{App, jobs, usage};
use rusqlite::{Connection, Row};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

pub const USAGE_SQL: &str =
    "SELECT s.session_id, s.node_id, s.pid, s.ppid, s.process_start_time, s.parent_start_time,
    s.user, s.task_name, s.first_seen_at, s.last_seen_at, s.status,
    u.gpu_uuid, u.first_seen_at, u.last_seen_at, u.avg_memory_mb,
    g.name, g.gpu_index, g.device_type, g.card_id, s.user_uid
    FROM process_gpu_usages u
    JOIN process_sessions s ON s.session_id = u.session_id
    LEFT JOIN gpus g ON g.gpu_id = u.node_id || ':' || u.gpu_uuid
    WHERE u.last_seen_at >= ? AND u.first_seen_at <= ?";

pub struct UsageRow {
    pub session: String,
    pub node: String,
    pub pid: i64,
    pub process_start: Option<f64>,
    pub user: Option<String>,
    pub uid: Option<i64>,
    pub task: String,
    pub session_start: f64,
    pub session_end: f64,
    pub status: String,
    pub uuid: String,
    pub index: Option<i64>,
    pub first: f64,
    pub last: f64,
    pub memory: f64,
    pub model: String,
    pub card: String,
    pub key: String,
}

impl UsageRow {
    pub fn read(row: &Row<'_>) -> rusqlite::Result<Self> {
        let node: String = row.get(1)?;
        let uuid: String = row.get(11)?;
        let user = row
            .get::<_, Option<String>>(6)?
            .filter(|user| !user.is_empty());
        let pid = row.get(2)?;
        let process_start = row.get(4)?;
        let session_start = row.get(8)?;
        let key = jobs::key(
            &node,
            user.as_deref().unwrap_or("unknown"),
            pid,
            row.get(3)?,
            session_start,
            process_start,
            row.get(5)?,
        );
        let device_type: Option<String> = row.get(17)?;
        let card_id = row
            .get::<_, Option<String>>(18)?
            .filter(|_| device_type.as_deref() == Some("ascend"));
        let card = card_id.map_or_else(
            || format!("{node}:{uuid}"),
            |id| format!("{node}:ascend:{id}"),
        );
        Ok(Self {
            session: row.get(0)?,
            node,
            pid,
            process_start,
            user,
            uid: row.get(19)?,
            task: row.get(7)?,
            session_start,
            session_end: row.get(9)?,
            status: row.get(10)?,
            uuid,
            index: row.get(16)?,
            first: row.get(12)?,
            last: row.get(13)?,
            memory: row.get(14)?,
            model: row
                .get::<_, Option<String>>(15)?
                .unwrap_or_else(|| "unknown".into())
                .replace("NVIDIA ", ""),
            card,
            key,
        })
    }
}

#[derive(Clone, Deserialize, Serialize)]
pub struct Binding {
    node_id: String,
    unix_uid: i64,
    unix_username: String,
    user_id: String,
    user_display_name: Option<String>,
    valid_from: f64,
    valid_to: Option<f64>,
}

pub async fn bindings(app: &App) -> Result<Vec<Binding>, crate::Error> {
    app.lab
        .as_ref()
        .expect("Lab enabled")
        .store
        .run(|conn| read_bindings(conn))
        .await
}

pub fn digest(value: impl Serialize) -> String {
    let bytes = serde_json::to_vec(&value).expect("cache key is serializable");
    format!("{:x}", Sha256::digest(bytes))
}

pub struct History<'a> {
    uid: BTreeMap<(&'a str, i64), Vec<&'a Binding>>,
    name: BTreeMap<(&'a str, &'a str), Vec<&'a Binding>>,
    start: f64,
}

impl<'a> History<'a> {
    pub fn new(bindings: &'a [Binding], start: f64) -> Self {
        let mut uid = BTreeMap::<_, Vec<_>>::new();
        let mut name = BTreeMap::<_, Vec<_>>::new();
        for binding in bindings {
            uid.entry((binding.node_id.as_str(), binding.unix_uid))
                .or_default()
                .push(binding);
            name.entry((binding.node_id.as_str(), binding.unix_username.as_str()))
                .or_default()
                .push(binding);
        }
        for entries in uid.values_mut().chain(name.values_mut()) {
            entries.sort_by(|a, b| a.valid_from.total_cmp(&b.valid_from));
        }
        Self { uid, name, start }
    }

    pub fn nodes(&self, owner: &str) -> BTreeMap<&str, Vec<i64>> {
        let mut nodes = BTreeMap::<_, Vec<_>>::new();
        for ((node, uid), history) in &self.uid {
            if history.iter().any(|binding| binding.user_id == owner) {
                nodes.entry(*node).or_default().push(*uid);
            }
        }
        nodes
    }

    pub fn eligible(&self, owner: &str, end: f64) -> Vec<(f64, f64)> {
        usage::merge_intervals(
            self.uid
                .values()
                .flat_map(|entries| self.windows(entries))
                .filter(|(binding, _)| binding.user_id == owner)
                .map(|(binding, start)| {
                    (
                        start.max(self.start),
                        binding.valid_to.unwrap_or(end).min(end),
                    )
                })
                .collect(),
        )
    }

    fn windows<'b>(
        &self,
        entries: &'b [&'a Binding],
    ) -> impl DoubleEndedIterator<Item = (&'a Binding, f64)> + use<'a, 'b> {
        let start = self.start;
        entries.iter().enumerate().map(move |(index, &binding)| {
            (
                binding,
                if index == 0 {
                    start.min(binding.valid_from)
                } else {
                    binding.valid_from
                },
            )
        })
    }

    pub fn split(
        &self,
        row: &UsageRow,
        end: f64,
        mut emit: impl FnMut(Option<&Binding>, f64, f64),
    ) {
        let left = self.start.max(row.first);
        let right = end.min(row.last);
        if right < left {
            return;
        }
        let entries = match row.uid {
            Some(uid) => self.uid.get(&(row.node.as_str(), uid)),
            None => self
                .name
                .get(&(row.node.as_str(), row.user.as_deref().unwrap_or_default())),
        };
        let Some(entries) = entries else {
            emit(None, left, right);
            return;
        };
        let mut edges = vec![left, right];
        for (binding, start) in self.windows(entries) {
            edges.push(start.clamp(left, right));
            edges.extend(binding.valid_to.map(|end| end.clamp(left, right)));
        }
        edges.sort_unstable_by(f64::total_cmp);
        edges.dedup();
        let owner = |at| {
            self.windows(entries)
                .filter(|(binding, start)| {
                    *start <= at && binding.valid_to.is_none_or(|end| at < end)
                })
                .map(|(binding, _)| binding)
                .next_back()
        };
        if edges.len() == 1 {
            emit(owner(left), left, right);
        } else {
            for pair in edges.windows(2) {
                emit(owner(pair[0]), pair[0], pair[1]);
            }
        }
    }
}

impl Binding {
    pub fn owned_by(&self, user: &str) -> bool {
        self.user_id == user
    }

    fn label(&self, row: &UsageRow) -> String {
        self.user_display_name
            .as_ref()
            .filter(|name| !name.is_empty())
            .or(row.user.as_ref())
            .unwrap_or(&self.user_id)
            .to_owned()
    }
}

pub fn read_bindings(conn: &Connection) -> rusqlite::Result<Vec<Binding>> {
    store::bindings(conn, None, false)?
        .into_iter()
        .map(|row| {
            serde_json::from_value(row)
                .map_err(|error| rusqlite::Error::ToSqlConversionFailure(Box::new(error)))
        })
        .collect()
}

#[derive(Default)]
pub struct Rankings(BTreeMap<String, Owner>);

#[derive(Default)]
struct Owner {
    label: String,
    sessions: BTreeSet<String>,
    jobs: BTreeSet<String>,
    last: f64,
    intervals: Vec<(String, f64, f64)>,
    models: usage::CardUsage,
}

impl Rankings {
    pub fn add(&mut self, row: &UsageRow, binding: Option<&Binding>, start: f64, end: f64) {
        let (key, label) = match binding {
            Some(binding) => (format!("user:{}", binding.user_id), binding.label(row)),
            None => {
                let node = serde_json::to_string(&row.node).unwrap();
                let account = row.uid.map_or_else(
                    || serde_json::to_string(&row.user).unwrap(),
                    |uid| uid.to_string(),
                );
                (
                    format!("account:[{node}, {account}]"),
                    format!(
                        "{} @ {}",
                        row.user.as_deref().unwrap_or("Unknown"),
                        row.node
                    ),
                )
            }
        };
        let owner = self.0.entry(key).or_insert_with(|| Owner {
            label,
            ..Owner::default()
        });
        owner.sessions.insert(row.session.clone());
        owner.jobs.insert(row.key.clone());
        owner.last = owner.last.max(end);
        owner.intervals.push((row.card.clone(), start, end));
        owner.models.add(&row.card, &row.model, start, end);
    }

    pub fn finish(self, start: f64, end: f64) -> Vec<Value> {
        let mut ranked: Vec<_> = self.0.into_iter().map(|(key, owner)| {
            let spans = usage::segments(owner.intervals.iter().map(|(card, left, right)| (card.as_str(), *left, *right)), start, end);
            let (hours, active) = segment_hours(&spans);
            let models = models(owner.models);
            let weighted = models.iter().map(|model| model.gpu_hours * usage::gpu_weight(&model.model)).sum::<f64>();
            let top: Vec<_> = models.iter().take(3).map(|model| json!({"name":model.model,"gpu_hours":model.gpu_hours})).collect();
            let mut days = BTreeSet::new();
            for (_, left, right) in owner.intervals {
                if left < end { add_days(&mut days, left, right); }
            }
            let result = json!({"user":owner.label,"owner_key":key,"gpu_hours":hours,"active_hours":active,
                "active_days":days.len(),"weighted_gpu_hours":weighted,"task_count":owner.sessions.len(),
                "job_count":owner.jobs.len(),"last_seen_at":owner.last,"top_gpu_models":top});
            (key, weighted, result)
        }).collect();
        ranked.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.cmp(&b.0)));
        ranked
            .into_iter()
            .take(20)
            .map(|(_, _, result)| result)
            .collect()
    }
}

pub fn date(at: f64) -> chrono::NaiveDate {
    use chrono::TimeZone;
    chrono_tz::Asia::Shanghai
        .timestamp_opt(at.floor() as i64, 0)
        .single()
        .expect("valid telemetry time")
        .date_naive()
}

pub fn midnight(day: chrono::NaiveDate) -> f64 {
    use chrono::TimeZone;
    chrono_tz::Asia::Shanghai
        .from_local_datetime(&day.and_hms_opt(0, 0, 0).unwrap())
        .earliest()
        .expect("valid Shanghai midnight")
        .timestamp() as f64
}

pub fn add_days(days: &mut BTreeSet<chrono::NaiveDate>, start: f64, end: f64) {
    let mut day = date(start);
    days.insert(day);
    while let Some(next) = day.succ_opt().filter(|next| midnight(*next) < end) {
        days.insert(next);
        day = next;
    }
}

pub fn segment_hours(spans: &[usage::Segment]) -> (f64, f64) {
    let (gpu, active) = spans.iter().fold((0.0, 0.0), |(gpu, active), span| {
        let width = span.end - span.start;
        (gpu + width * f64::from(span.count), active + width)
    });
    (gpu / 3600.0, active / 3600.0)
}

#[derive(Serialize)]
pub struct Model {
    pub model: String,
    pub gpu_hours: f64,
    share: f64,
}

pub fn models(usage: usage::CardUsage) -> Vec<Model> {
    let totals = usage.totals();
    let total = totals.values().sum::<f64>();
    let mut models: Vec<_> = totals
        .into_iter()
        .map(|(model, seconds)| Model {
            model,
            gpu_hours: seconds / 3600.0,
            share: if total > 0.0 { seconds / total } else { 0.0 },
        })
        .collect();
    models.sort_by(|a, b| {
        b.gpu_hours
            .total_cmp(&a.gpu_hours)
            .then(a.model.cmp(&b.model))
    });
    models
}
