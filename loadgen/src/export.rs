use std::fmt::Write;
use std::time::{Duration, Instant};

use anyhow::{Result, bail};
use futures_util::TryStreamExt;
use hdrhistogram::Histogram;
use sha2::{Digest, Sha256};
use tokio::task::JoinSet;

use crate::metrics::{
    Counters, ExportResult, HistogramSummary, RunReport, SecondSample, histogram, record_us,
};
use crate::{ExportArgs, db};

struct ExportStats {
    counters: Counters,
    first_byte: Histogram<u64>,
    completion: Histogram<u64>,
    samples: Vec<SecondSample>,
    exports: Vec<ExportResult>,
}

impl ExportStats {
    fn new(duration: u64) -> Self {
        Self {
            counters: Counters::default(),
            first_byte: histogram(),
            completion: histogram(),
            samples: (0..duration)
                .map(|second| SecondSample {
                    second,
                    ..SecondSample::default()
                })
                .collect(),
            exports: Vec::new(),
        }
    }

    fn merge(&mut self, other: Self) {
        self.counters.add(&other.counters);
        self.first_byte
            .add(&other.first_byte)
            .expect("compatible histograms");
        self.completion
            .add(&other.completion)
            .expect("compatible histograms");
        for (target, source) in self.samples.iter_mut().zip(other.samples) {
            target.offered += source.offered;
            target.completed += source.completed;
            target.failed += source.failed;
            target.bytes += source.bytes;
        }
        self.exports.extend(other.exports);
    }
}

pub async fn run(args: ExportArgs) -> Result<RunReport> {
    if args.concurrency == 0 {
        bail!("concurrency must be greater than zero");
    }
    if args.tenant_range_start > args.tenant_range_end {
        bail!("tenant-range-start must not exceed tenant-range-end");
    }
    if args.consumer_rate_mbps < 0.0 {
        bail!("consumer-rate-mbps cannot be negative");
    }

    let endpoint = db::redact_dsn(&args.common.dsn);
    let mut report = RunReport::new(
        args.common.run_id.clone(),
        "export",
        endpoint,
        args.common.duration,
        args.common.output.clone(),
    );
    let mut workers = JoinSet::new();
    for worker_id in 0..args.concurrency {
        let args = args.clone();
        workers.spawn(async move { run_worker(args, worker_id).await });
    }

    let mut stats = ExportStats::new(args.common.duration);
    while let Some(result) = workers.join_next().await {
        stats.merge(result??);
    }

    report.counters = stats.counters;
    report.histograms.insert(
        "first_byte".to_string(),
        HistogramSummary::from_histogram(&stats.first_byte),
    );
    report.histograms.insert(
        "completion".to_string(),
        HistogramSummary::from_histogram(&stats.completion),
    );
    report.per_second = stats.samples;
    report.exports = stats.exports;
    report.finish();
    Ok(report)
}

async fn run_worker(args: ExportArgs, worker_id: usize) -> Result<ExportStats> {
    let client = db::connect(
        &args.common.dsn,
        args.common.tls_mode,
        args.common.ca_cert.as_deref(),
        &format!("{}-export-{}", args.common.application_name, worker_id),
    )
    .await?;
    client.simple_query("SELECT 1").await?;
    tokio::time::sleep_until(tokio::time::Instant::from_std(crate::measurement_start(
        &args.common,
    )))
    .await;

    let start = Instant::now();
    let deadline = start + Duration::from_secs(args.common.duration);
    let timeout = Duration::from_secs(args.timeout_seconds);
    let tenant_count = (args.tenant_range_end - args.tenant_range_start + 1) as usize;
    let mut iteration = 0_usize;
    let mut stats = ExportStats::new(args.common.duration);
    stats.counters.connections_opened = 1;

    while Instant::now() < deadline {
        let tenant = args.tenant_range_start + ((worker_id + iteration) % tenant_count) as i32;
        let second = start
            .elapsed()
            .as_secs()
            .min(args.common.duration.saturating_sub(1)) as usize;
        stats.counters.scheduled += 1;
        stats.counters.started += 1;
        stats.samples[second].offered += 1;

        match tokio::time::timeout(timeout, run_export(&client, tenant, &args)).await {
            Ok(Ok(result)) if result.bytes > 0 => {
                stats.counters.completed += 1;
                stats.counters.bytes += result.bytes;
                stats.samples[second].completed += 1;
                stats.samples[second].bytes += result.bytes;
                record_us(&mut stats.first_byte, result.first_byte_us as u128);
                record_us(&mut stats.completion, result.completion_us as u128);
                stats.exports.push(result);
            }
            Ok(Ok(_)) | Ok(Err(_)) => {
                stats.counters.failed += 1;
                stats.samples[second].failed += 1;
            }
            Err(_) => {
                stats.counters.timed_out += 1;
                stats.samples[second].failed += 1;
            }
        }
        iteration += 1;
    }
    Ok(stats)
}

async fn run_export(
    client: &tokio_postgres::Client,
    tenant: i32,
    args: &ExportArgs,
) -> Result<ExportResult> {
    let limit = args
        .result_limit_bytes
        .map(|bytes| format!(" LIMIT {}", (bytes / 320).max(1)))
        .unwrap_or_default();
    let query = format!(
        "COPY (SELECT id, created_at, event_type, payload FROM events WHERE tenant_id = {tenant} ORDER BY id{limit}) TO STDOUT WITH (FORMAT csv)"
    );
    let started = Instant::now();
    let stream = client.copy_out(&query).await?;
    tokio::pin!(stream);
    let mut bytes = 0_u64;
    let mut first_byte_us = 0_u64;
    let mut pacing_sleep_us = 0_u64;
    let mut hasher = Sha256::new();
    let bytes_per_second = args.consumer_rate_mbps * 1024.0 * 1024.0;

    while let Some(chunk) = stream.try_next().await? {
        if bytes == 0 {
            first_byte_us = started.elapsed().as_micros() as u64;
        }
        bytes += chunk.len() as u64;
        hasher.update(&chunk);

        if bytes_per_second > 0.0 {
            let target_elapsed = Duration::from_secs_f64(bytes as f64 / bytes_per_second);
            let actual_elapsed = started.elapsed();
            if target_elapsed > actual_elapsed {
                let sleep = target_elapsed - actual_elapsed;
                tokio::time::sleep(sleep).await;
                pacing_sleep_us += sleep.as_micros() as u64;
            }
        }
    }

    let digest = hasher.finalize();
    let mut sha256 = String::with_capacity(digest.len() * 2);
    for byte in digest {
        write!(&mut sha256, "{byte:02x}").expect("writing to a String cannot fail");
    }

    Ok(ExportResult {
        tenant_id: tenant,
        bytes,
        sha256,
        first_byte_us,
        completion_us: started.elapsed().as_micros() as u64,
        pacing_sleep_us,
    })
}
