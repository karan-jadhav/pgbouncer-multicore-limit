use std::time::{Duration, Instant};

use anyhow::{Result, bail};
use hdrhistogram::Histogram;
use tokio::task::JoinSet;
use tokio_postgres::error::SqlState;

use crate::metrics::{Counters, HistogramSummary, RunReport, histogram, record_us};
use crate::{CancelArgs, db};

struct CancelStats {
    counters: Counters,
    latency: Histogram<u64>,
}

pub async fn run(args: CancelArgs) -> Result<RunReport> {
    if args.count == 0 {
        bail!("count must be greater than zero");
    }
    let endpoint = db::redact_dsn(&args.common.dsn);
    let mut report = RunReport::new(
        args.common.run_id.clone(),
        "cancel",
        endpoint,
        args.common.duration,
        args.common.output.clone(),
    );
    let mut tasks = JoinSet::new();
    for id in 0..args.count {
        let args = args.clone();
        tasks.spawn(async move {
            let client = db::connect(
                &args.common.dsn,
                args.common.tls_mode,
                args.common.ca_cert.as_deref(),
                &format!("{}-cancel-{}", args.common.application_name, id),
            )
            .await?;
            let token = client.cancel_token();
            let query_seconds = args.query_seconds as f64;
            let query = tokio::spawn(async move {
                client
                    .query_one("SELECT pg_sleep($1::double precision)", &[&query_seconds])
                    .await
            });
            tokio::time::sleep(Duration::from_millis(args.cancel_after_ms)).await;
            let cancel_started = Instant::now();
            db::cancel(&token, args.common.tls_mode, args.common.ca_cert.as_deref()).await?;
            let result = query.await?;
            let cancelled = result
                .err()
                .and_then(|error| error.code().cloned())
                .is_some_and(|code| code == SqlState::QUERY_CANCELED);
            Ok::<_, anyhow::Error>((cancelled, cancel_started.elapsed()))
        });
    }

    let mut stats = CancelStats {
        counters: Counters {
            scheduled: args.count as u64,
            started: args.count as u64,
            ..Counters::default()
        },
        latency: histogram(),
    };
    while let Some(result) = tasks.join_next().await {
        match result? {
            Ok((true, elapsed)) => {
                stats.counters.completed += 1;
                stats.counters.cancellations_succeeded += 1;
                record_us(&mut stats.latency, elapsed.as_micros());
            }
            Ok((false, elapsed)) => {
                stats.counters.failed += 1;
                stats.counters.cancellations_failed += 1;
                record_us(&mut stats.latency, elapsed.as_micros());
            }
            Err(_) => {
                stats.counters.failed += 1;
                stats.counters.cancellations_failed += 1;
            }
        }
    }

    report.counters = stats.counters;
    report.histograms.insert(
        "cancellation_delay".to_string(),
        HistogramSummary::from_histogram(&stats.latency),
    );
    report.finish();
    Ok(report)
}
