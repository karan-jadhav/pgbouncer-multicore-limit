#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "runner"))

from remote import run_ssh, start_ssh  # noqa: E402


def required_hosts() -> dict[str, str]:
    variables = {
        "api": "AWS_API_LOADGEN_HOST",
        "export": "AWS_EXPORT_LOADGEN_HOST",
        "pgbouncer": "AWS_PGBOUNCER_HOST",
        "postgres": "AWS_POSTGRES_HOST",
        "pgbouncer_private": "AWS_PGBOUNCER_PRIVATE_IP",
        "postgres_private": "AWS_POSTGRES_PRIVATE_IP",
    }
    missing = [variable for variable in variables.values() if not os.environ.get(variable)]
    if missing:
        raise SystemExit("missing environment variables: " + ", ".join(missing))
    return {role: os.environ[variable] for role, variable in variables.items()}


def write_result(name: str, result: dict[str, object], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    result["operation"] = name
    result["captured_at"] = datetime.now(UTC).isoformat()
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


def network_preflight(hosts: dict[str, str], output: Path) -> None:
    pairs = [("api", "pgbouncer"), ("export", "pgbouncer"), ("pgbouncer", "postgres")]
    records = []
    for source, target in pairs:
        server = start_ssh(hosts[target], ["iperf3", "-s", "-1"])
        time.sleep(1)
        target_address = hosts[f"{target}_private"]
        result = run_ssh(
            hosts[source],
            ["iperf3", "-c", target_address, "-P", "8", "-t", "60", "-J"],
            check=False,
        )
        server.wait(timeout=10)
        records.append(
            {
                "source": source,
                "target": target,
                "returncode": result.returncode,
                "iperf": json.loads(result.stdout) if result.stdout else {"error": result.stderr},
            }
        )
    write_result("network-preflight", {"pairs": records}, output)


def process_failure(hosts: dict[str, str], instance: int, recovery_seconds: float, output: Path) -> None:
    unit = f"pgbouncer@{instance}.service"
    before = run_ssh(hosts["pgbouncer"], ["systemctl", "is-active", unit], check=False).stdout.strip()
    killed_at = datetime.now(UTC).isoformat()
    run_ssh(hosts["pgbouncer"], ["sudo", "systemctl", "kill", "--signal=SIGQUIT", unit])
    time.sleep(recovery_seconds)
    run_ssh(hosts["pgbouncer"], ["sudo", "systemctl", "start", unit])
    after = run_ssh(hosts["pgbouncer"], ["systemctl", "is-active", unit], check=False).stdout.strip()
    write_result(
        "process-failure",
        {"instance": instance, "state_before": before, "killed_at": killed_at, "state_after": after},
        output,
    )


def rolling_restart(hosts: dict[str, str], processes: int, output: Path) -> None:
    records = []
    for instance in range(1, processes + 1):
        unit = f"pgbouncer@{instance}.service"
        started = time.monotonic()
        result = run_ssh(hosts["pgbouncer"], ["sudo", "systemctl", "restart", unit], check=False)
        state = run_ssh(hosts["pgbouncer"], ["systemctl", "is-active", unit], check=False).stdout.strip()
        records.append(
            {
                "instance": instance,
                "returncode": result.returncode,
                "state": state,
                "elapsed_seconds": time.monotonic() - started,
            }
        )
    write_result("rolling-restart", {"instances": records}, output)


def client_counts(host: str, processes: int) -> dict[str, str]:
    password = os.environ["PGBOUNCER_ADMIN_PASSWORD"]
    counts = {}
    for instance in range(1, processes + 1):
        command = [
            "env",
            f"PGPASSWORD={password}",
            "psql",
            "-h",
            f"/run/pgbouncer/{instance}",
            "-p",
            "6432",
            "-U",
            "pgbouncer_admin",
            "-d",
            "pgbouncer",
            "-At",
            "-c",
            "SHOW LISTS;",
        ]
        counts[str(instance)] = run_ssh(host, command, check=False).stdout
    return counts


def dynamic_scale(hosts: dict[str, str], clients: int, hold_seconds: int, output: Path) -> None:
    for instance in range(2, 5):
        run_ssh(hosts["pgbouncer"], ["sudo", "systemctl", "stop", f"pgbouncer@{instance}.service"], check=False)
    password = quote(os.environ["BENCH_PASSWORD"], safe="")
    dsn = f"postgresql://bench_login:{password}@{hosts['pgbouncer_private']}:6432/bench"
    command = [
        "/opt/pgbouncer-experiment/bin/loadgen",
        "connections",
        "--dsn",
        dsn,
        "--mode",
        "hold",
        "--connections",
        str(clients),
        "--duration",
        str(hold_seconds),
        "--tls-mode",
        "verify-full",
        "--ca-cert",
        "/opt/pgbouncer-experiment/ca.crt",
        "--output",
        "/tmp/dynamic-scale-hold.json",
    ]
    load = start_ssh(hosts["api"], command)
    time.sleep(5)
    before = client_counts(hosts["pgbouncer"], 1)
    for instance in range(2, 5):
        run_ssh(hosts["pgbouncer"], ["sudo", "systemctl", "start", f"pgbouncer@{instance}.service"])
    after_add = client_counts(hosts["pgbouncer"], 4)
    load.wait(timeout=hold_seconds + 30)
    reconnect = start_ssh(hosts["api"], command)
    time.sleep(5)
    after_reconnect = client_counts(hosts["pgbouncer"], 4)
    reconnect.wait(timeout=hold_seconds + 30)
    write_result(
        "dynamic-scale",
        {"before": before, "after_add": after_add, "after_reconnect": after_reconnect},
        output,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "operations.json")
    subcommands = parser.add_subparsers(dest="operation", required=True)
    subcommands.add_parser("network-preflight")
    failure = subcommands.add_parser("process-failure")
    failure.add_argument("--instance", type=int, default=4)
    failure.add_argument("--recovery-seconds", type=float, default=5)
    rolling = subcommands.add_parser("rolling-restart")
    rolling.add_argument("--processes", type=int, default=4)
    dynamic = subcommands.add_parser("dynamic-scale")
    dynamic.add_argument("--clients", type=int, default=10000)
    dynamic.add_argument("--hold-seconds", type=int, default=30)
    args = parser.parse_args()
    hosts = required_hosts()
    if args.operation == "network-preflight":
        network_preflight(hosts, args.output)
    elif args.operation == "process-failure":
        process_failure(hosts, args.instance, args.recovery_seconds, args.output)
    elif args.operation == "rolling-restart":
        rolling_restart(hosts, args.processes, args.output)
    elif args.operation == "dynamic-scale":
        dynamic_scale(hosts, args.clients, args.hold_seconds, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
