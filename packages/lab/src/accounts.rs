use super::api::{Failure, Reply, database_error, failure, lab};
use super::{Session, store};
use crate::{App, agent_control, history};
use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::{Extension, Json};
use constella_protocol::{Account, ControlRequest, ControlResponse, unix_time};
use rusqlite::{Connection, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::HashSet;
use std::sync::Arc;
use std::time::Duration;

pub struct Policy {
    pattern: regex::Regex,
    deny: HashSet<String>,
    min: u32,
    max: u32,
    timeout: Duration,
}

impl Policy {
    pub fn from_env() -> Result<Self, crate::Error> {
        let get = |name, default: &str| std::env::var(name).unwrap_or_else(|_| default.to_owned());
        let pattern = get(
            "CONSTELLA_ACCOUNT_USERNAME_PATTERN",
            "[a-z_][a-z0-9_-]{0,31}",
        );
        let min = get("CONSTELLA_ACCOUNT_UID_MIN", "1000").parse()?;
        let max = get("CONSTELLA_ACCOUNT_UID_MAX", "2147483647").parse()?;
        let timeout: f64 = get("CONSTELLA_ACCOUNT_LOOKUP_TIMEOUT", "5").parse()?;
        if min > max || !timeout.is_finite() || !(0.1..=60.0).contains(&timeout) {
            return Err("invalid Lab account policy bounds".into());
        }
        Ok(Self {
            pattern: regex::Regex::new(&format!(r"\A(?:{pattern})\z"))?,
            deny: get("CONSTELLA_ACCOUNT_DENY_USERS", "root,constella")
                .split(',')
                .map(str::trim)
                .filter(|name| !name.is_empty())
                .map(str::to_owned)
                .collect(),
            min,
            max,
            timeout: Duration::from_secs_f64(timeout),
        })
    }
}

#[derive(Deserialize)]
pub struct Target {
    node_id: String,
    username: String,
}

impl Target {
    fn valid(&self) -> bool {
        !self.node_id.is_empty()
            && self.node_id.len() <= 255
            && !self.username.is_empty()
            && self.username.len() <= 256
    }
}

#[derive(Deserialize)]
pub struct Batch {
    accounts: Vec<Target>,
}

#[derive(Serialize)]
struct Lookup {
    node_id: String,
    bindable: bool,
    #[serde(flatten, skip_serializing_if = "Option::is_none")]
    account: Option<PublicAccount>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<&'static str>,
}

#[derive(Serialize)]
struct PublicAccount {
    canonical_username: String,
    uid: u32,
    gid: u32,
}

impl From<Account> for PublicAccount {
    fn from(account: Account) -> Self {
        Self {
            canonical_username: account.canonical_username,
            uid: account.uid,
            gid: account.gid,
        }
    }
}

impl Lookup {
    fn error(node_id: String, error: &'static str) -> Self {
        Self {
            node_id,
            bindable: false,
            account: None,
            error: Some(error),
        }
    }
}

fn require_member(session: &Session) -> Result<(), Failure> {
    if session.user.role == "viewer" {
        Err(failure(session, StatusCode::FORBIDDEN, "member_required"))
    } else {
        Ok(())
    }
}

fn validate(batch: &Batch, session: &Session) -> Result<(), Failure> {
    require_member(session)?;
    let accounts = &batch.accounts;
    let distinct: HashSet<_> = accounts
        .iter()
        .map(|target| target.node_id.as_str())
        .collect();
    if distinct.len() != accounts.len() {
        return Err((
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({"detail":"duplicate node_id"})),
        ));
    }
    if !(1..=32).contains(&accounts.len()) || accounts.iter().any(|target| !target.valid()) {
        return Err(failure(
            session,
            StatusCode::UNPROCESSABLE_ENTITY,
            "invalid_account_batch",
        ));
    }
    Ok(())
}

