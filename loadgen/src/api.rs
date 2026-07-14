use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::{Result, bail};
use hdrhistogram::Histogram;
use tokio::sync::{Mutex, mpsc};
use tokio::task::JoinSet;

use crate::metrics::{Counters, HistogramSummary, RunReport, SecondSample, histogram, record_us};
use crate::scheduler::{Job, id_for};
use crate::{ApiArgs, LoadModel, db};

const API_QUERY: &str = "SELECT id, tenant_id, created_at, event_type FROM events WHERE id = $1";

struct WorkerStats {
    counters: Counters,
    queue: Histogram<u64>,
    service: Histogram<u64>,
    end_to_end: Histogram<u64>,
    samples: Vec<SecondSample>,
}

impl WorkerStats {
    fn new(duration: u64) -> Self {
        Self {
            counters: Counters::default(),
            queue: histogram(),
            service: histogram(),
            end_to_end: histogram(),
            samples: (0..duration)
                .map(|second| SecondSample {
                    second,
                    ..SecondSample::default()
                })
                .collect(),
        }
    }

    fn merge(&mut self, other: Self) {
        self.counters.add(&other.counters);
        self.queue.add(&other.queue).expect("compatible histograms");
        self.service
            .add(&other.service)
            .expect("compatible histograms");
        self.end_to_end
            .add(&other.end_to_end)
            .expect("compatible histograms");
        for (target, source) in self.samples.iter_mut().zip(other.samples) {
            target.offered += source.offered;
            target.completed += source.completed;
            target.failed += source.failed;
        }
    }
}

pub async fn run(args: ApiArgs) -> Result<RunReport> {
    if args.id_range_start > args.id_range_end {
        bail!("id-range-start must not exceed id-range-end");
    }
    if args.common.connections == 0 {
        bail!("connections must be greater than zero");
    }
    if matches!(args.model, LoadModel::OpenLoop) && args.target_rate == 0 {
        bail!("target-rate must be greater than zero in open-loop mode");
    }

    let endpoint = db::redact_dsn(&args.common.dsn);
    let output_path = args.common.output.clone();
    let run_id = args.common.run_id.clone();
    let duration = args.common.duration;
    let mut report = RunReport::new(run_id, "api", endpoint, duration, output_path);
    let stats = match args.model {
        LoadModel::ClosedLoop => run_closed_loop(args).await?,
        LoadModel::OpenLoop => run_open_loop(args).await?,
    };

    report.counters = stats.counters;
    report.histograms.insert(
        "queue_delay".to_string(),
        HistogramSummary::from_histogram(&stats.queue),
    );
    report.histograms.insert(
        "service_time".to_string(),
        HistogramSummary::from_histogram(&stats.service),
    );
    report.histograms.insert(
        "end_to_end".to_string(),
        HistogramSummary::from_histogram(&stats.end_to_end),
    );
    report.per_second = stats.samples;
    report.finish();
    Ok(report)
}

