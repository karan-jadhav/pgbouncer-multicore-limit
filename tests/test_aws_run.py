from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "runner"))
sys.path.insert(0, str(ROOT / "analysis"))

from normalize import normalize_run  # noqa: E402
from remote import COPY_ATTEMPTS, copy_from  # noqa: E402
from runner import cli_args, start_aws_collectors  # noqa: E402
from telegram import TelegramNotifier  # noqa: E402
from validation import validate_aws_collectors  # noqa: E402


def load_aws_run_module():
    path = ROOT / "experiments" / "scripts" / "aws_run.py"
    spec = importlib.util.spec_from_file_location("aws_run", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AwsRunTests(unittest.TestCase):
    def test_collector_copy_retries_transient_ssh_failure(self) -> None:
        failure = subprocess.CalledProcessError(255, ["scp"])
        completed = subprocess.CompletedProcess(["scp"], 0)
        with (
            patch("remote.private_key", return_value="/tmp/test-key"),
            patch("remote.subprocess.run", side_effect=[failure, completed]) as run,
            patch("remote.time.sleep") as sleep,
        ):
            copy_from("api.example", "/tmp/host-api.csv", Path("host-api.csv"))

        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once()

    def test_collector_copy_stops_after_bounded_retries(self) -> None:
        failure = subprocess.CalledProcessError(255, ["scp"])
        with (
            patch("remote.private_key", return_value="/tmp/test-key"),
            patch("remote.subprocess.run", side_effect=failure) as run,
            patch("remote.time.sleep") as sleep,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            copy_from("api.example", "/tmp/host-api.csv", Path("host-api.csv"))

        self.assertEqual(run.call_count, COPY_ATTEMPTS)
        self.assertEqual(sleep.call_count, COPY_ATTEMPTS - 1)

    def test_cleanup_ignores_broken_notification_pipe(self) -> None:
        aws_run = load_aws_run_module()
        notifier = MagicMock()
        notifier.send.side_effect = BrokenPipeError
        with patch.object(aws_run, "destroy_infrastructure") as destroy:
            cleanup = aws_run.cleanup_infrastructure({}, notifier)

        self.assertEqual(cleanup, "completed")
        destroy.assert_called_once_with({})

    def test_destroy_redirects_output_to_cleanup_log(self) -> None:
        aws_run = load_aws_run_module()
        with tempfile.TemporaryDirectory() as temporary:
            cleanup_log = Path(temporary) / "cleanup.log"
            with (
                patch.object(aws_run, "CLEANUP_LOG", cleanup_log),
                patch.object(aws_run.subprocess, "run") as run,
            ):
                aws_run.destroy_infrastructure({"AWS_PROFILE": "test"})

        self.assertIsNotNone(run.call_args.kwargs["stdout"])
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.STDOUT)

    def test_aws_pgbouncer_collectors_run_as_service_user(self) -> None:
        environment = {
            "AWS_API_LOADGEN_HOST": "api.example",
            "AWS_EXPORT_LOADGEN_HOST": "export.example",
            "AWS_PGBOUNCER_HOST": "pgbouncer.example",
            "AWS_POSTGRES_HOST": "postgres.example",
            "PGBOUNCER_ADMIN_PASSWORD": "admin-password",
            "METRICS_PASSWORD": "metrics-password",
        }
        completed = MagicMock(returncode=0, stdout="123\n", stderr="")
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.dict("os.environ", environment, clear=True),
                patch("runner.run_ssh", return_value=completed) as run_ssh,
                patch("runner.time.sleep"),
            ):
                collectors = start_aws_collectors(Path(temporary), "test-run", 4)

        commands = [call.args[1] for call in run_ssh.call_args_list]
        service_user_commands = [
            command
            for command in commands
            if command[:3] == ["sudo", "-u", "pgbouncer"]
        ]
        self.assertEqual(len(service_user_commands), 2)
        self.assertTrue(
            any(
                "/opt/pgbouncer/current/bin/pgbouncer" in " ".join(command)
                for command in service_user_commands
            )
        )
        self.assertEqual(
            len([collector for collector in collectors if collector["run_as"] == "pgbouncer"]),
            2,
        )
        self.assertTrue(any(command[0] == "awk" for command in commands))
        self.assertTrue(any(command[0] == "jq" for command in commands))

    def test_mode_specific_defaults_are_not_forwarded_to_other_commands(self) -> None:
        defaults = {
            "duration": 60,
            "warmup": 20,
            "tls_mode": "verify-full",
            "id_range_end": 20_000_000,
        }
        for mode in ("cancel", "export", "mixed"):
            command = cli_args(
                {"mode": mode, "args": {}},
                defaults,
                Path("output.json"),
                "test-run",
                "local",
            )
            self.assertNotIn("--id-range-end", command)
        api_command = cli_args(
            {"mode": "api", "args": {}},
            defaults,
            Path("output.json"),
            "test-run",
            "local",
        )
        self.assertIn("--id-range-end", api_command)

    def test_cancellation_outcomes_are_measurements_not_pipeline_failures(self) -> None:
        matrix = yaml.safe_load(
            (ROOT / "experiments/matrices/focused-secondary.yaml").read_text()
        )
        cancellation_runs = [run for run in matrix["runs"] if run["mode"] == "cancel"]
        self.assertEqual(len(cancellation_runs), 3)
        for run in cancellation_runs:
            self.assertEqual(run["args"]["duration"], 1)
            self.assertEqual(run["validation"]["minimum_completed"], 0)
            self.assertEqual(
                run["validation"]["maximum_failures"], run["args"]["count"]
            )

    def test_postgres_collector_query_errors_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = Path(temporary)
            (raw / "postgres.jsonl").write_text(
                json.dumps(
                    {
                        "timestamp_unix": 1,
                        "postgres": {"error": "postgres query failed"},
                    }
                )
                + "\n"
            )
            result = validate_aws_collectors(raw, 1, False)
            self.assertIn("postgres collector reported query failures", result.reasons)

    def test_telegram_notification_reads_loaded_environment(self) -> None:
        notifier = TelegramNotifier.from_env(
            {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_CHAT_ID": "12345"}
        )
        self.assertEqual(notifier, TelegramNotifier("test-token", "12345"))

    def test_telegram_notification_uses_configured_chat(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.status = 200
        notifier = TelegramNotifier("test-token", "12345")
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            self.assertTrue(notifier.send("stage complete"))
        request = urlopen.call_args.args[0]
        self.assertIn(b"chat_id=12345", request.data)
        self.assertIn(b"text=stage+complete", request.data)

    def test_focused_matrices_have_less_than_one_hour_of_scheduled_load(self) -> None:
        seconds = 0
        for name in ("focused-baseline", "focused-main", "focused-secondary"):
            matrix = yaml.safe_load(
                (ROOT / "experiments" / "matrices" / f"{name}.yaml").read_text()
            )
            defaults = matrix["defaults"]
            for run in matrix["runs"]:
                arguments = {**defaults, **run.get("args", {})}
                seconds += matrix["repeats"] * (
                    int(arguments["warmup"]) + 10 + int(arguments["duration"])
                )
        self.assertLess(seconds, 60 * 60)

    def test_network_preflight_rejects_a_failed_pair(self) -> None:
        aws_run = load_aws_run_module()
        pairs = [
            {
                "source": source,
                "target": target,
                "returncode": 0,
                "iperf": {"end": {"sum_received": {"bits_per_second": 1_000_000}}},
            }
            for source, target in (
                ("api", "pgbouncer"),
                ("export", "pgbouncer"),
                ("pgbouncer", "postgres"),
            )
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "network.json"
            path.write_text(json.dumps({"pairs": pairs}))
            aws_run.validate_network_preflight(path)
            pairs[1]["returncode"] = 1
            path.write_text(json.dumps({"pairs": pairs}))
            with self.assertRaises(RuntimeError):
                aws_run.validate_network_preflight(path)

    def test_aws_normalization_reads_remote_pgbouncer_process_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run-1"
            raw = run_dir / "raw"
            raw.mkdir(parents=True)
            manifest = {
                "run_id": "run-1",
                "status": "accepted",
                "environment": "aws",
                "matrix": "focused-main.yaml",
                "workload": "api",
                "topology": "shared",
                "pgbouncer_processes": 1,
                "pool_size_per_process": 128,
                "total_pool_budget": 128,
                "tls_mode": "verify-full",
                "repeat_number": 1,
                "measure_seconds": 10,
                "metadata": {
                    "run_definition": {"id": "api-c256-p1"},
                    "resolved_arguments": {"connections": 256},
                },
            }
            (run_dir / "manifest.json").write_text(json.dumps(manifest))
            (raw / "loadgen.json").write_text(
                json.dumps(
                    {
                        "duration_seconds": 10,
                        "counters": {"completed": 100},
                        "histograms": {},
                    }
                )
            )
            samples = [
                {"timestamp_unix": 0, "processes": [{"utime_ticks": 0, "stime_ticks": 0}]},
                {"timestamp_unix": 10, "processes": [{"utime_ticks": 100, "stime_ticks": 0}]},
            ]
            (raw / "process-pgbouncer.jsonl").write_text(
                "\n".join(json.dumps(sample) for sample in samples) + "\n"
            )
            normalized = normalize_run(run_dir)
            self.assertIsNotNone(normalized["pgbouncer_cpu_percent"])
            self.assertEqual(normalized["case_id"], "api-c256-p1")
            self.assertEqual(normalized["connections"], 256)


if __name__ == "__main__":
    unittest.main()
