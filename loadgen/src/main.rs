mod api;
mod cancellation;
mod connections;
mod db;
mod export;
mod metrics;
mod output;
mod scheduler;

use std::path::PathBuf;

use anyhow::Result;
use clap::{Args, Parser, Subcommand, ValueEnum};
use serde::Serialize;

use db::TlsMode;

#[derive(Debug, Parser)]
#[command(
    name = "loadgen",
    version,
    about = "PgBouncer experiment load generator"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Api(ApiArgs),
    Export(ExportArgs),
    Connections(ConnectionArgs),
    Cancel(CancelArgs),
    Mixed(MixedArgs),
    Validate(ValidateArgs),
}

#[derive(Debug, Clone, Args)]
struct CommonArgs {
    #[arg(
        long,
        default_value = "postgresql://bench_login:bench-password@127.0.0.1:6432/bench"
    )]
    dsn: String,

    #[arg(long, default_value_t = 10)]
    duration: u64,

    #[arg(long, default_value_t = 2)]
    warmup: u64,

    #[arg(long, default_value_t = 16)]
    connections: usize,

    #[arg(long, value_enum, default_value_t = TlsMode::Disable)]
    tls_mode: TlsMode,

    #[arg(long)]
    ca_cert: Option<PathBuf>,

    #[arg(long, default_value_t = 1)]
    seed: u64,

    #[arg(long, default_value = "-")]
    output: PathBuf,

    #[arg(long, default_value = "manual")]
    run_id: String,

    #[arg(long, default_value = "pgbouncer-loadgen")]
    application_name: String,

    #[arg(long)]
    start_at_unix_ms: Option<u64>,
}

#[derive(Debug, Clone, Copy, ValueEnum, Serialize)]
#[serde(rename_all = "kebab-case")]
enum LoadModel {
    ClosedLoop,
    OpenLoop,
}

#[derive(Debug, Clone, Args)]
struct ApiArgs {
    #[command(flatten)]
    common: CommonArgs,

    #[arg(long, value_enum, default_value_t = LoadModel::ClosedLoop)]
    model: LoadModel,

    #[arg(long, default_value_t = 1000)]
    target_rate: u64,

    #[arg(long, default_value_t = 4096)]
    max_inflight: usize,

    #[arg(long, default_value_t = 1)]
    id_range_start: i64,

    #[arg(long, default_value_t = 100000)]
    id_range_end: i64,

    #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
    prepared: bool,

    #[arg(long, default_value_t = 5000)]
    timeout_ms: u64,
}

#[derive(Debug, Clone, Args)]
struct ExportArgs {
    #[command(flatten)]
    common: CommonArgs,

    #[arg(long, default_value_t = 1)]
    concurrency: usize,

    #[arg(long, default_value_t = 0)]
    tenant_range_start: i32,

    #[arg(long, default_value_t = 99)]
    tenant_range_end: i32,

    #[arg(long, default_value_t = 0.0)]
    consumer_rate_mbps: f64,

    #[arg(long)]
    result_limit_bytes: Option<u64>,

    #[arg(long, default_value_t = 120)]
    timeout_seconds: u64,
}

#[derive(Debug, Clone, Copy, ValueEnum, Serialize)]
#[serde(rename_all = "kebab-case")]
enum ConnectionMode {
    Hold,
    Churn,
}

#[derive(Debug, Clone, Args)]
struct ConnectionArgs {
    #[command(flatten)]
    common: CommonArgs,

    #[arg(long, value_enum, default_value_t = ConnectionMode::Hold)]
    mode: ConnectionMode,

    #[arg(long, default_value_t = 100)]
    target_rate: u64,

    #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
    health_query: bool,
}

#[derive(Debug, Clone, Args)]
struct CancelArgs {
    #[command(flatten)]
    common: CommonArgs,

    #[arg(long, default_value_t = 10)]
    count: usize,

    #[arg(long, default_value_t = 30)]
    query_seconds: u64,

    #[arg(long, default_value_t = 250)]
    cancel_after_ms: u64,
}

#[derive(Debug, Clone, Args)]
struct MixedArgs {
    #[command(flatten)]
    common: CommonArgs,

    #[arg(long, default_value_t = 1000)]
    api_target_rate: u64,

    #[arg(long, default_value_t = 4)]
    export_concurrency: usize,

    #[arg(long, default_value_t = 0.0)]
    consumer_rate_mbps: f64,

    #[arg(long)]
    export_dsn: Option<String>,
}

#[derive(Debug, Clone, Args)]
struct ValidateArgs {
    #[command(flatten)]
    common: CommonArgs,

    #[arg(long)]
    expected_rows: Option<i64>,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .init();
    let cli = Cli::parse();

    let report = match cli.command {
        Command::Api(args) => api::run(args).await?,
        Command::Export(args) => export::run(args).await?,
        Command::Connections(args) => {
            wait_for_start(&args.common).await;
            connections::run(args).await?
        }
        Command::Cancel(args) => {
            wait_for_start(&args.common).await;
            cancellation::run(args).await?
        }
        Command::Mixed(args) => run_mixed(args).await?,
        Command::Validate(args) => {
            wait_for_start(&args.common).await;
            output::validate_database(args).await?
        }
    };

    let output_path = report.output_path.clone();
    output::write_report(&report, &output_path)?;
    Ok(())
}

async fn wait_for_start(common: &CommonArgs) {
    let Some(target_ms) = common.start_at_unix_ms else {
        return;
    };
    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
    if target_ms > now_ms {
        tokio::time::sleep(std::time::Duration::from_millis(target_ms - now_ms)).await;
    }
}

fn measurement_start(common: &CommonArgs) -> std::time::Instant {
    if let Some(target_ms) = common.start_at_unix_ms {
        let now_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64;
        return std::time::Instant::now()
            + std::time::Duration::from_millis(target_ms.saturating_sub(now_ms));
    }
    std::time::Instant::now() + std::time::Duration::from_secs(common.warmup)
}

async fn run_mixed(args: MixedArgs) -> Result<metrics::RunReport> {
    let api_args = ApiArgs {
        common: args.common.clone(),
        model: LoadModel::OpenLoop,
        target_rate: args.api_target_rate,
        max_inflight: 4096,
        id_range_start: 1,
        id_range_end: 100000,
        prepared: true,
        timeout_ms: 5000,
    };
    let mut export_common = args.common.clone();
    export_common.dsn = args.export_dsn.unwrap_or_else(|| args.common.dsn.clone());
    export_common.output = PathBuf::from("-");
    let export_args = ExportArgs {
        common: export_common,
        concurrency: args.export_concurrency,
        tenant_range_start: 0,
        tenant_range_end: 99,
        consumer_rate_mbps: args.consumer_rate_mbps,
        result_limit_bytes: None,
        timeout_seconds: 120,
    };

    let (api_report, export_report) =
        tokio::try_join!(api::run(api_args), export::run(export_args))?;
    Ok(metrics::RunReport::merge(
        "mixed",
        api_report,
        export_report,
    ))
}