async fn run_closed_loop(args: ApiArgs) -> Result<WorkerStats> {
    let mut workers = JoinSet::new();
    for worker_id in 0..args.common.connections {
        let args = args.clone();
        workers.spawn(async move {
            let client = db::connect(
                &args.common.dsn,
                args.common.tls_mode,
                args.common.ca_cert.as_deref(),
                &format!("{}-{}", args.common.application_name, worker_id),
            )
            .await?;
            let statement = if args.prepared {
                Some(client.prepare(API_QUERY).await?)
            } else {
                None
            };
            let warmup_end = crate::measurement_start(&args.common);
            let mut sequence = worker_id as u64;
            while Instant::now() < warmup_end {
                let id = id_for(
                    sequence,
                    args.common.seed,
                    args.id_range_start,
                    args.id_range_end,
                );
                if let Some(statement) = &statement {
                    client.query_one(statement, &[&id]).await?;
                } else {
                    client.query_one(API_QUERY, &[&id]).await?;
                }
                sequence = sequence.wrapping_add(args.common.connections as u64);
            }

            let started = Instant::now();
            let deadline = started + Duration::from_secs(args.common.duration);
            let timeout = Duration::from_millis(args.timeout_ms);
            let mut stats = WorkerStats::new(args.common.duration);
            stats.counters.connections_opened = 1;

            while Instant::now() < deadline {
                let id = id_for(
                    sequence,
                    args.common.seed,
                    args.id_range_start,
                    args.id_range_end,
                );
                sequence = sequence.wrapping_add(args.common.connections as u64);
                let operation_start = Instant::now();
                let second = operation_start
                    .saturating_duration_since(started)
                    .as_secs()
                    .min(args.common.duration.saturating_sub(1))
                    as usize;
                stats.counters.scheduled += 1;
                stats.counters.started += 1;
                stats.samples[second].offered += 1;

                let query = async {
                    if let Some(statement) = &statement {
                        client.query_one(statement, &[&id]).await
                    } else {
                        client.query_one(API_QUERY, &[&id]).await
                    }
                };
                match tokio::time::timeout(timeout, query).await {
                    Ok(Ok(row)) if row.get::<_, i64>(0) == id => {
                        stats.counters.completed += 1;
                        stats.samples[second].completed += 1;
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
                let elapsed = operation_start.elapsed().as_micros();
                record_us(&mut stats.service, elapsed);
                record_us(&mut stats.end_to_end, elapsed);
                record_us(&mut stats.queue, 1);
            }
            Ok::<_, anyhow::Error>(stats)
        });
    }

    collect_workers(workers, args.common.duration).await
}

async fn run_open_loop(args: ApiArgs) -> Result<WorkerStats> {
    let (sender, receiver) = mpsc::channel::<Job>(args.max_inflight);
    let receiver = Arc::new(Mutex::new(receiver));
    let mut workers = JoinSet::new();
    let start = crate::measurement_start(&args.common);
    let end = start + Duration::from_secs(args.common.duration);

    for worker_id in 0..args.common.connections {
        let args = args.clone();
        let receiver = Arc::clone(&receiver);
        let measurement_end = end;
        workers.spawn(async move {
            let client = db::connect(
                &args.common.dsn,
                args.common.tls_mode,
                args.common.ca_cert.as_deref(),
                &format!("{}-{}", args.common.application_name, worker_id),
            )
            .await?;
            let statement = if args.prepared {
                Some(client.prepare(API_QUERY).await?)
            } else {
                None
            };
            let timeout = Duration::from_millis(args.timeout_ms);
            let mut stats = WorkerStats::new(args.common.duration);
            stats.counters.connections_opened = 1;

            loop {
                let job = {
                    let mut guard = receiver.lock().await;
                    guard.recv().await
                };
                let Some(job) = job else { break };
                let operation_start = Instant::now();
                if operation_start >= measurement_end {
                    stats.counters.skipped += 1;
                    continue;
                }
                stats.counters.started += 1;
                record_us(
                    &mut stats.queue,
                    operation_start
                        .saturating_duration_since(job.scheduled_at)
                        .as_micros(),
                );
                let query = async {
                    if let Some(statement) = &statement {
                        client.query_one(statement, &[&job.id]).await
                    } else {
                        client.query_one(API_QUERY, &[&job.id]).await
                    }
                };
                let remaining = measurement_end.saturating_duration_since(operation_start);
                match tokio::time::timeout(timeout.min(remaining), query).await {
                    Ok(Ok(row)) if row.get::<_, i64>(0) == job.id => {
                        stats.counters.completed += 1;
                        stats.samples[job.second].completed += 1;
                    }
                    Ok(Ok(_)) | Ok(Err(_)) => {
                        stats.counters.failed += 1;
                        stats.samples[job.second].failed += 1;
                    }
                    Err(_) => {
                        stats.counters.timed_out += 1;
                        stats.samples[job.second].failed += 1;
                    }
                }
                record_us(&mut stats.service, operation_start.elapsed().as_micros());
                record_us(
                    &mut stats.end_to_end,
                    job.scheduled_at.elapsed().as_micros(),
                );
            }
            Ok::<_, anyhow::Error>(stats)
        });
    }

    tokio::time::sleep_until(tokio::time::Instant::from_std(start)).await;
    let interval = Duration::from_secs_f64(1.0 / args.target_rate as f64);
    let mut next = start;
    let mut sequence = 0_u64;
    let mut scheduler_stats = WorkerStats::new(args.common.duration);

    while next < end {
        tokio::time::sleep_until(tokio::time::Instant::from_std(next)).await;
        let second = next
            .saturating_duration_since(start)
            .as_secs()
            .min(args.common.duration.saturating_sub(1)) as usize;
        scheduler_stats.counters.scheduled += 1;
        scheduler_stats.samples[second].offered += 1;
        let job = Job {
            id: id_for(
                sequence,
                args.common.seed,
                args.id_range_start,
                args.id_range_end,
            ),
            scheduled_at: next,
            second,
        };
        if sender.try_send(job).is_err() {
            scheduler_stats.counters.skipped += 1;
        }
        sequence = sequence.wrapping_add(1);
        next += interval;
    }
    drop(sender);

    let mut combined = collect_workers(workers, args.common.duration).await?;
    combined.counters.scheduled += scheduler_stats.counters.scheduled;
    combined.counters.skipped += scheduler_stats.counters.skipped;
    for (target, source) in combined.samples.iter_mut().zip(scheduler_stats.samples) {
        target.offered += source.offered;
    }
    Ok(combined)
}

async fn collect_workers(
    mut workers: JoinSet<Result<WorkerStats>>,
    duration: u64,
) -> Result<WorkerStats> {
    let mut combined = WorkerStats::new(duration);
    while let Some(result) = workers.join_next().await {
        combined.merge(result??);
    }
    Ok(combined)
}
