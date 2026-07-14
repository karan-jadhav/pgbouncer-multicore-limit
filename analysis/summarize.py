#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from normalize import normalize_results


METRICS = [
    "completed_per_second",
    "completion_ratio",
    "latency_p50_us",
    "latency_p95_us",
    "latency_p99_us",
    "export_mib_per_second",
    "pgbouncer_cpu_percent",
]


def summarize(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    groups = [
        "workload",
        "endpoint",
        "topology",
        "processes",
        "connections",
        "offered_rate",
        "tls_mode",
        "client_tls_sslmode",
        "server_tls_sslmode",
        "peering_enabled",
    ]
    available_metrics = [metric for metric in METRICS if metric in frame]
    summary = frame.groupby(groups, dropna=False)[available_metrics].agg(["median", "min", "max", "mean", "std"])
    summary.columns = ["_".join(column) for column in summary.columns]
    summary = summary.reset_index()
    for metric in available_metrics:
        mean = summary[f"{metric}_mean"]
        summary[f"{metric}_cv"] = summary[f"{metric}_std"] / mean
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    output = args.results / "summaries"
    output.mkdir(parents=True, exist_ok=True)
    runs = normalize_results(args.results)
    runs.to_csv(output / "runs.csv", index=False)
    summarize(runs).to_csv(output / "summary.csv", index=False)
    print(f"wrote {len(runs)} accepted runs to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
