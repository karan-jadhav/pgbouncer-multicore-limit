#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import shlex
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from manifest import RunManifest, hash_tree, sha256_file
from remote import copy_from, run_ssh, start_ssh
from state import RunState
from telegram import TelegramNotifier
from validation import validate_aws_collectors, validate_loadgen

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results"

COMMON_CLI_ARGUMENTS = {
    "dsn",
    "duration",
    "warmup",
    "connections",
    "tls_mode",
    "ca_cert",
    "seed",
    "application_name",
    "start_at_unix_ms",
}
MODE_CLI_ARGUMENTS = {
    "api": {
        "model",
        "target_rate",
        "max_inflight",
        "id_range_start",
        "id_range_end",
        "prepared",
        "timeout_ms",
    },
    "export": {
        "concurrency",
        "tenant_range_start",
        "tenant_range_end",
        "consumer_rate_mbps",
        "result_limit_bytes",
        "timeout_seconds",
    },
    "connections": {"mode", "target_rate", "health_query"},
    "cancel": {"count", "query_seconds", "cancel_after_ms"},
    "mixed": {"api_target_rate", "export_concurrency", "consumer_rate_mbps", "export_dsn"},
    "validate": {"expected_rows"},
}
ALL_CLI_ARGUMENTS = COMMON_CLI_ARGUMENTS | set().union(*MODE_CLI_ARGUMENTS.values())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a PgBouncer experiment matrix")
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--environment", choices=("local", "aws"), default="local")
    parser.add_argument(
        "--loadgen",
        type=Path,
        default=ROOT / "loadgen" / "target" / "release" / "pgbouncer-loadgen",
    )
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_local_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)


def git_metadata() -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return (commit.stdout.strip() if commit.returncode == 0 else "uncommitted", bool(status.stdout))


def local_dsn(endpoint: str) -> str:
    password = quote(os.environ.get("BENCH_PASSWORD", "bench-password"), safe="")
    port = {"postgres": 5432, "pgbouncer": 6432, "export": 6433}[endpoint]
    return f"postgresql://bench_login:{password}@127.0.0.1:{port}/bench"


def aws_dsn(endpoint: str) -> str:
    variable = "POSTGRES_DSN" if endpoint == "postgres" else "PGBOUNCER_DSN"
    configured = os.environ.get(variable)
    if configured and endpoint != "export":
        return configured
    password = quote(os.environ["BENCH_PASSWORD"], safe="")
    if endpoint == "postgres":
        host = os.environ["AWS_POSTGRES_PRIVATE_IP"]
        port = 5432
    else:
        host = os.environ["AWS_PGBOUNCER_PRIVATE_IP"]
        port = 6433 if endpoint == "export" else 6432
    return f"postgresql://bench_login:{password}@{host}:{port}/bench"


def ensure_local_topology(run: dict[str, Any]) -> None:
    environment = os.environ.copy()
    environment["PGBOUNCER_TOPOLOGY"] = str(run.get("topology", "shared"))
    environment["PGBOUNCER_PROCESSES"] = str(run.get("pgbouncer_processes", 4))
    environment["TOTAL_POOL_BUDGET"] = str(run.get("total_pool_budget", 128))
    environment["PGBOUNCER_PEERING"] = "1" if run.get("peering_enabled", True) else "0"
    subprocess.run(
        ["docker", "compose", "up", "--detach", "--force-recreate", "pgbouncer"],
        cwd=ROOT,
        env=environment,
        check=True,
    )


