use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::{Result, bail};
use hdrhistogram::Histogram;
use tokio::sync::Semaphore;
use tokio::task::JoinSet;

use crate::metrics::{Counters, HistogramSummary, RunReport, SecondSample, histogram, record_us};
use crate::{ConnectionArgs, ConnectionMode, db};

struct ConnectionStats {
    counters: Counters,
    latency: Histogram<u64>,
    samples: Vec<SecondSample>,
}

impl ConnectionStats {
    fn new(duration: u64) -> Self {
        Self {
            counters: Counters::default(),
            latency: histogram(),
            samples: (0..duration)
                .map(|second| SecondSample {
                    second,
                    ..SecondSample::default()
                })
                .collect(),
        }
    }
}

pub async fn run(args: ConnectionArgs) -> Result<RunReport> {
    if args.common.connections == 0 {
        bail!("connections must be greater than zero");
    }
    let endpoint = db::redact_dsn(&args.common.dsn);
    let mut report = RunReport::new(
        args.common.run_id.clone(),
        "connections",
        endpoint,
        args.common.duration,
        args.common.output.clone(),
    );
    let stats = match args.mode {
        ConnectionMode::Hold => run_hold(args.clone()).await?,
        ConnectionMode::Churn => run_churn(args.clone()).await?,
    };
    report.counters = stats.counters;
    report.histograms.insert(
        "connection_latency".to_string(),
        HistogramSummary::from_histogram(&stats.latency),
    );
    report.per_second = stats.samples;
    report.finish();
    Ok(report)
}

async fn run_hold(args: ConnectionArgs) -> Result<ConnectionStats> {
    let mut tasks = JoinSet::new();
    for id in 0..args.common.connections {
        let args = args.clone();
        tasks.spawn(async move {
            let started = Instant::now();
            let client = db::connect(
                &args.common.dsn,
                args.common.tls_mode,
                args.common.ca_cert.as_deref(),
                &format!("{}-hold-{}", args.common.application_name, id),
            )
            .await;
            (started.elapsed(), client)
        });
    }

    let mut clients = Vec::new();
    let mut stats = ConnectionStats::new(args.common.duration);
    stats.counters.scheduled = args.common.connections as u64;
    while let Some(result) = tasks.join_next().await {
        let (elapsed, client) = result?;
        record_us(&mut stats.latency, elapsed.as_micros());
        match client {
            Ok(client) => {
                if args.health_query {
                    client.simple_query("SELECT 1").await?;
                }
                stats.counters.connections_opened += 1;
                clients.push(client);
            }
            Err(_) => stats.counters.connections_failed += 1,
        }
    }
    stats.counters.completed = stats.counters.connections_opened;
    tokio::time::sleep(Duration::from_secs(args.common.duration)).await;
    drop(clients);
    Ok(stats)
}

async fn run_churn(args: ConnectionArgs) -> Result<ConnectionStats> {
    if args.target_rate == 0 {
        bail!("target-rate must be greater than zero in churn mode");
    }
    let start = Instant::now();
    let end = start + Duration::from_secs(args.common.duration);
    let interval = Duration::from_secs_f64(1.0 / args.target_rate as f64);
    let semaphore = Arc::new(Semaphore::new(args.common.connections));
    let mut next = start;
    let mut tasks = JoinSet::new();
    let mut stats = ConnectionStats::new(args.common.duration);

    while next < end {
        tokio::time::sleep_until(tokio::time::Instant::from_std(next)).await;
        let second = next
            .saturating_duration_since(start)
            .as_secs()
            .min(args.common.duration.saturating_sub(1)) as usize;
        stats.counters.scheduled += 1;
        stats.samples[second].offered += 1;
        let Ok(permit) = Arc::clone(&semaphore).try_acquire_owned() else {
            stats.counters.skipped += 1;
            next += interval;
            continue;
        };
        if Instant::now() >= end {
            stats.counters.skipped += 1;
            next += interval;
            continue;
        }
        let args = args.clone();
        tasks.spawn(async move {
            let _permit = permit;
            let started = Instant::now();
            let result = async {
                let client = db::connect(
                    &args.common.dsn,
                    args.common.tls_mode,
                    args.common.ca_cert.as_deref(),
                    &args.common.application_name,
                )
                .await?;
                if args.health_query {
                    client.simple_query("SELECT 1").await?;
                }
                Ok::<_, anyhow::Error>(())
            }
            .await;
            (second, started.elapsed(), result)
        });
        next += interval;
    }

    while let Some(result) = tasks.join_next().await {
        let (second, elapsed, outcome) = result?;
        record_us(&mut stats.latency, elapsed.as_micros());
        match outcome {
            Ok(()) => {
                stats.counters.connections_opened += 1;
                stats.counters.completed += 1;
                stats.samples[second].completed += 1;
            }
            Err(_) => {
                stats.counters.connections_failed += 1;
                stats.counters.failed += 1;
                stats.samples[second].failed += 1;
            }
        }
    }
    Ok(stats)
}