async fn find_account(
    app: &App,
    session: &Session,
    owner: &str,
    target: Target,
    ignore_conflict: bool,
) -> Result<Lookup, Failure> {
    let lab = lab(app);
    if !lab.accounts.pattern.is_match(&target.username) {
        return Ok(Lookup::error(target.node_id, "invalid_username"));
    }
    let outcome = app
        .agent_control
        .request(
            &target.node_id,
            ControlRequest::AccountLookup {
                username: target.username,
            },
            lab.accounts.timeout,
            None,
        )
        .await;
    let (reason, public) = match outcome {
        Ok(ControlResponse::Account {
            account: Some(account),
        }) => {
            let policy = &lab.accounts;
            if policy.deny.contains(&account.canonical_username)
                || !policy.pattern.is_match(&account.canonical_username)
            {
                ("denied_username", "username_not_allowed")
            } else if !(policy.min..=policy.max).contains(&account.uid) {
                ("denied_uid", "uid_not_allowed")
            } else if account.shell.ends_with("/nologin") || account.shell.ends_with("/false") {
                ("denied_shell", "login_disabled")
            } else {
                if !ignore_conflict {
                    let node = target.node_id.clone();
                    let owner = owner.to_owned();
                    let uid = account.uid;
                    let conflict = lab
                        .store
                        .run(move |conn| has_conflict(conn, &owner, &node, uid))
                        .await
                        .map_err(|error| database_error(session, error))?;
                    if conflict {
                        return Ok(Lookup::error(target.node_id, "account_already_bound"));
                    }
                }
                return Ok(Lookup {
                    node_id: target.node_id,
                    bindable: true,
                    account: Some(account.into()),
                    error: None,
                });
            }
        }
        Ok(ControlResponse::Account { account: None }) => {
            ("account_not_found", "account_not_found")
        }
        Ok(_) => ("lookup_failed", "account_lookup_failed"),
        Err(agent_control::Error::Unavailable) => {
            let code = if app.agent_control.instance(&target.node_id).is_some() {
                "account_lookup_unavailable"
            } else if super::nodes::snapshot(app)
                .iter()
                .any(|node| node["node_id"] == target.node_id && node["status"] != "offline")
            {
                "account_lookup_unsupported"
            } else {
                "node_offline"
            };
            return Ok(Lookup::error(target.node_id, code));
        }
        Err(agent_control::Error::Timeout) => {
            return Ok(Lookup::error(target.node_id, "account_lookup_unavailable"));
        }
    };
    let node = target.node_id.clone();
    let id = session.user.id.clone();
    let request = session.request.clone();
    lab.store
        .run(move |conn| {
            store::audit(
                conn,
                &id,
                &request,
                "account.lookup_rejected",
                "node",
                &node,
                json!({"reason":reason}),
            )
        })
        .await
        .map_err(|error| database_error(session, error))?;
    Ok(Lookup::error(target.node_id, public))
}

async fn lookups(
    app: &App,
    session: &Session,
    accounts: Vec<Target>,
) -> Result<Vec<Lookup>, Failure> {
    futures_util::future::try_join_all(
        accounts
            .into_iter()
            .map(|target| find_account(app, session, &session.user.id, target, false)),
    )
    .await
}

pub async fn preview(
    State(app): State<Arc<App>>,
    Extension(session): Extension<Session>,
    Json(batch): Json<Batch>,
) -> Reply {
    validate(&batch, &session)?;
    if !lab(&app).limits.consume(
        &session.user.id,
        batch.accounts.len(),
        &[("lookup-minute", 10, 60), ("lookup-day", 50, 86400)],
    ) {
        return Err(failure(
            &session,
            StatusCode::TOO_MANY_REQUESTS,
            "account_lookup_rate_limited",
        ));
    }
    let results = lookups(&app, &session, batch.accounts).await?;
    Ok(Json(
        json!({"results":results,"request_id":session.request}),
    ))
}

pub async fn create(
    State(app): State<Arc<App>>,
    Extension(session): Extension<Session>,
    Json(batch): Json<Batch>,
) -> Result<(StatusCode, Json<Value>), Failure> {
    validate(&batch, &session)?;
    if !lab(&app)
        .limits
        .consume(&session.user.id, 1, &[("binding-day", 5, 86400)])
    {
        return Err(failure(
            &session,
            StatusCode::TOO_MANY_REQUESTS,
            "account_binding_rate_limited",
        ));
    }
    let results = lookups(&app, &session, batch.accounts).await?;
    if results.iter().any(|result| !result.bindable) {
        return Err((
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({
                "detail":{"code":"account_batch_not_bindable","results":results,"request_id":session.request},
            })),
        ));
    }
    let actor = session.user.id.clone();
    let request = session.request.clone();
    let bindings = lab(&app)
        .store
        .run(move |conn| create_batch(conn, &actor, &request, results))
        .await
        .map_err(|error| change_error(&session, error))?
        .ok_or_else(|| failure(&session, StatusCode::FORBIDDEN, "member_required"))?;
    Ok((
        StatusCode::CREATED,
        Json(json!({"bindings":bindings,"request_id":session.request})),
    ))
}

fn change_error(session: &Session, error: crate::Error) -> Failure {
    if error
        .downcast_ref::<rusqlite::Error>()
        .and_then(rusqlite::Error::sqlite_error_code)
        == Some(rusqlite::ErrorCode::ConstraintViolation)
    {
        failure(session, StatusCode::CONFLICT, "account_binding_conflict")
    } else {
        database_error(session, error)
    }
}

