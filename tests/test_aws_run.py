from __future__ import annotations

import importlib.util
import json
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
from runner import cli_args  # noqa: E402
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
