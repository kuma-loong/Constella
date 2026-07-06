use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::Context;
use clap::{Parser, Subcommand};
use constella::agent::{run_agent, AgentConfig};
use constella::api::{app, AppState};
use constella::cluster::ClusterState;
use constella::cluster_config::load_manager_hostname;
use constella::db::{SQLiteStore, RAW_SNAPSHOT_RETENTION_SECONDS};
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
    Agent(AgentArgs),
    Db(DbArgs),
    Config(ConfigArgs),
}

#[derive(Debug, Parser)]
struct ConfigArgs {
    #[command(subcommand)]
    command: ConfigCommand,
}

#[derive(Debug, Subcommand)]
enum ConfigCommand {
    ManagerHostname {
        #[arg(long, default_value = "nodes.yaml")]
        nodes: PathBuf,
    },
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

#[derive(Debug, Parser)]
struct AgentArgs {
    #[arg(long)]
    node_id: Option<String>,
    #[arg(long, env = "CONSTELLA_MANAGER_URL")]
    manager_url: Option<String>,
    #[arg(long, env = "CONSTELLA_AGENT_TOKEN")]
    token: Option<String>,
    #[arg(long, env = "CONSTELLA_AGENT_TOKEN_FILE")]
    token_file: Option<PathBuf>,
    #[arg(long, env = "CONSTELLA_REFRESH_SECONDS")]
    refresh: Option<f64>,
    #[arg(long, env = "CONSTELLA_PROCESS_SECONDS")]
    process_refresh: Option<f64>,
    #[arg(long, env = "CONSTELLA_AGENT_STATE_FILE")]
    state_file: Option<PathBuf>,
}

#[derive(Debug, Parser)]
struct DbArgs {
    #[command(subcommand)]
    command: Option<DbCommand>,
}

#[derive(Debug, Subcommand)]
enum DbCommand {
    Maintain {
        #[arg(long, default_value = "run/constella.db")]
        path: PathBuf,
        #[arg(long, default_value_t = RAW_SNAPSHOT_RETENTION_SECONDS)]
        raw_retention_seconds: f64,
        #[arg(long, default_value_t = 300.0)]
        session_stale_seconds: f64,
    },
    Rollup {
        #[arg(long, default_value = "run/constella.db")]
        path: PathBuf,
        #[arg(long)]
        from_bucket_seconds: i64,
        #[arg(long)]
        to_bucket_seconds: i64,
    },
    PruneRollups {
        #[arg(long, default_value = "run/constella.db")]
        path: PathBuf,
        #[arg(long)]
        bucket_seconds: Option<i64>,
    },
    PruneRaw {
        #[arg(long, default_value = "run/constella.db")]
        path: PathBuf,
        #[arg(long, default_value_t = RAW_SNAPSHOT_RETENTION_SECONDS)]
        retention_seconds: f64,
    },
    CloseSessions {
        #[arg(long, default_value = "run/constella.db")]
        path: PathBuf,
        #[arg(long, default_value_t = 60.0)]
        stale_seconds: f64,
    },
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
        Command::Agent(args) => agent(args).await,
        Command::Db(args) => db(args),
        Command::Config(args) => config(args),
    }
}

async fn agent(args: AgentArgs) -> anyhow::Result<()> {
    let config = AgentConfig::from_env(
        args.node_id,
        args.manager_url,
        args.token,
        args.token_file,
        args.refresh,
        args.process_refresh,
        args.state_file,
    )?;
    run_agent(config).await?;
    Ok(())
}

fn db(args: DbArgs) -> anyhow::Result<()> {
    let Some(command) = args.command else {
        return Ok(());
    };
    match command {
        DbCommand::Maintain {
            path,
            raw_retention_seconds,
            session_stale_seconds,
        } => {
            let mut store = open_store(path)?;
            let result =
                store.maintain(now_seconds(), session_stale_seconds, raw_retention_seconds)?;
            for (key, value) in result.to_map() {
                println!("{key}: {value}");
            }
            store.close();
        }
        DbCommand::Rollup {
            path,
            from_bucket_seconds,
            to_bucket_seconds,
        } => {
            let mut store = open_store(path)?;
            let count = store.rollup_gpu_metric_rollups(
                from_bucket_seconds,
                to_bucket_seconds,
                now_seconds(),
            )?;
            println!(
                "rolled up {count} GPU buckets {from_bucket_seconds}s -> {to_bucket_seconds}s"
            );
            store.close();
        }
        DbCommand::PruneRollups {
            path,
            bucket_seconds,
        } => {
            let mut store = open_store(path)?;
            let count = store.prune_rollups(now_seconds(), bucket_seconds)?;
            println!("deleted {count} expired rollups");
            store.close();
        }
        DbCommand::PruneRaw {
            path,
            retention_seconds,
        } => {
            let mut store = open_store(path)?;
            let count = store.prune_raw_snapshots(now_seconds(), retention_seconds)?;
            println!("deleted {count} raw snapshots");
            store.close();
        }
        DbCommand::CloseSessions {
            path,
            stale_seconds,
        } => {
            let mut store = open_store(path)?;
            let count = store.close_stale_sessions(now_seconds(), stale_seconds)?;
            println!("closed {count} process sessions");
            store.close();
        }
    }
    Ok(())
}

fn open_store(path: PathBuf) -> anyhow::Result<SQLiteStore> {
    let mut store = SQLiteStore::new(path);
    store.open()?;
    Ok(store)
}

fn now_seconds() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

fn config(args: ConfigArgs) -> anyhow::Result<()> {
    match args.command {
        ConfigCommand::ManagerHostname { nodes } => {
            if let Some(hostname) = load_manager_hostname(nodes)? {
                println!("{hostname}");
            }
            Ok(())
        }
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
