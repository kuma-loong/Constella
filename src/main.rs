use std::net::SocketAddr;
use std::path::PathBuf;

use anyhow::Context;
use clap::{Parser, Subcommand};
use constella::api::{app, AppState};
use constella::cluster::ClusterState;
use constella::schema::local_node_id;
use constella::settings::ManagerSettings;

#[derive(Debug, Parser)]
#[command(name = "constella")]
#[command(version, about)]
struct Cli {
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Debug, Subcommand)]
enum Command {
    Serve(ServeArgs),
}

#[derive(Debug, Parser)]
struct ServeArgs {
    #[arg(long, default_value = "127.0.0.1")]
    host: String,
    #[arg(long, default_value_t = 8765)]
    port: u16,
    #[arg(long, env = "CONSTELLA_AGENT_TOKEN")]
    agent_token: Option<String>,
    #[arg(long, env = "CONSTELLA_AGENT_TOKEN_FILE")]
    agent_token_file: Option<PathBuf>,
    #[arg(long, env = "CONSTELLA_REFRESH_SECONDS")]
    refresh: Option<f64>,
    #[arg(long, env = "CONSTELLA_PROCESS_SECONDS")]
    process_refresh: Option<f64>,
    #[arg(long, env = "CONSTELLA_DB_PATH")]
    db_path: Option<PathBuf>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    let cli = Cli::parse();
    match cli.command.unwrap_or(Command::Serve(ServeArgs {
        host: "127.0.0.1".to_string(),
        port: 8765,
        agent_token: None,
        agent_token_file: None,
        refresh: None,
        process_refresh: None,
        db_path: None,
    })) {
        Command::Serve(args) => serve(args).await,
    }
}

async fn serve(args: ServeArgs) -> anyhow::Result<()> {
    let settings = ManagerSettings::from_env(args.refresh, args.process_refresh)?;
    let agent_token = match (args.agent_token, args.agent_token_file) {
        (Some(token), _) if !token.is_empty() => Some(token),
        (_, Some(path)) => std::fs::read_to_string(&path)
            .ok()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty()),
        _ => std::env::var("CONSTELLA_AGENT_TOKEN_FILE")
            .ok()
            .and_then(|path| std::fs::read_to_string(path).ok())
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty()),
    };
    let mut state = AppState::new(
        ClusterState::new(local_node_id(None)),
        settings,
        agent_token,
    );
    if let Some(db_path) = args.db_path {
        state = state.with_db_path(db_path);
    }
    let addr: SocketAddr = format!("{}:{}", args.host, args.port)
        .parse()
        .context("invalid listen address")?;
    let listener = tokio::net::TcpListener::bind(addr).await?;
    tracing::info!(%addr, "starting constella rust backend");
    axum::serve(listener, app(state))
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}
