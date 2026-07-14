from __future__ import annotations

import json
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    reasons: list[str]


def validate_loadgen(path: Path, rules: dict[str, Any] | None = None) -> ValidationResult:
    rules = rules or {}
    report = json.loads(path.read_text())
    counters = report.get("counters", {})
    reasons: list[str] = []

    completed = int(counters.get("completed", 0))
    failed = int(counters.get("failed", 0))
    timed_out = int(counters.get("timed_out", 0))
    scheduled = int(counters.get("scheduled", 0))
    skipped = int(counters.get("skipped", 0))

    if completed < int(rules.get("minimum_completed", 1)):
        reasons.append("no successful operations completed")
    if failed > int(rules.get("maximum_failures", 0)):
        reasons.append(f"load generator reported {failed} failures")
    if "maximum_timeouts" in rules and timed_out > int(rules["maximum_timeouts"]):
        reasons.append(f"load generator reported {timed_out} timeouts")

    expected = max(scheduled - skipped, 0)
    if scheduled:
        offered_ratio = expected / scheduled
        minimum_ratio = float(
            rules.get("minimum_offered_ratio", rules.get("minimum_completion_ratio", 0.995))
        )
        if offered_ratio < minimum_ratio:
            reasons.append(
                f"offered-load delivery ratio {offered_ratio:.4f} is below {minimum_ratio:.4f}"
            )

    if report.get("mode") == "export" and int(counters.get("bytes", 0)) == 0:
        reasons.append("export completed without receiving bytes")

    return ValidationResult(not reasons, reasons)


def validate_aws_collectors(
    raw_dir: Path, duration: int, claim_postgres_headroom: bool
) -> ValidationResult:
    reasons: list[str] = []
    minimum_samples = max(duration - 5, 1)
    host_files = sorted(raw_dir.glob("host-*.csv"))
    if len(host_files) < 4:
        reasons.append("host collectors are missing for one or more AWS roles")

    for path in host_files:
        with path.open(newline="") as file:
            rows = list(csv.DictReader(file))
        role = path.stem.removeprefix("host-")
        if len(rows) < minimum_samples:
            reasons.append(
                f"{role} host collector has {len(rows)} samples; expected at least {minimum_samples}"
            )
        if len(rows) < 2:
            continue
        timestamps = [int(row["timestamp_unix"]) for row in rows]
        if max(later - earlier for earlier, later in zip(timestamps, timestamps[1:], strict=False)) > 6:
            reasons.append(f"{role} host collector missed more than five consecutive samples")
        first, last = rows[0], rows[-1]
        fields = [
            "cpu_user",
            "cpu_nice",
            "cpu_system",
            "cpu_idle",
            "cpu_iowait",
            "cpu_irq",
            "cpu_softirq",
            "cpu_steal",
        ]
        deltas = {field: max(int(last[field]) - int(first[field]), 0) for field in fields}
        total = sum(deltas.values())
        if not total:
            continue
        steal = deltas["cpu_steal"] / total
        busy = 1 - (deltas["cpu_idle"] + deltas["cpu_iowait"]) / total
        if steal > 0.01:
            reasons.append(f"{role} average CPU steal {steal:.2%} exceeds 1%")
        if role in {"api", "export"} and busy > 0.75:
            reasons.append(f"{role} load-generator host CPU {busy:.2%} exceeds 75%")
        if role == "postgres" and claim_postgres_headroom and busy > 0.80:
            reasons.append(f"PostgreSQL host CPU {busy:.2%} exceeds 80% headroom limit")

    for path in sorted(raw_dir.glob("clock-*.txt")):
        contents = path.read_text()
        role = path.stem.removeprefix("clock-")
        if "NTPSynchronized=no" in contents:
            reasons.append(f"{role} clock is not synchronized")
        match = re.search(r"Last offset\s*:\s*([+-]?[0-9.eE-]+) seconds", contents)
        if match and abs(float(match.group(1))) * 1000 > 2:
            reasons.append(f"{role} clock offset exceeds 2 ms")

    for name in ("process-pgbouncer.jsonl", "process-api-loadgen.jsonl", "process-export-loadgen.jsonl", "postgres.jsonl"):
        path = raw_dir / name
        samples = len(path.read_text().splitlines()) if path.exists() else 0
        if samples < minimum_samples:
            reasons.append(f"{name} has {samples} samples; expected at least {minimum_samples}")
    pgbouncer_path = raw_dir / "pgbouncer.log"
    if not pgbouncer_path.exists() or not pgbouncer_path.read_text().strip():
        reasons.append("per-process PgBouncer admin metrics are missing")

    return ValidationResult(not reasons, reasons)
