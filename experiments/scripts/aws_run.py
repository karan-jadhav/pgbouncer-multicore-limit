#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TERRAFORM_DIR = ROOT / "infra" / "terraform"
TFVARS = TERRAFORM_DIR / "terraform.tfvars"
RESULTS = ROOT / "results"
LOCAL = ROOT / ".local"
PLAN = LOCAL / "aws-workers.tfplan"
ARCHIVE = LOCAL / "aws-results.tar.gz"
STATUS = RESULTS / "aws-run-status.json"

MATRICES = [
    ROOT / "experiments" / "matrices" / "focused-baseline.yaml",
    ROOT / "experiments" / "matrices" / "focused-main.yaml",
    ROOT / "experiments" / "matrices" / "focused-secondary.yaml",
]


def stop_on_signal(signum: int, _frame: object) -> None:
    raise KeyboardInterrupt(f"received signal {signum}")


def load_dotenv(environment: dict[str, str]) -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        environment.setdefault(key, value)


def command_text(command: list[str]) -> str:
    return " ".join(command)


def run(command: list[str], environment: dict[str, str], *, output: Path | None = None) -> None:
    print(f"+ {command_text(command)}", flush=True)
    if output is None:
        subprocess.run(command, cwd=ROOT, env=environment, check=True)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w") as stream:
        subprocess.run(command, cwd=ROOT, env=environment, stdout=stream, check=True)


def terraform(*arguments: str) -> list[str]:
    return ["terraform", f"-chdir={TERRAFORM_DIR.relative_to(ROOT)}", *arguments]


def refresh_expiry() -> None:
    if not TFVARS.is_file():
        raise RuntimeError(f"missing {TFVARS.relative_to(ROOT)}")
    contents = TFVARS.read_text()
    runtime = re.search(r"^max_runtime_hours\s*=\s*(\d+)\s*$", contents, re.MULTILINE)
    expiry = re.search(r'^expires_at\s*=\s*"[^"]*"\s*$', contents, re.MULTILINE)
    if runtime is None or expiry is None:
        raise RuntimeError("terraform.tfvars must define max_runtime_hours and expires_at")
    expires_at = datetime.now(UTC) + timedelta(hours=int(runtime.group(1)))
    replacement = f'expires_at        = "{expires_at.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
    TFVARS.write_text(contents[: expiry.start()] + replacement + contents[expiry.end() :])


