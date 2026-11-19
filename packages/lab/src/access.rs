use crate::Error;
use jsonwebtoken::{Algorithm, DecodingKey, Validation, decode, decode_header};
use reqwest::{Client, Url, redirect::Policy};
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::RwLock;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

const MAX_TOKEN_BYTES: usize = 16_384;
const MAX_JWKS_BYTES: usize = 128 * 1024;

pub struct Access {
    issuer: String,
    jwks: Url,
    client: Client,
    validation: Validation,
    keys: RwLock<Keys>,
    refresh: Mutex<()>,
}

#[derive(Default)]
struct Keys {
    refreshed: Option<Instant>,
    attempted: Option<Instant>,
    values: HashMap<String, DecodingKey>,
}

#[derive(Deserialize)]
struct Jwks {
    keys: Vec<Jwk>,
}

#[derive(Deserialize)]
struct Jwk {
    kid: String,
    kty: String,
    alg: Option<String>,
    #[serde(rename = "use")]
    purpose: Option<String>,
    n: Option<String>,
    e: Option<String>,
}

#[derive(Clone, Deserialize)]
pub struct Identity {
    #[serde(rename = "iss")]
    pub issuer: String,
    #[serde(rename = "sub")]
    pub subject: String,
    pub email: String,
    #[serde(rename = "exp")]
    pub expires: u64,
    iat: u64,
    #[serde(rename = "type")]
    kind: String,
}

pub fn https_origin(input: &str) -> Result<String, Error> {
    let url = Url::parse(input.trim())?;
    if url.scheme() != "https"
        || url.host().is_none()
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return Err("expected an HTTPS origin without credentials, query or path".into());
    }
    Ok(url.origin().ascii_serialization())
}

impl Access {
    pub fn new(issuer: String, audience: &str) -> Result<Self, Error> {
        let jwks = Url::parse(&format!("{issuer}/cdn-cgi/access/certs"))?;
        let client = Client::builder()
            .redirect(Policy::none())
            .no_proxy()
            .connect_timeout(Duration::from_secs(3))
            .timeout(Duration::from_secs(5))
            .build()?;
        let mut validation = Validation::new(Algorithm::RS256);
        validation.set_audience(&[audience]);
        validation.set_issuer(&[issuer.as_str()]);
        validation.set_required_spec_claims(&["iss", "sub", "exp", "aud"]);
        validation.validate_nbf = true;
        validation.leeway = 60;
        Ok(Self {
            issuer,
            jwks,
            client,
            validation,
            keys: RwLock::default(),
            refresh: Mutex::default(),
        })
    }

    pub async fn verify(&self, token: &str) -> Result<Identity, Error> {
        if token.len() > MAX_TOKEN_BYTES {
            return Err("Access token exceeds size limit".into());
        }
        let header = decode_header(token)?;
        let kid = header
            .kid
            .filter(|id| !id.is_empty() && id.len() <= 128)
            .ok_or("missing Access key identifier")?;
        if header.alg != Algorithm::RS256 {
            return Err("unexpected Access signing algorithm".into());
        }
        let key = self.key(&kid).await?;
        let claims = decode::<Identity>(token, &key, &self.validation)?.claims;
        if claims.kind != "app"
            || claims.issuer != self.issuer
            || claims.subject.trim().is_empty()
            || claims.subject.len() > 255
            || super::store::normalize_email(&claims.email).is_none()
            || claims.iat > constella_protocol::unix_time() as u64 + self.validation.leeway
        {
            return Err("invalid Access application claims".into());
        }
        Ok(claims)
    }

    async fn key(&self, kid: &str) -> Result<DecodingKey, Error> {
        if let Some(key) = self.cached_key(kid) {
            return Ok(key);
        }
        let _refresh = self.refresh.lock().await;
        if let Some(key) = self.cached_key(kid) {
            return Ok(key);
        }
        {
            let mut keys = self.keys.write().unwrap();
            if keys
                .attempted
                .is_some_and(|at| at.elapsed() < Duration::from_secs(10))
            {
                return Err("Access signing key unavailable".into());
            }
            keys.attempted = Some(Instant::now());
        }
        let values = match self.fetch_keys().await {
            Ok(keys) => keys,
            Err(error) => {
                eprintln!("Lab Access key refresh failed: {error}");
                return Err(error);
            }
        };
        let key = values.get(kid).cloned();
        let mut keys = self.keys.write().unwrap();
        keys.values = values;
        keys.refreshed = Some(Instant::now());
        key.ok_or_else(|| "Access signing key unavailable".into())
    }

    fn cached_key(&self, kid: &str) -> Option<DecodingKey> {
        let cached = self.keys.read().unwrap();
        if cached
            .refreshed
            .is_some_and(|at| at.elapsed() < Duration::from_secs(3600))
        {
            cached.values.get(kid).cloned()
        } else {
            None
        }
    }

    async fn fetch_keys(&self) -> Result<HashMap<String, DecodingKey>, Error> {
        let mut response = self
            .client
            .get(self.jwks.clone())
            .send()
            .await?
            .error_for_status()?;
        let mut body = Vec::new();
        while let Some(chunk) = response.chunk().await? {
            if body.len() + chunk.len() > MAX_JWKS_BYTES {
                return Err("Access key response exceeds size limit".into());
            }
            body.extend_from_slice(&chunk);
        }
        let mut keys = HashMap::new();
        for key in serde_json::from_slice::<Jwks>(&body)?.keys {
            if key.kty == "RSA"
                && key.alg.as_deref().is_none_or(|value| value == "RS256")
                && key.purpose.as_deref().is_none_or(|value| value == "sig")
                && let Some((modulus, exponent)) = key.n.zip(key.e)
            {
                keys.insert(
                    key.kid,
                    DecodingKey::from_rsa_components(&modulus, &exponent)?,
                );
            }
        }
        if keys.is_empty() {
            return Err("Access returned no RSA signing keys".into());
        }
        Ok(keys)
    }
}
