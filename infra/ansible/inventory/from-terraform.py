#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def terraform_outputs() -> dict[str, object]:
    result = subprocess.run(
        ["terraform", "-chdir=infra/terraform", "output", "-json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return {key: item["value"] for key, item in json.loads(result.stdout).items()}


def host(public_ip: str, private_ip: str, private_key: str) -> dict[str, str]:
    return {
        "ansible_host": public_ip,
        "private_ip_address": private_ip,
        "ansible_ssh_private_key_file": private_key,
        "ansible_ssh_common_args": "-o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new",
    }


def build_inventory(outputs: dict[str, object], private_key: str) -> dict[str, object]:
    api_public_ips = [str(value) for value in outputs["api_loadgen_public_ips"]]
    api_private_ips = [str(value) for value in outputs["api_loadgen_private_ips"]]
    children: dict[str, object] = {
        "role_postgres": {
            "hosts": {
                "postgres": host(
                    str(outputs["postgres_public_ip"]),
                    str(outputs["postgres_private_ip"]),
                    private_key,
                )
            }
        },
        "role_pgbouncer": {
            "hosts": {
                "pgbouncer": host(
                    str(outputs["pgbouncer_public_ip"]),
                    str(outputs["pgbouncer_private_ip"]),
                    private_key,
                )
            }
        },
        "role_loadgen_api": {
            "hosts": {
                f"loadgen-api-{index + 1}": host(public_ip, private_ip, private_key)
                for index, (public_ip, private_ip) in enumerate(
                    zip(api_public_ips, api_private_ips, strict=True)
                )
            }
        },
        "role_loadgen_export": {
            "hosts": {
                "loadgen-export": host(
                    str(outputs["export_loadgen_public_ip"]),
                    str(outputs["export_loadgen_private_ip"]),
                    private_key,
                )
            }
        },
    }
    return {
        "all": {
            "hosts": {"localhost": {"ansible_connection": "local"}},
            "children": children,
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    configured_key = os.environ.get("SSH_PRIVATE_KEY")
    if not configured_key:
        raise SystemExit("SSH_PRIVATE_KEY must point to the EC2 key pair's private key")
    private_key = str(Path(configured_key).expanduser().resolve())
    if not Path(private_key).is_file():
        raise SystemExit(f"SSH private key not found: {private_key}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(build_inventory(terraform_outputs(), private_key), sort_keys=False)
    )
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