def ensure_aws_topology(run: dict[str, Any]) -> None:
    if os.environ.get("AWS_SKIP_TOPOLOGY_CONFIG") == "1":
        return
    inventory = os.environ.get(
        "ANSIBLE_INVENTORY", str(ROOT / "infra" / "ansible" / "inventory" / "generated.yml")
    )
    environment = os.environ.copy()
    environment["ANSIBLE_CONFIG"] = str(ROOT / "infra" / "ansible" / "ansible.cfg")
    extra_vars = {
        "pgbouncer_process_count": int(run.get("pgbouncer_processes", 4)),
        "pgbouncer_topology": str(run.get("topology", "shared")),
        "total_pool_budget": int(run.get("total_pool_budget", 128)),
        "pgbouncer_client_tls_sslmode": str(run.get("client_tls_sslmode", "require")),
        "pgbouncer_server_tls_sslmode": str(run.get("server_tls_sslmode", "verify-full")),
        "pgbouncer_peering_enabled": bool(run.get("peering_enabled", True)),
        "sbuf_loopcnt": int(run.get("sbuf_loopcnt", 5)),
    }
    subprocess.run(
        [
            "uv",
            "run",
            "ansible-playbook",
            "-i",
            inventory,
            str(ROOT / "infra" / "ansible" / "playbooks" / "topology.yml"),
            "--extra-vars",
            json.dumps(extra_vars),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
    )


def snapshot_postgres(path: Path) -> None:
    with path.open("w") as output:
        subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "postgres",
                "-d",
                "bench",
                "-x",
                "-c",
                "SELECT * FROM pg_stat_database WHERE datname = current_database();",
                "-c",
                "SELECT * FROM pg_stat_io;",
                "-c",
                "SELECT * FROM pg_stat_bgwriter;",
                "-c",
                "SELECT * FROM pg_stat_wal;",
            ],
            cwd=ROOT,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )


def snapshot_postgres_aws(path: Path) -> None:
    result = run_ssh(
        os.environ["AWS_POSTGRES_HOST"],
        [
            "sudo",
            "-u",
            "postgres",
            "psql",
            "-d",
            "bench",
            "-x",
            "-c",
            "SELECT * FROM pg_stat_database WHERE datname = current_database();",
            "-c",
            "SELECT * FROM pg_stat_io;",
            "-c",
            "SELECT * FROM pg_stat_bgwriter;",
            "-c",
            "SELECT * FROM pg_stat_wal;",
        ],
        check=False,
    )
    path.write_text(result.stdout + result.stderr)


