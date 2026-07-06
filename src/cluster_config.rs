use std::path::{Path, PathBuf};

use serde::Deserialize;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ClusterConfigError {
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Yaml(#[from] serde_yaml::Error),
    #[error("nodes config must include at least one node")]
    EmptyNodes,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ClusterConfig {
    pub manager_hostname: Option<String>,
    pub manager_url: String,
    pub agent_token_file: PathBuf,
    pub refresh_interval: f64,
    pub process_interval: f64,
    pub remote_base: String,
    pub nodes: Vec<ClusterNode>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClusterNode {
    pub id: String,
    pub host: String,
    pub user: Option<String>,
    pub port: Option<u16>,
}

#[derive(Debug, Deserialize)]
struct RawClusterConfig {
    manager_hostname: Option<String>,
    manager_url: String,
    agent_token_file: PathBuf,
    refresh_interval: Option<f64>,
    process_interval: Option<f64>,
    remote_base: Option<String>,
    nodes: Vec<RawClusterNode>,
}

#[derive(Debug, Deserialize)]
struct RawClusterNode {
    id: String,
    host: String,
    user: Option<String>,
    port: Option<u16>,
}

pub fn load_cluster_config(path: impl AsRef<Path>) -> Result<ClusterConfig, ClusterConfigError> {
    let path = path.as_ref();
    let text = std::fs::read_to_string(path)?;
    let raw: RawClusterConfig = serde_yaml::from_str(&text)?;
    if raw.nodes.is_empty() {
        return Err(ClusterConfigError::EmptyNodes);
    }
    let base_dir = path.parent().unwrap_or_else(|| Path::new("."));
    let agent_token_file = if raw.agent_token_file.is_absolute() {
        raw.agent_token_file
    } else {
        base_dir.join(raw.agent_token_file)
    };
    Ok(ClusterConfig {
        manager_hostname: raw.manager_hostname,
        manager_url: raw.manager_url,
        agent_token_file: agent_token_file.canonicalize().unwrap_or(agent_token_file),
        refresh_interval: raw.refresh_interval.unwrap_or(1.0),
        process_interval: raw.process_interval.unwrap_or(3.0),
        remote_base: raw
            .remote_base
            .unwrap_or_else(|| "$HOME/.constella".to_string()),
        nodes: raw
            .nodes
            .into_iter()
            .map(|node| ClusterNode {
                id: node.id,
                host: node.host,
                user: node.user,
                port: node.port,
            })
            .collect(),
    })
}

pub fn load_manager_hostname(path: impl AsRef<Path>) -> Result<Option<String>, ClusterConfigError> {
    Ok(load_cluster_config(path)?.manager_hostname)
}
