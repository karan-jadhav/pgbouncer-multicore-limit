#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from pandas.errors import EmptyDataError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs_csv", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        frame = pd.read_csv(args.runs_csv)
    except EmptyDataError:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text("No accepted runs.\n")
        return 0
    columns = [
        "workload",
        "topology",
        "processes",
        "offered_rate",
        "completed_per_second",
        "latency_p99_us",
        "export_mib_per_second",
        "pgbouncer_cpu_percent",
        "total_pool_budget",
    ]
    available = [column for column in columns if column in frame]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(frame[available].to_markdown(index=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