def capture_environment(environment: str, processes: int, raw_dir: Path) -> dict[str, Any]:
    capture: dict[str, Any] = {"cpu_affinity": {}, "configuration_sha256": {}}
    if environment == "local":
        version = subprocess.run(
            ["docker", "compose", "exec", "-T", "pgbouncer", "/opt/pgbouncer/bin/pgbouncer", "-V"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        postgres = subprocess.run(
            ["docker", "compose", "exec", "-T", "postgres", "postgres", "--version"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        capture["pgbouncer_version"] = version.stdout.strip()
        capture["postgres_version"] = postgres.stdout.strip()
        for instance in range(1, processes + 1):
            checksum = subprocess.run(
                [
                    "docker", "compose", "exec", "-T", "pgbouncer", "sha256sum",
                    f"/run/pgbouncer/config/pgbouncer-{instance}.ini",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            if checksum.stdout:
                capture["configuration_sha256"][str(instance)] = checksum.stdout.split()[0]
    else:
        pool_host = os.environ["AWS_PGBOUNCER_HOST"]
        postgres_host = os.environ["AWS_POSTGRES_HOST"]
        capture["pgbouncer_version"] = run_ssh(
            pool_host, ["/opt/pgbouncer/current/bin/pgbouncer", "-V"], check=False
        ).stdout.strip()
        capture["postgres_version"] = run_ssh(
            postgres_host,
            ["sudo", "-u", "postgres", "psql", "-At", "-c", "SHOW server_version;"],
            check=False,
        ).stdout.strip()
        capture["kernel_version"] = run_ssh(
            pool_host, ["uname", "-r"], check=False
        ).stdout.strip()
        capture["cpu_topology"] = run_ssh(
            pool_host, ["lscpu", "-e=CPU,CORE,SOCKET,NODE,ONLINE"], check=False
        ).stdout
        for instance in range(1, processes + 1):
            affinity = run_ssh(
                pool_host,
                ["systemctl", "show", f"pgbouncer@{instance}.service", "--property=CPUAffinity", "--value"],
                check=False,
            ).stdout.strip()
            checksum = run_ssh(
                pool_host,
                ["sudo", "sha256sum", f"/etc/pgbouncer/pgbouncer-{instance}.ini"],
                check=False,
            ).stdout
            capture["cpu_affinity"][str(instance)] = affinity
            if checksum:
                capture["configuration_sha256"][str(instance)] = checksum.split()[0]
    (raw_dir / "environment.json").write_text(json.dumps(capture, indent=2, sort_keys=True) + "\n")
    return capture


def start_collectors(raw_dir: Path, processes: int, environment: str) -> list[subprocess.Popen[Any]]:
    if environment != "local":
        return []
    commands = [
        [str(ROOT / "monitoring" / "collectors" / "host.sh"), str(raw_dir / "host.csv")],
        [
            sys.executable,
            str(ROOT / "monitoring" / "collectors" / "process.py"),
            "--output",
            str(raw_dir / "process.jsonl"),
            "--match",
            "pgbouncer",
        ],
        [
            sys.executable,
            str(ROOT / "monitoring" / "collectors" / "pgbouncer.py"),
            "--output",
            str(raw_dir / "pgbouncer.jsonl"),
            "--processes",
            str(processes),
        ],
        [
            sys.executable,
            str(ROOT / "monitoring" / "collectors" / "postgres.py"),
            "--output",
            str(raw_dir / "postgres.jsonl"),
        ],
    ]
    return [subprocess.Popen(command, cwd=ROOT) for command in commands]


def stop_collectors(collectors: list[subprocess.Popen[Any]]) -> bool:
    healthy = True
    for collector in collectors:
        if collector.poll() is None:
            collector.send_signal(signal.SIGTERM)
    for collector in collectors:
        try:
            collector.wait(timeout=5)
        except subprocess.TimeoutExpired:
            collector.kill()
            collector.wait()
            healthy = False
        if collector.returncode not in (0, -signal.SIGTERM):
            healthy = False
    return healthy


def start_aws_collectors(raw_dir: Path, run_id: str, processes: int) -> list[dict[str, Any]]:
    hosts = {
        "api": os.environ["AWS_API_LOADGEN_HOST"],
        "export": os.environ["AWS_EXPORT_LOADGEN_HOST"],
        "pgbouncer": os.environ["AWS_PGBOUNCER_HOST"],
        "postgres": os.environ["AWS_POSTGRES_HOST"],
    }
    collectors: list[dict[str, Any]] = []

    def start(role: str, name: str, command: list[str], extension: str) -> None:
        host = hosts[role]
        remote_path = f"/tmp/{run_id}-{name}.{extension}"
        expanded = [remote_path if value == "{output}" else value for value in command]
        shell = "nohup " + " ".join(shlex.quote(value) for value in expanded)
        shell += f" >/tmp/{run_id}-{name}.log 2>&1 & echo $!"
        result = run_ssh(host, ["sh", "-c", shell])
        collectors.append(
            {
                "host": host,
                "pid": int(result.stdout.strip()),
                "remote": remote_path,
                "local": raw_dir / f"{name}.{extension}",
            }
        )

    for role in hosts:
        start(role, f"host-{role}", ["/opt/pgbouncer-experiment/collectors/host.sh", "{output}"], "csv")
        clock = run_ssh(hosts[role], ["/opt/pgbouncer-experiment/collectors/clock.sh"], check=False)
        (raw_dir / f"clock-{role}.txt").write_text(clock.stdout + clock.stderr)
    start(
        "pgbouncer",
        "process-pgbouncer",
        ["python3", "/opt/pgbouncer-experiment/collectors/process.py", "--output", "{output}", "--match", "pgbouncer"],
        "jsonl",
    )
    start(
        "api",
        "process-api-loadgen",
        ["python3", "/opt/pgbouncer-experiment/collectors/process.py", "--output", "{output}", "--match", "loadgen"],
        "jsonl",
    )
    start(
        "export",
        "process-export-loadgen",
        ["python3", "/opt/pgbouncer-experiment/collectors/process.py", "--output", "{output}", "--match", "loadgen"],
        "jsonl",
    )
    start(
        "pgbouncer",
        "pgbouncer",
        [
            "env",
            f"PGPASSWORD={os.environ['PGBOUNCER_ADMIN_PASSWORD']}",
            "/opt/pgbouncer-experiment/collectors/pgbouncer.sh",
            "{output}",
            str(processes),
        ],
        "log",
    )
    start(
        "postgres",
        "postgres",
        [
            "env",
            f"PGPASSWORD={os.environ['METRICS_PASSWORD']}",
            "/opt/pgbouncer-experiment/collectors/postgres.sh",
            "{output}",
        ],
        "jsonl",
    )
    return collectors


def stop_aws_collectors(collectors: list[dict[str, Any]]) -> bool:
    healthy = True
    for collector in collectors:
        result = run_ssh(
            collector["host"], ["kill", "-TERM", str(collector["pid"])], check=False
        )
        healthy = healthy and result.returncode == 0
    time.sleep(1.2)
    for collector in collectors:
        try:
            copy_from(collector["host"], collector["remote"], collector["local"])
        except subprocess.CalledProcessError:
            healthy = False
    return healthy


def cli_args(run: dict[str, Any], defaults: dict[str, Any], output: Path, run_id: str, environment: str) -> list[str]:
    mode = str(run["mode"])
    if mode not in MODE_CLI_ARGUMENTS:
        raise ValueError(f"unsupported load-generator mode: {mode}")
    unknown_defaults = sorted(set(defaults) - ALL_CLI_ARGUMENTS)
    if unknown_defaults:
        raise ValueError(f"unsupported matrix defaults: {', '.join(unknown_defaults)}")
    allowed = COMMON_CLI_ARGUMENTS | MODE_CLI_ARGUMENTS[mode]
    run_arguments = run.get("args", {})
    unknown = sorted(set(run_arguments) - allowed)
    if unknown:
        raise ValueError(f"unsupported {mode} arguments: {', '.join(unknown)}")
    merged = {key: value for key, value in defaults.items() if key in allowed}
    merged.update(run_arguments)
    endpoint = str(run.get("endpoint", "pgbouncer"))
    dsn = merged.pop("dsn", None)
    if dsn is None:
        if environment == "local":
            dsn = local_dsn(endpoint)
        else:
            dsn = aws_dsn(endpoint)
    if environment == "aws" and merged.get("tls_mode") == "verify-full" and "ca_cert" not in merged:
        merged["ca_cert"] = "/opt/pgbouncer-experiment/ca.crt"
    command = [mode, "--dsn", os.path.expandvars(str(dsn)), "--output", str(output), "--run-id", run_id]
    if mode == "mixed" and run.get("topology") == "isolated" and "export_dsn" not in merged:
        if environment == "local":
            merged["export_dsn"] = local_dsn("export")
        else:
            merged["export_dsn"] = os.environ.get("EXPORT_PGBOUNCER_DSN", aws_dsn("export"))
    for key, value in merged.items():
        if value is None:
            continue
        option = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            command.extend([option, str(value).lower()])
        else:
            command.extend([option, str(value)])
    return command


def merge_mixed_reports(api_path: Path, export_path: Path, output: Path) -> None:
    api = json.loads(api_path.read_text())
    export = json.loads(export_path.read_text())
    api["mode"] = "mixed"
    for key, value in export.get("counters", {}).items():
        api["counters"][key] = int(api["counters"].get(key, 0)) + int(value)
    for name, histogram in export.get("histograms", {}).items():
        api["histograms"][f"export_{name}"] = histogram
    api["exports"] = export.get("exports", [])
    for target, source in zip(api.get("per_second", []), export.get("per_second", []), strict=False):
        for key in ("offered", "completed", "failed", "bytes"):
            target[key] = int(target.get(key, 0)) + int(source.get(key, 0))
    api["ended_at_unix_ms"] = max(api["ended_at_unix_ms"], export["ended_at_unix_ms"])
    output.write_text(json.dumps(api, indent=2, sort_keys=True) + "\n")


def run_aws_loadgen(
    run: dict[str, Any], defaults: dict[str, Any], output: Path, log_path: Path, run_id: str
) -> int:
    remote_binary = "/opt/pgbouncer-experiment/bin/loadgen"
    warmup_seconds = int(run.get("args", {}).get("warmup", defaults.get("warmup", 0)))
    synchronized_start_ms = int(time.time() * 1000) + (warmup_seconds + 10) * 1000
    if run["mode"] != "mixed":
        host = (
            os.environ["AWS_EXPORT_LOADGEN_HOST"]
            if run["mode"] == "export"
            else os.environ["AWS_API_LOADGEN_HOST"]
        )
        remote_output = f"/tmp/{run_id}-loadgen.json"
        synchronized_run = {**run, "args": {**run.get("args", {}), "start_at_unix_ms": synchronized_start_ms}}
        command = [remote_binary, *cli_args(synchronized_run, defaults, Path(remote_output), run_id, "aws")]
        process = start_ssh(host, command)
        stdout, _ = process.communicate()
        log_path.write_text(stdout or "")
        if process.returncode == 0:
            copy_from(host, remote_output, output)
        return int(process.returncode or 0)

    common_args = dict(run.get("args", {}))
    api_run = {
        **run,
        "mode": "api",
        "endpoint": "pgbouncer",
        "args": {
            "model": "open-loop",
            "target_rate": common_args.get("api_target_rate", 1000),
            "connections": common_args.get("connections", defaults.get("connections", 256)),
            "max_inflight": common_args.get("max_inflight", 4096),
            "id_range_end": common_args.get("id_range_end", 20000000),
            "start_at_unix_ms": synchronized_start_ms,
        },
    }
    export_run = {
        **run,
        "mode": "export",
        "endpoint": "export" if run.get("topology") == "isolated" else "pgbouncer",
        "args": {
            "concurrency": common_args.get("export_concurrency", 4),
            "consumer_rate_mbps": common_args.get("consumer_rate_mbps", 0),
            "start_at_unix_ms": synchronized_start_ms,
        },
    }
    api_remote = f"/tmp/{run_id}-api.json"
    export_remote = f"/tmp/{run_id}-export.json"
    api_host = os.environ["AWS_API_LOADGEN_HOST"]
    export_host = os.environ["AWS_EXPORT_LOADGEN_HOST"]
    api_process = start_ssh(
        api_host,
        [remote_binary, *cli_args(api_run, defaults, Path(api_remote), f"{run_id}-api", "aws")],
    )
    export_process = start_ssh(
        export_host,
        [remote_binary, *cli_args(export_run, defaults, Path(export_remote), f"{run_id}-export", "aws")],
    )
    api_log, _ = api_process.communicate()
    export_log, _ = export_process.communicate()
    log_path.write_text((api_log or "") + (export_log or ""))
    if api_process.returncode or export_process.returncode:
        return int(api_process.returncode or export_process.returncode or 1)
    api_local = output.with_name("loadgen-api.json")
    export_local = output.with_name("loadgen-export.json")
    copy_from(api_host, api_remote, api_local)
    copy_from(export_host, export_remote, export_local)
    merge_mixed_reports(api_local, export_local, output)
    return 0


def run_one(
    matrix: dict[str, Any],
    run: dict[str, Any],
    repeat: int,
    args: argparse.Namespace,
    notifier: TelegramNotifier | None = None,
) -> tuple[str, bool]:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    run_id = f"{timestamp}-{run['id']}-r{repeat}"
    running_dir = args.results / ".running" / run_id
    raw_dir = running_dir / "raw"
    raw_dir.mkdir(parents=True)
    commit, dirty = git_metadata()
    processes = int(run.get("pgbouncer_processes", 4))
    total_budget = int(run.get("total_pool_budget", 128))
    topology = str(run.get("topology", "shared"))
    mode = str(run["mode"])
    defaults = matrix.get("defaults", {})
    resolved_arguments = {**defaults, **run.get("args", {})}
    started_at = datetime.now(UTC).isoformat()
    manifest = RunManifest(
        run_id=run_id,
        status=RunState.CREATED,
        rejection_reason=None,
        started_at=started_at,
        ended_at=None,
        git_commit=commit,
        dirty_tree=dirty,
        environment=args.environment,
        matrix=str(args.matrix),
        workload=mode,
        topology=topology,
        pgbouncer_processes=processes,
        pool_size_per_process=total_budget // processes,
        total_pool_budget=total_budget,
        tls_mode=str(run.get("args", {}).get("tls_mode", defaults.get("tls_mode", "disable"))),
        offered_rate=run.get("args", {}).get("target_rate"),
        warmup_seconds=int(run.get("args", {}).get("warmup", defaults.get("warmup", 2))),
        measure_seconds=int(run.get("args", {}).get("duration", defaults.get("duration", 10))),
        repeat_number=repeat,
        random_seed=int(run.get("args", {}).get("seed", defaults.get("seed", 1))),
        metadata={
            "state": RunState.CREATED,
            "run_definition": run,
            "resolved_arguments": resolved_arguments,
        },
    )
    manifest.write(running_dir / "manifest.json")

    if notifier is not None and not args.dry_run:
        notifier.send(
            f"Run started\nMatrix: {matrix['name']}\nRun: {run['id']}\n"
            f"Repeat: {repeat}\nMode: {mode}"
        )

    if args.dry_run:
        print(" ".join([str(args.loadgen), *cli_args(run, defaults, raw_dir / "loadgen.json", run_id, args.environment)]))
        shutil.rmtree(running_dir)
        return run_id, True

    collectors: list[subprocess.Popen[Any]] = []
    aws_collectors: list[dict[str, Any]] = []
    accepted = False
    reasons: list[str] = []
    try:
        manifest.status = RunState.PREFLIGHT
        if run.get("endpoint", "pgbouncer") != "postgres":
            ensure_local_topology(run) if args.environment == "local" else ensure_aws_topology(run)
        manifest.metadata["environment"] = capture_environment(args.environment, processes, raw_dir)
        if args.environment == "local":
            snapshot_postgres(raw_dir / "postgres-start.txt")
            (raw_dir / "clock.txt").write_text(
                subprocess.run(
                    [str(ROOT / "monitoring" / "collectors" / "clock.sh")],
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout
            )
            collectors = start_collectors(raw_dir, processes, args.environment)
        else:
            snapshot_postgres_aws(raw_dir / "postgres-start.txt")
            aws_collectors = start_aws_collectors(raw_dir, run_id, processes)
        manifest.status = RunState.MEASURING
        loadgen_output = raw_dir / "loadgen.json"
        if args.environment == "local":
            command = [str(args.loadgen), *cli_args(run, defaults, loadgen_output, run_id, args.environment)]
            with (raw_dir / "loadgen.log").open("w") as log:
                completed = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            returncode = completed.returncode
        else:
            returncode = run_aws_loadgen(
                run, defaults, loadgen_output, raw_dir / "loadgen.log", run_id
            )
        if returncode != 0:
            reasons.append(f"load generator exited with status {returncode}")
        manifest.status = RunState.COLLECTING
        if args.environment == "local":
            manifest.collector_status = "ok" if stop_collectors(collectors) else "failed"
            collectors = []
            snapshot_postgres(raw_dir / "postgres-end.txt")
        else:
            manifest.collector_status = "ok" if stop_aws_collectors(aws_collectors) else "failed"
            aws_collectors = []
            snapshot_postgres_aws(raw_dir / "postgres-end.txt")
        manifest.status = RunState.VALIDATING
        if loadgen_output.exists():
            result = validate_loadgen(loadgen_output, run.get("validation", matrix.get("validation")))
            reasons.extend(result.reasons)
        else:
            reasons.append("load generator JSON output is missing")
        if manifest.collector_status != "ok":
            reasons.append("one or more collectors failed")
        if args.environment == "aws":
            quality = validate_aws_collectors(
                raw_dir,
                manifest.measure_seconds,
                bool(run.get("claim_postgres_headroom", run.get("endpoint", "pgbouncer") != "postgres")),
            )
            reasons.extend(quality.reasons)
            if dirty and not matrix.get("allow_dirty", False):
                reasons.append("repository is dirty and the matrix does not allow dirty AWS runs")
        accepted = not reasons
    except Exception as error:  # preserve artifacts for diagnosis
        reasons.append(str(error))
    finally:
        if collectors:
            stop_collectors(collectors)
        if aws_collectors:
            stop_aws_collectors(aws_collectors)

    manifest.status = RunState.ACCEPTED if accepted else RunState.REJECTED
    manifest.validation_status = "accepted" if accepted else "rejected"
    manifest.rejection_reason = "; ".join(reasons) if reasons else None
    manifest.ended_at = datetime.now(UTC).isoformat()
    if args.environment == "local":
        manifest.loadgen_sha256 = sha256_file(args.loadgen)
    else:
        binary_hash = run_ssh(
            os.environ["AWS_API_LOADGEN_HOST"],
            ["sha256sum", "/opt/pgbouncer-experiment/bin/loadgen"],
            check=False,
        )
        manifest.loadgen_sha256 = binary_hash.stdout.split()[0] if binary_hash.stdout else None
    manifest.raw_file_sha256 = hash_tree(raw_dir)
    manifest.metadata["state"] = manifest.status
    manifest.write(running_dir / "manifest.json")

    destination = args.results / ("accepted" if accepted else "rejected") / run_id
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(running_dir), destination)
    print(f"{manifest.status}: {run_id}" + (f" ({manifest.rejection_reason})" if reasons else ""))
    if notifier is not None:
        message = (
            f"Run {manifest.status}\nMatrix: {matrix['name']}\nRun: {run['id']}\n"
            f"Repeat: {repeat}"
        )
        if reasons:
            message += f"\nReason: {manifest.rejection_reason[:3000]}"
        notifier.send(message)
    return run_id, accepted


def main() -> int:
    args = parse_args()
    load_local_env()
    notifier = TelegramNotifier.from_env()
    matrix = yaml.safe_load(args.matrix.read_text())
    if not args.dry_run and args.environment == "local" and not args.loadgen.is_file():
        raise SystemExit(f"load generator not found: {args.loadgen}; run make build-loadgen")
    repeats = int(matrix.get("repeats", 1))
    seed = int(matrix.get("randomization_seed", 1))
    outcomes = []
    for repeat in range(1, repeats + 1):
        runs = list(matrix["runs"])
        if matrix.get("randomize", False):
            random.Random(seed + repeat).shuffle(runs)
        for run in runs:
            outcomes.append(run_one(matrix, run, repeat, args, notifier))
            time.sleep(float(matrix.get("cooldown_seconds", 0)))
    return 0 if all(accepted for _, accepted in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
