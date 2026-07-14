from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "runner"))

from remote import ssh_prefix  # noqa: E402
from runner import aws_dsn  # noqa: E402


def load_inventory_module():
    path = ROOT / "infra" / "ansible" / "inventory" / "from-terraform.py"
    spec = importlib.util.spec_from_file_location("from_terraform", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AwsAccessTests(unittest.TestCase):
    def test_workload_dsns_use_private_addresses(self) -> None:
        environment = {
            "BENCH_PASSWORD": "temporary-password",
            "AWS_POSTGRES_PRIVATE_IP": "10.80.0.10",
            "AWS_PGBOUNCER_PRIVATE_IP": "10.80.0.11",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertIn("@10.80.0.10:5432/", aws_dsn("postgres"))
            self.assertIn("@10.80.0.11:6432/", aws_dsn("pgbouncer"))
            self.assertIn("@10.80.0.11:6433/", aws_dsn("export"))

    def test_inventory_uses_public_ip_for_ssh_and_private_ip_for_traffic(self) -> None:
        outputs = {
            "postgres_public_ip": "203.0.113.10",
            "postgres_private_ip": "10.80.0.10",
            "pgbouncer_public_ip": "203.0.113.11",
            "pgbouncer_private_ip": "10.80.0.11",
            "api_loadgen_public_ips": ["203.0.113.12"],
            "api_loadgen_private_ips": ["10.80.0.12"],
            "export_loadgen_public_ip": "203.0.113.13",
            "export_loadgen_private_ip": "10.80.0.13",
        }
        inventory = load_inventory_module().build_inventory(outputs, "/tmp/key.pem")
        postgres = inventory["all"]["children"]["role_postgres"]["hosts"]["postgres"]
        self.assertEqual(postgres["ansible_host"], "203.0.113.10")
        self.assertEqual(postgres["private_ip_address"], "10.80.0.10")
        self.assertNotIn("role_control", inventory["all"]["children"])

    def test_ssh_uses_configured_private_key_without_a_bastion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / "experiment.pem"
            key.write_text("temporary test key")
            with patch.dict(os.environ, {"SSH_PRIVATE_KEY": str(key)}):
                command = ssh_prefix("203.0.113.10")
        self.assertIn(str(key), command)
        self.assertNotIn("-J", command)
        self.assertEqual(command[-1], "ubuntu@203.0.113.10")


if __name__ == "__main__":
    unittest.main()