def terraform_outputs(environment: dict[str, str]) -> dict[str, object]:
    completed = subprocess.run(
        terraform("output", "-json"),
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    return {name: item["value"] for name, item in json.loads(completed.stdout).items()}


def wait_for_ssh(outputs: dict[str, object], environment: dict[str, str]) -> None:
    hosts = [
        str(outputs["postgres_public_ip"]),
        str(outputs["pgbouncer_public_ip"]),
        *[str(host) for host in outputs["api_loadgen_public_ips"]],
        str(outputs["export_loadgen_public_ip"]),
    ]
    key = environment["SSH_PRIVATE_KEY"]
    deadline = time.monotonic() + 600
    pending = set(hosts)
    while pending and time.monotonic() < deadline:
        for host in list(pending):
            completed = subprocess.run(
                [
                    "ssh",
                    "-i",
                    key,
                    "-o",
                    "IdentitiesOnly=yes",
                    "-o",
                    "StrictHostKeyChecking=accept-new",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=5",
                    f"ubuntu@{host}",
                    "true",
                ],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if completed.returncode == 0:
                pending.remove(host)
        if pending:
            time.sleep(10)
    if pending:
        raise RuntimeError("SSH did not become ready on: " + ", ".join(sorted(pending)))


def export_hosts(outputs: dict[str, object], environment: dict[str, str]) -> None:
    environment.update(
        {
            "AWS_POSTGRES_HOST": str(outputs["postgres_public_ip"]),
            "AWS_PGBOUNCER_HOST": str(outputs["pgbouncer_public_ip"]),
            "AWS_API_LOADGEN_HOST": str(outputs["api_loadgen_public_ips"][0]),
            "AWS_EXPORT_LOADGEN_HOST": str(outputs["export_loadgen_public_ip"]),
            "AWS_POSTGRES_PRIVATE_IP": str(outputs["postgres_private_ip"]),
            "AWS_PGBOUNCER_PRIVATE_IP": str(outputs["pgbouncer_private_ip"]),
        }
    )


def validate_network_preflight(path: Path) -> None:
    report = json.loads(path.read_text())
    failed = [
        f"{pair['source']}->{pair['target']}"
        for pair in report.get("pairs", [])
        if int(pair.get("returncode", 1)) != 0
        or float(
            pair.get("iperf", {})
            .get("end", {})
            .get("sum_received", {})
            .get("bits_per_second", 0)
        )
        <= 0
    ]
    if len(report.get("pairs", [])) != 3 or failed:
        raise RuntimeError("network preflight failed: " + ", ".join(failed or ["missing pairs"]))


def prepare_results() -> None:
    LOCAL.mkdir(parents=True, exist_ok=True)
    if RESULTS.exists():
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        shutil.move(str(RESULTS), LOCAL / f"results-before-aws-run-{timestamp}")
    RESULTS.mkdir(parents=True)


def write_status(status: str, error: str | None, cleanup: str) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(
        json.dumps(
            {
                "status": status,
                "error": error,
                "worker_cleanup": cleanup,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def archive_results() -> None:
    if ARCHIVE.exists():
        ARCHIVE.unlink()
    with tarfile.open(ARCHIVE, "w:gz") as archive:
        archive.add(RESULTS, arcname="results")


def required_environment(environment: dict[str, str]) -> None:
    required = [
        "BENCH_PASSWORD",
        "BENCH_BACKEND_PASSWORD",
        "METRICS_PASSWORD",
        "PGBOUNCER_ADMIN_PASSWORD",
        "SSH_PRIVATE_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ]
    missing = [name for name in required if not environment.get(name, "").strip()]
    if missing:
        raise RuntimeError("missing environment variables: " + ", ".join(missing))
    key = Path(environment["SSH_PRIVATE_KEY"]).expanduser()
    if not key.is_file():
        raise RuntimeError(f"SSH private key not found: {key}")
    environment["SSH_PRIVATE_KEY"] = str(key.resolve())


def require_clean_repository(environment: dict[str, str]) -> None:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    if completed.stdout.strip():
        raise RuntimeError("repository must be clean before an AWS run")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the focused AWS experiment unattended")
    parser.add_argument(
        "--aws-profile",
        help="Override the AWS profile loaded from the environment or .env",
    )
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop_on_signal)

    environment = os.environ.copy()
    load_dotenv(environment)
    if args.aws_profile:
        environment["AWS_PROFILE"] = args.aws_profile

    sys_path = str(ROOT / "experiments" / "runner")
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from telegram import TelegramNotifier

    required_environment(environment)
    require_clean_repository(environment)
    notifier = TelegramNotifier.from_env(environment)
    if notifier is None or not notifier.send("AWS experiment starting"):
        raise RuntimeError("Telegram notification preflight failed")

    apply_attempted = False
    experiment_error: str | None = None
    cleanup = "not-needed"

    stages: list[tuple[str, list[str], Path | None]] = [
        ("AWS authentication", ["aws", "sts", "get-caller-identity"], None),
        ("Terraform initialization", terraform("init", "-input=false"), None),
        (
            "Terraform plan",
            terraform("plan", "-input=false", f"-out={PLAN}"),
            None,
        ),
    ]

    try:
        prepare_results()
        refresh_expiry()
        for name, command, output in stages:
            notifier.send(f"Step started: {name}")
            run(command, environment, output=output)
            notifier.send(f"Step completed: {name}")

        notifier.send("Step started: Create worker infrastructure")
        apply_attempted = True
        run(terraform("apply", "-input=false", str(PLAN)), environment)
        notifier.send("Step completed: Create worker infrastructure")

        outputs = terraform_outputs(environment)
        export_hosts(outputs, environment)

        notifier.send("Step started: Wait for worker SSH")
        wait_for_ssh(outputs, environment)
        notifier.send("Step completed: Wait for worker SSH")

        for name, command in [
            ("Configure workers", ["make", "configure"]),
            ("Generate benchmark dataset", ["make", "seed-data"]),
        ]:
            notifier.send(f"Step started: {name}")
            run(command, environment)
            notifier.send(f"Step completed: {name}")

        run(terraform("show", "-json"), environment, output=RESULTS / "infrastructure.json")

        network_output = RESULTS / "operations" / "network-preflight.json"
        notifier.send("Step started: Network preflight")
        run(
            [
                "uv",
                "run",
                "experiments/scripts/aws_operations.py",
                "--output",
                str(network_output),
                "network-preflight",
            ],
            environment,
        )
        validate_network_preflight(network_output)
        notifier.send("Step completed: Network preflight")

        for matrix in MATRICES:
            name = matrix.stem
            notifier.send(f"Step started: Matrix {name}")
            run(
                [
                    "uv",
                    "run",
                    "experiments/runner/runner.py",
                    "--matrix",
                    str(matrix),
                    "--environment",
                    "aws",
                ],
                environment,
            )
            notifier.send(f"Step completed: Matrix {name}")

        for name, command in [
            ("Validate results", ["make", "validate-results"]),
            ("Summarize results", ["make", "summarize"]),
            ("Generate plots", ["make", "plots"]),
            (
                "Generate result table",
                [
                    "uv",
                    "run",
                    "analysis/tables.py",
                    "results/summaries/runs.csv",
                    "results/summaries/table.md",
                ],
            ),
        ]:
            notifier.send(f"Step started: {name}")
            run(command, environment)
            notifier.send(f"Step completed: {name}")
    except (Exception, KeyboardInterrupt) as error:
        experiment_error = f"{type(error).__name__}: {error}"
        notifier.send(f"AWS experiment failed\n{experiment_error}")
    finally:
        if apply_attempted:
            notifier.send("Step started: Destroy worker infrastructure")
            try:
                run(terraform("destroy", "-auto-approve", "-input=false"), environment)
                cleanup = "completed"
                notifier.send("Step completed: Destroy worker infrastructure")
            except Exception as error:
                cleanup = f"failed: {type(error).__name__}"
                notifier.send("Worker infrastructure cleanup failed")

        final_status = (
            "completed"
            if experiment_error is None and cleanup in {"completed", "not-needed"}
            else "failed"
        )
        write_status(final_status, experiment_error, cleanup)
        archive_results()
        notifier.send(
            f"AWS experiment {final_status}\nWorker cleanup: {cleanup}\n"
            f"Results: {ARCHIVE}"
        )

    return 0 if experiment_error is None and not cleanup.startswith("failed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
