#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from pandas.errors import EmptyDataError


def scaling_plot(frame: pd.DataFrame, output: Path) -> None:
    api = frame[frame["workload"] == "api"]
    if api.empty:
        return
    grouped = api.groupby("processes", as_index=False)["completed_per_second"].median()
    figure, axis = plt.subplots(figsize=(7, 4.5))
    axis.plot(grouped["processes"], grouped["completed_per_second"], marker="o")
    axis.set(xlabel="PgBouncer processes", ylabel="Completed operations/s", title="PgBouncer scaling")
    axis.set_xticks(sorted(grouped["processes"].unique()))
    axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(output / "scaling-throughput.png", dpi=180)
    plt.close(figure)


def latency_plot(frame: pd.DataFrame, output: Path) -> None:
    api = frame.dropna(subset=["latency_p99_us"])
    if api.empty:
        return
    grouped = api.groupby("processes", as_index=False)["latency_p99_us"].median()
    figure, axis = plt.subplots(figsize=(7, 4.5))
    axis.plot(grouped["processes"], grouped["latency_p99_us"] / 1000, marker="o", color="#b33b2e")
    axis.set(xlabel="PgBouncer processes", ylabel="API p99 (ms)", title="Tail latency at fixed offered load")
    axis.set_xticks(sorted(grouped["processes"].unique()))
    axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(output / "latency-p99.png", dpi=180)
    plt.close(figure)


def isolation_plot(frame: pd.DataFrame, output: Path) -> None:
    mixed = frame[(frame["workload"] == "mixed") & frame["latency_p99_us"].notna()]
    if mixed.empty or mixed["topology"].nunique() < 2:
        return
    grouped = mixed.groupby("topology", as_index=False)["latency_p99_us"].median()
    figure, axis = plt.subplots(figsize=(6, 4.5))
    axis.bar(grouped["topology"], grouped["latency_p99_us"] / 1000, color=["#59636e", "#2e7d61"])
    axis.set(ylabel="API p99 (ms)", title="Shared versus isolated workloads")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    figure.savefig(output / "shared-versus-isolated.png", dpi=180)
    plt.close(figure)


def tls_plot(frame: pd.DataFrame, output: Path) -> None:
    tls = frame[(frame["workload"] == "api") & frame["tls_mode"].notna()].copy()
    if tls.empty or tls["tls_mode"].nunique() < 2:
        return
    tls["configuration"] = tls["client_tls_sslmode"].astype(str) + "/" + tls["server_tls_sslmode"].astype(str)
    grouped = tls.groupby("configuration", as_index=False)["completed_per_second"].median()
    figure, axis = plt.subplots(figsize=(7, 4.5))
    axis.bar(grouped["configuration"], grouped["completed_per_second"], color="#4d6f8c")
    axis.set(xlabel="Client/server TLS", ylabel="Completed operations/s", title="TLS attribution")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    figure.savefig(output / "tls-attribution.png", dpi=180)
    plt.close(figure)


def cancellation_plot(frame: pd.DataFrame, output: Path) -> None:
    cancellation = frame[frame["workload"] == "cancel"].copy()
    if cancellation.empty:
        return
    attempts = cancellation["cancellations_succeeded"] + cancellation["cancellations_failed"]
    cancellation["success_percent"] = cancellation["cancellations_succeeded"] / attempts * 100
    cancellation["configuration"] = cancellation["processes"].astype(str) + " processes, peering=" + cancellation["peering_enabled"].astype(str)
    grouped = cancellation.groupby("configuration", as_index=False)["success_percent"].median()
    figure, axis = plt.subplots(figsize=(6, 4.5))
    axis.bar(grouped["configuration"], grouped["success_percent"], color="#6f5a8c")
    axis.set(xlabel="Configuration", ylabel="Cancellation success (%)", title="Cancellation reliability")
    axis.set_ylim(0, 100)
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    figure.savefig(output / "cancellation-success.png", dpi=180)
    plt.close(figure)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs_csv", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        frame = pd.read_csv(args.runs_csv)
    except EmptyDataError:
        return 0
    scaling_plot(frame, args.output)
    latency_plot(frame, args.output)
    isolation_plot(frame, args.output)
    tls_plot(frame, args.output)
    cancellation_plot(frame, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
