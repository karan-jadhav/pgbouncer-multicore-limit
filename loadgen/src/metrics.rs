use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use hdrhistogram::Histogram;
use hdrhistogram::serialization::{Serializer, V2Serializer};
use serde::Serialize;

#[derive(Debug, Clone, Default, Serialize)]
pub struct Counters {
    pub scheduled: u64,
    pub started: u64,
    pub completed: u64,
    pub failed: u64,
    pub timed_out: u64,
    pub skipped: u64,
    pub bytes: u64,
    pub connections_opened: u64,
    pub connections_failed: u64,
    pub cancellations_succeeded: u64,
    pub cancellations_failed: u64,
}

impl Counters {
    pub fn add(&mut self, other: &Self) {
        self.scheduled += other.scheduled;
        self.started += other.started;
        self.completed += other.completed;
        self.failed += other.failed;
        self.timed_out += other.timed_out;
        self.skipped += other.skipped;
        self.bytes += other.bytes;
        self.connections_opened += other.connections_opened;
        self.connections_failed += other.connections_failed;
        self.cancellations_succeeded += other.cancellations_succeeded;
        self.cancellations_failed += other.cancellations_failed;
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct HistogramSummary {
    pub count: u64,
    pub min_us: u64,
    pub p50_us: u64,
    pub p95_us: u64,
    pub p99_us: u64,
    pub max_us: u64,
    pub hdr_v2_base64: String,
}

impl HistogramSummary {
    pub fn from_histogram(histogram: &Histogram<u64>) -> Self {
        let mut encoded = Vec::new();
        V2Serializer::new()
            .serialize(histogram, &mut encoded)
            .expect("serializing a histogram to memory cannot fail");
        Self {
            count: histogram.len(),
            min_us: histogram.min(),
            p50_us: histogram.value_at_quantile(0.50),
            p95_us: histogram.value_at_quantile(0.95),
            p99_us: histogram.value_at_quantile(0.99),
            max_us: histogram.max(),
            hdr_v2_base64: STANDARD.encode(encoded),
        }
    }
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct SecondSample {
    pub second: u64,
    pub offered: u64,
    pub completed: u64,
    pub failed: u64,
    pub bytes: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct ExportResult {
    pub tenant_id: i32,
    pub bytes: u64,
    pub sha256: String,
    pub first_byte_us: u64,
    pub completion_us: u64,
    pub pacing_sleep_us: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct RunReport {
    pub schema_version: u32,
    pub run_id: String,
    pub mode: String,
    pub endpoint: String,
    pub started_at_unix_ms: u128,
    pub ended_at_unix_ms: u128,
    pub duration_seconds: u64,
    pub counters: Counters,
    pub histograms: BTreeMap<String, HistogramSummary>,
    pub per_second: Vec<SecondSample>,
    pub exports: Vec<ExportResult>,
    pub validation: BTreeMap<String, serde_json::Value>,
    #[serde(skip)]
    pub output_path: PathBuf,
}

impl RunReport {
    pub fn new(
        run_id: String,
        mode: &str,
        endpoint: String,
        duration_seconds: u64,
        output_path: PathBuf,
    ) -> Self {
        Self {
            schema_version: 1,
            run_id,
            mode: mode.to_string(),
            endpoint,
            started_at_unix_ms: unix_ms(),
            ended_at_unix_ms: 0,
            duration_seconds,
            counters: Counters::default(),
            histograms: BTreeMap::new(),
            per_second: Vec::new(),
            exports: Vec::new(),
            validation: BTreeMap::new(),
            output_path,
        }
    }

    pub fn finish(&mut self) {
        self.ended_at_unix_ms = unix_ms();
    }

    pub fn merge(mode: &str, mut left: Self, right: Self) -> Self {
        left.mode = mode.to_string();
        left.counters.add(&right.counters);
        for (name, histogram) in right.histograms {
            left.histograms.insert(format!("export_{name}"), histogram);
        }
        left.exports.extend(right.exports);
        left.ended_at_unix_ms = left.ended_at_unix_ms.max(right.ended_at_unix_ms);
        left
    }
}

pub fn histogram() -> Histogram<u64> {
    Histogram::new_with_max(3_600_000_000, 3).expect("valid histogram configuration")
}

pub fn record_us(histogram: &mut Histogram<u64>, micros: u128) {
    let value = micros.clamp(1, 3_600_000_000) as u64;
    let _ = histogram.record(value);
}

fn unix_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}
