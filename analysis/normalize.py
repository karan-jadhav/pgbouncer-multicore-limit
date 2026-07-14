from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import pandas as pd


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _process_cpu_percent(path: Path) -> float | None:
    samples = _read_jsonl(path)
    if len(samples) < 2:
        return None
    first, last = samples[0], samples[-1]
    elapsed = float(last["timestamp_unix"]) - float(first["timestamp_unix"])
    if elapsed <= 0:
        return None
    first_ticks = sum(
        int(process["utime_ticks"]) + int(process["stime_ticks"])
        for process in first.get("processes", [])
    )
    last_ticks = sum(
        int(process["utime_ticks"]) + int(process["stime_ticks"])
        for process in last.get("processes", [])
    )
    return max(last_ticks - first_ticks, 0) / os.sysconf("SC_CLK_TCK") / elapsed * 100.0


def normalize_run(run_dir: Path) -> dict[str, Any]:
    manifest = json.loads((run_dir / "manifest.json").read_text())
    run_definition = manifest.get("metadata", {}).get("run_definition", {})
    loadgen_path = run_dir / "raw" / "loadgen.json"
    loadgen = json.loads(loadgen_path.read_text()) if loadgen_path.exists() else {}
    counters = loadgen.get("counters", {})
    duration = float(loadgen.get("duration_seconds") or manifest.get("measure_seconds") or 0)
    scheduled = int(counters.get("scheduled", 0))
    skipped = int(counters.get("skipped", 0))
    expected = max(scheduled - skipped, 0)
    completed = int(counters.get("completed", 0))
    end_to_end = loadgen.get("histograms", {}).get("end_to_end", {})
    completion = loadgen.get("histograms", {}).get("completion", {})

    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "environment": manifest["environment"],
        "matrix": manifest["matrix"],
        "workload": manifest["workload"],
        "topology": manifest["topology"],
        "processes": int(manifest["pgbouncer_processes"]),
        "pool_size_per_process": int(manifest["pool_size_per_process"]),
        "total_pool_budget": int(manifest["total_pool_budget"]),
        "tls_mode": manifest["tls_mode"],
        "client_tls_sslmode": run_definition.get("client_tls_sslmode", "require"),
        "server_tls_sslmode": run_definition.get("server_tls_sslmode", "verify-full"),
        "peering_enabled": run_definition.get("peering_enabled", True),
        "offered_rate": manifest.get("offered_rate"),
        "repeat": int(manifest["repeat_number"]),
        "duration_seconds": duration,
        "scheduled": scheduled,
        "completed": completed,
        "failed": int(counters.get("failed", 0)),
        "timed_out": int(counters.get("timed_out", 0)),
        "skipped": skipped,
        "connections_opened": int(counters.get("connections_opened", 0)),
        "connections_failed": int(counters.get("connections_failed", 0)),
        "cancellations_succeeded": int(counters.get("cancellations_succeeded", 0)),
        "cancellations_failed": int(counters.get("cancellations_failed", 0)),
        "completion_ratio": completed / expected if expected else math.nan,
        "completed_per_second": completed / duration if duration else math.nan,
        "bytes": int(counters.get("bytes", 0)),
        "export_mib_per_second": int(counters.get("bytes", 0)) / 1024 / 1024 / duration if duration else math.nan,
        "latency_p50_us": end_to_end.get("p50_us"),
        "latency_p95_us": end_to_end.get("p95_us"),
        "latency_p99_us": end_to_end.get("p99_us"),
        "export_completion_p95_us": completion.get("p95_us"),
        "pgbouncer_cpu_percent": _process_cpu_percent(run_dir / "raw" / "process.jsonl"),
        "rejection_reason": manifest.get("rejection_reason"),
    }


def normalize_results(results: Path, accepted_only: bool = True) -> pd.DataFrame:
    statuses = ["accepted"] if accepted_only else ["accepted", "rejected"]
    rows = []
    for status in statuses:
        status_dir = results / status
        if not status_dir.exists():
            continue
        for run_dir in sorted(path for path in status_dir.iterdir() if path.is_dir()):
            if (run_dir / "manifest.json").exists():
                rows.append(normalize_run(run_dir))
    return pd.DataFrame(rows)
