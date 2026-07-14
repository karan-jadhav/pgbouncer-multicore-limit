use std::collections::BTreeMap;
use std::fs::File;
use std::io::{self, BufWriter};
use std::path::Path;

use anyhow::{Context, Result, bail};
use serde_json::json;

use crate::ValidateArgs;
use crate::db;
use crate::metrics::RunReport;

pub fn write_report(report: &RunReport, output_path: &Path) -> Result<()> {
    if output_path == Path::new("-") {
        serde_json::to_writer_pretty(io::stdout().lock(), report)?;
        println!();
        return Ok(());
    }

    if let Some(parent) = output_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let file = File::create(output_path)
        .with_context(|| format!("failed to create {}", output_path.display()))?;
    serde_json::to_writer_pretty(BufWriter::new(file), report)?;
    Ok(())
}

pub async fn validate_database(args: ValidateArgs) -> Result<RunReport> {
    let mut report = RunReport::new(
        args.common.run_id.clone(),
        "validate",
        db::redact_dsn(&args.common.dsn),
        0,
        args.common.output.clone(),
    );
    let client = db::connect(
        &args.common.dsn,
        args.common.tls_mode,
        args.common.ca_cert.as_deref(),
        &args.common.application_name,
    )
    .await?;
    let row = client
        .query_one(
            "SELECT count(*)::bigint, min(id)::bigint, max(id)::bigint, count(DISTINCT tenant_id)::bigint FROM events",
            &[],
        )
        .await?;
    let rows: i64 = row.get(0);
    if let Some(expected) = args.expected_rows
        && rows != expected
    {
        bail!("expected {expected} rows but database contains {rows}");
    }

    let sample = client
        .query_one(
            "SELECT id, tenant_id FROM events WHERE id = (SELECT min(id) FROM events)",
            &[],
        )
        .await?;
    let mut validation = BTreeMap::new();
    validation.insert("rows".to_string(), json!(rows));
    validation.insert("min_id".to_string(), json!(row.get::<_, i64>(1)));
    validation.insert("max_id".to_string(), json!(row.get::<_, i64>(2)));
    validation.insert("tenant_count".to_string(), json!(row.get::<_, i64>(3)));
    validation.insert("sample_id".to_string(), json!(sample.get::<_, i64>(0)));
    validation.insert("sample_tenant".to_string(), json!(sample.get::<_, i32>(1)));

    report.validation = validation;
    report.counters.completed = 1;
    report.finish();
    Ok(report)
}