fn create_batch(
    conn: &mut Connection,
    actor: &str,
    request: &str,
    results: Vec<Lookup>,
) -> rusqlite::Result<Option<Vec<Value>>> {
    let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
    if !active_role(&tx, actor, false)? {
        return Ok(None);
    }
    let change = BindingChange {
        owner: actor,
        actor,
        request,
        assurance: "self_claimed",
        reason: None,
        now: unix_time(),
    };
    let mut created = HashSet::new();
    for result in results {
        let account = result.account.expect("bindable accounts have valid fields");
        let id = uuid::Uuid::new_v4().simple().to_string();
        change.insert(&tx, &id, &result.node_id, &account)?;
        created.insert(id);
    }
    if tx.execute(
        "UPDATE lab_users SET onboarding_completed_at=?1,updated_at=?1
        WHERE id=?2 AND onboarding_completed_at IS NULL",
        params![change.now, actor],
    )? > 0
    {
        store::audit(
            &tx,
            actor,
            request,
            "user.onboarding_completed",
            "user",
            actor,
            json!({"binding_count":created.len()}),
        )?;
    }
    let mut bindings = store::bindings(&tx, Some(actor), true)?;
    bindings.retain(|binding| {
        binding["id"]
            .as_str()
            .is_some_and(|id| created.contains(id))
    });
    tx.commit()?;
    Ok(Some(bindings))
}

struct BindingChange<'a> {
    owner: &'a str,
    actor: &'a str,
    request: &'a str,
    assurance: &'a str,
    reason: Option<&'a str>,
    now: f64,
}

impl BindingChange<'_> {
    fn insert(
        &self,
        conn: &Connection,
        id: &str,
        node: &str,
        account: &PublicAccount,
    ) -> rusqlite::Result<()> {
        conn.execute("INSERT INTO lab_account_bindings
            (id,user_id,node_id,unix_uid,unix_gid,unix_username,assurance,status,valid_from,created_by,reason)
            VALUES (?,?,?,?,?,?,?,'active',?,?,?)",
            params![id,self.owner,node,account.uid,account.gid,account.canonical_username,self.assurance,self.now,self.actor,self.reason])?;
        store::audit(
            conn,
            self.actor,
            self.request,
            "binding.created",
            "binding",
            id,
            json!({"user_id":self.owner,"node_id":node,"unix_uid":account.uid,"assurance":self.assurance,"reason":self.reason}),
        )
    }
}

fn has_conflict(conn: &Connection, owner: &str, node: &str, uid: u32) -> rusqlite::Result<bool> {
    conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM lab_account_bindings WHERE node_id=? AND valid_to IS NULL
        AND (user_id=? OR unix_uid=?))",
        params![node, owner, uid],
        |row| row.get(0),
    )
}

fn active_role(conn: &Connection, actor: &str, admin: bool) -> rusqlite::Result<bool> {
    Ok(store::user_by_id(conn, actor)?.is_some_and(|user| {
        user.status == "active" && (user.role == "admin" || (!admin && user.role == "member"))
    }))
}

fn get_binding(conn: &Connection, id: &str) -> rusqlite::Result<Option<Value>> {
    Ok(history::fetch(
        conn,
        "SELECT * FROM lab_account_bindings",
        vec![("id=?", rusqlite::types::Value::Text(id.to_owned()))],
        "",
        [],
    )?
    .pop())
}

pub async fn end(
    State(app): State<Arc<App>>,
    Extension(session): Extension<Session>,
    Path(id): Path<String>,
) -> Reply {
    require_member(&session)?;
    let actor = session.user.id.clone();
    let request = session.request.clone();
    let ended = lab(&app)
        .store
        .run(move |conn| {
            let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
            if !active_role(&tx, &actor, false)? {
                return Ok(None);
            }
            let changed = tx.execute(
                "UPDATE lab_account_bindings SET valid_to=?,ended_by=?2,status='revoked'
            WHERE id=?3 AND user_id=?2 AND valid_to IS NULL",
                params![unix_time(), actor, id],
            )?;
            if changed > 0 {
                store::audit(
                    &tx,
                    &actor,
                    &request,
                    "binding.ended",
                    "binding",
                    &id,
                    json!({"status":"revoked","reason":null}),
                )?;
            }
            tx.commit()?;
            Ok(Some(changed > 0))
        })
        .await
        .map_err(|error| database_error(&session, error))?
        .ok_or_else(|| failure(&session, StatusCode::FORBIDDEN, "member_required"))?;
    if !ended {
        return Err(failure(
            &session,
            StatusCode::NOT_FOUND,
            "binding_not_found",
        ));
    }
    Ok(Json(json!({"ended":true,"request_id":session.request})))
}

