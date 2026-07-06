use serde::{Deserialize, Serialize};
use thiserror::Error;

pub const ALLOWED_REFRESH_INTERVALS: [f64; 4] = [0.5, 1.0, 2.0, 5.0];

#[derive(Debug, Error, PartialEq)]
pub enum SettingsError {
    #[error("unsupported refresh interval: {0}")]
    UnsupportedRefreshInterval(f64),
}

#[derive(Debug, Clone, Deserialize)]
pub struct SettingsUpdate {
    pub refresh_interval: Option<f64>,
    pub process_interval: Option<f64>,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SettingsPayload {
    pub refresh_interval: f64,
    pub allowed_refresh_intervals: Vec<f64>,
    pub process_interval: f64,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(tag = "type")]
pub enum ConfigMessage {
    #[serde(rename = "config")]
    Config {
        refresh_interval: f64,
        process_interval: f64,
    },
}

#[derive(Debug, Clone, PartialEq)]
pub struct ManagerSettings {
    pub refresh_interval: f64,
    process_interval: f64,
}

impl ManagerSettings {
    pub fn new(refresh_interval: f64, process_interval: f64) -> Result<Self, SettingsError> {
        Ok(Self {
            refresh_interval: validate_refresh_interval(refresh_interval)?,
            process_interval: process_interval.max(1.0),
        })
    }

    pub fn from_env(
        refresh_interval: Option<f64>,
        process_interval: Option<f64>,
    ) -> Result<Self, SettingsError> {
        let refresh = refresh_interval
            .or_else(|| {
                std::env::var("CONSTELLA_REFRESH_SECONDS")
                    .ok()?
                    .parse()
                    .ok()
            })
            .unwrap_or(1.0);
        let process = process_interval
            .or_else(|| {
                std::env::var("CONSTELLA_PROCESS_SECONDS")
                    .ok()?
                    .parse()
                    .ok()
            })
            .unwrap_or(3.0);
        Self::new(refresh, process)
    }

    pub fn process_interval(&self) -> f64 {
        self.process_interval.max(self.refresh_interval)
    }

    pub fn to_payload(&self) -> SettingsPayload {
        SettingsPayload {
            refresh_interval: self.refresh_interval,
            allowed_refresh_intervals: ALLOWED_REFRESH_INTERVALS.to_vec(),
            process_interval: self.process_interval(),
        }
    }

    pub fn config_message(&self) -> ConfigMessage {
        ConfigMessage::Config {
            refresh_interval: self.refresh_interval,
            process_interval: self.process_interval(),
        }
    }

    pub fn update(&mut self, update: SettingsUpdate) -> Result<SettingsPayload, SettingsError> {
        if let Some(refresh) = update.refresh_interval {
            self.refresh_interval = validate_refresh_interval(refresh)?;
        }
        if let Some(process) = update.process_interval {
            self.process_interval = process.max(1.0);
        }
        Ok(self.to_payload())
    }
}

pub fn validate_refresh_interval(value: f64) -> Result<f64, SettingsError> {
    if ALLOWED_REFRESH_INTERVALS
        .iter()
        .any(|allowed| (*allowed - value).abs() < f64::EPSILON)
    {
        Ok(value)
    } else {
        Err(SettingsError::UnsupportedRefreshInterval(value))
    }
}
