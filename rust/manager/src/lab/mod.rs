mod access;
mod accounts;
mod activity;
mod admin;
mod api;
mod limits;
mod nodes;
pub(crate) mod ownership;
mod schema;
mod store;

pub use schema::migrate;
pub use store::user_by_id;

use crate::{App, Error};
use axum::Json;
use axum::Router;
use axum::extract::{Request, State};
use axum::http::{HeaderValue, Method, StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use serde_json::json;
use std::env;
use std::path::Path;
use std::sync::Arc;

pub struct Lab {
    access: access::Access,
    pub store: store::Store,
    limits: limits::Limits,
    accounts: accounts::Policy,
    activity_readers: Arc<tokio::sync::Semaphore>,
    bootstrap: String,
    origin: String,
}

#[derive(Clone)]
pub struct Session {
    pub user: store::User,
    pub request: String,
    pub expires: u64,
}

impl Lab {
    pub fn from_env() -> Result<Self, Error> {
        let mode = env::var("CONSTELLA_AUTH_MODE").unwrap_or_default();
        if mode.trim() != "cloudflare-access" {
            return Err("Lab requires CONSTELLA_AUTH_MODE=cloudflare-access".into());
        }
        fn setting(name: &str) -> Result<String, Error> {
            let value = env::var(name).unwrap_or_default().trim().to_owned();
            if value.is_empty() {
                Err(format!("{name} is required").into())
            } else {
                Ok(value)
            }
        }
        let origin = access::https_origin(&setting("CONSTELLA_PUBLIC_ORIGIN")?)?;
        let issuer = access::https_origin(&setting("CONSTELLA_ACCESS_TEAM_DOMAIN")?)?;
        let bootstrap = store::normalize_email(&setting("CONSTELLA_BOOTSTRAP_ADMIN_EMAIL")?)
            .ok_or("invalid CONSTELLA_BOOTSTRAP_ADMIN_EMAIL")?;
        let access = access::Access::new(issuer, &setting("CONSTELLA_ACCESS_AUD")?)?;
        let store = store::Store::open(Path::new(&setting("CONSTELLA_LAB_DB_PATH")?))?;
        Ok(Self {
            access,
            store,
            bootstrap,
            origin,
            limits: limits::Limits::default(),
            accounts: accounts::Policy::from_env()?,
            activity_readers: Arc::new(tokio::sync::Semaphore::new(2)),
        })
    }
}

pub fn protect(router: Router, app: Arc<App>) -> Router {
    router
        .merge(api::routes(app.clone()))
        .layer(middleware::from_fn_with_state(app, authenticate))
}

fn reject(status: StatusCode, code: &str, request: &str) -> Response {
    let mut response = (status, Json(json!({"error":code,"request_id":request}))).into_response();
    headers(&mut response, request);
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}

fn headers(response: &mut Response, request: &str) {
    let headers = response.headers_mut();
    headers.insert(
        "x-request-id",
        HeaderValue::from_str(request).expect("valid request ID"),
    );
    headers.insert(
        header::X_CONTENT_TYPE_OPTIONS,
        HeaderValue::from_static("nosniff"),
    );
    headers.insert(header::X_FRAME_OPTIONS, HeaderValue::from_static("DENY"));
    headers.insert(
        header::REFERRER_POLICY,
        HeaderValue::from_static("same-origin"),
    );
    headers.insert(
        header::CONTENT_SECURITY_POLICY,
        HeaderValue::from_static("frame-ancestors 'none'; object-src 'none'; base-uri 'self'"),
    );
}

fn get_header<'a>(headers: &'a axum::http::HeaderMap, name: &str) -> Option<&'a str> {
    headers.get(name).and_then(|value| value.to_str().ok())
}

async fn authenticate(State(app): State<Arc<App>>, mut request: Request, next: Next) -> Response {
    let lab = app.lab.as_ref().expect("Lab is enabled");
    let path = request.uri().path();
    if matches!(
        path,
        "/api/health" | "/api/agents/ws" | "/api/highres/stream"
    ) {
        return next.run(request).await;
    }
    let id = uuid::Uuid::new_v4().simple().to_string();
    let Some(token) = get_header(request.headers(), "cf-access-jwt-assertion")
        .filter(|token| !token.is_empty())
        .map(str::to_owned)
    else {
        return reject(StatusCode::UNAUTHORIZED, "access_identity_required", &id);
    };
    let Ok(identity) = lab.access.verify(&token).await else {
        return reject(StatusCode::UNAUTHORIZED, "invalid_access_identity", &id);
    };
    let expires = identity.expires;
    let bootstrap = lab.bootstrap.clone();
    let audit_id = id.clone();
    let user = match lab
        .store
        .run(move |conn| store::resolve(conn, &identity, &bootstrap, &audit_id))
        .await
    {
        Ok(user) => user,
        Err(error) => {
            eprintln!("Lab identity lookup: {error}");
            return reject(
                StatusCode::SERVICE_UNAVAILABLE,
                "identity_store_unavailable",
                &id,
            );
        }
    };
    if user.status != "active" {
        return reject(StatusCode::FORBIDDEN, "user_not_active", &id);
    }
    let method = request.method();
    if (path.starts_with("/api/lab/admin")
        || path.starts_with("/api/docs")
        || path == "/openapi.json"
        || (path == "/api/settings" && method == Method::PATCH))
        && user.role != "admin"
    {
        return reject(StatusCode::FORBIDDEN, "admin_required", &id);
    }
    if matches!(
        *method,
        Method::POST | Method::PATCH | Method::DELETE | Method::PUT
    ) && (get_header(request.headers(), "origin").map(|origin| origin.trim_end_matches('/'))
        != Some(lab.origin.as_str())
        || get_header(request.headers(), "x-constella-request") != Some("same-origin"))
    {
        return reject(StatusCode::FORBIDDEN, "csrf_check_failed", &id);
    }
    request.extensions_mut().insert(Session {
        user,
        request: id.clone(),
        expires,
    });
    let mut response = next.run(request).await;
    headers(&mut response, &id);
    response
}