pub async fn verify(
    State(app): State<Arc<App>>,
    Extension(session): Extension<Session>,
    Path(id): Path<String>,
) -> Reply {
    let actor = session.user.id.clone();
    let request = session.request.clone();
    let binding = lab(&app).store.run(move |conn| {
        let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if !active_role(&tx, &actor, true)? { return Ok(None); }
        let changed = tx.execute("UPDATE lab_account_bindings SET assurance='admin_verified' WHERE id=? AND valid_to IS NULL", [&id])?;
        if changed == 0 { return Ok(Some(None)); }
        store::audit(&tx, &actor, &request, "binding.verified", "binding", &id, json!({"assurance":"admin_verified"}))?;
        let result = get_binding(&tx, &id)?;
        tx.commit()?;
        Ok(Some(result))
    }).await.map_err(|error| database_error(&session, error))?
        .ok_or_else(|| failure(&session, StatusCode::FORBIDDEN, "admin_required"))?
        .ok_or_else(|| failure(&session, StatusCode::NOT_FOUND, "binding_not_found"))?;
    Ok(Json(
        json!({"binding":binding,"request_id":session.request}),
    ))
}

#[derive(Deserialize)]
pub struct Reassignment {
    user_id: String,
    reason: String,
    #[serde(flatten)]
    target: Target,
}

pub async fn reassign(
    State(app): State<Arc<App>>,
    Extension(session): Extension<Session>,
    Json(input): Json<Reassignment>,
) -> Reply {
    let reason = input.reason.trim().to_owned();
    if reason.is_empty() || reason.chars().count() > 500 || !input.target.valid() {
        return Err(failure(
            &session,
            StatusCode::UNPROCESSABLE_ENTITY,
            "account_not_bindable",
        ));
    }
    let lookup = find_account(&app, &session, &input.user_id, input.target, true).await?;
    let Some(account) = lookup.account else {
        return Err(failure(
            &session,
            StatusCode::UNPROCESSABLE_ENTITY,
            "account_not_bindable",
        ));
    };
    let actor = session.user.id.clone();
    let request = session.request.clone();
    let binding = lab(&app)
        .store
        .run(move |conn| {
            let change = BindingChange {
                owner: &input.user_id,
                actor: &actor,
                request: &request,
                assurance: "admin_verified",
                reason: Some(&reason),
                now: unix_time(),
            };
            replace(conn, &change, &lookup.node_id, &account)
        })
        .await
        .map_err(|error| change_error(&session, error))?
        .ok_or_else(|| failure(&session, StatusCode::FORBIDDEN, "admin_required"))?
        .ok_or_else(|| {
            failure(
                &session,
                StatusCode::UNPROCESSABLE_ENTITY,
                "account_not_bindable",
            )
        })?;
    Ok(Json(
        json!({"binding":binding,"request_id":session.request}),
    ))
}

fn replace(
    conn: &mut Connection,
    change: &BindingChange<'_>,
    node: &str,
    account: &PublicAccount,
) -> rusqlite::Result<Option<Option<Value>>> {
    let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
    if !active_role(&tx, change.actor, true)? {
        return Ok(None);
    }
    if store::user_by_id(&tx, change.owner)?.is_none_or(|user| user.status != "active") {
        return Ok(Some(None));
    }
    let replacement = uuid::Uuid::new_v4().simple().to_string();
    let ended = {
        let mut query = tx.prepare(
            "UPDATE lab_account_bindings SET valid_to=?,ended_by=?,reason=?,status='reassigned'
            WHERE node_id=? AND valid_to IS NULL AND (user_id=? OR unix_uid=?) RETURNING id",
        )?;
        query
            .query_map(
                params![
                    change.now,
                    change.actor,
                    change.reason,
                    node,
                    change.owner,
                    account.uid
                ],
                |row| row.get::<_, String>(0),
            )?
            .collect::<rusqlite::Result<Vec<_>>>()?
    };
    for id in ended {
        store::audit(
            &tx,
            change.actor,
            change.request,
            "binding.reassigned",
            "binding",
            &id,
            json!({"replacement_binding_id":replacement,"reason":change.reason}),
        )?;
    }
    change.insert(&tx, &replacement, node, account)?;
    let binding = get_binding(&tx, &replacement)?;
    tx.commit()?;
    Ok(Some(binding))
}
