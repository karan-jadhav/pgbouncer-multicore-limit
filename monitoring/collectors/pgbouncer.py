#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path

running = True


def stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def query(instance: int, password: str) -> dict[str, object]:
    errors = []
    for port in (6432, 6433):
        result = subprocess.run(
            [
                "docker", "compose", "exec", "-T", "-e", f"PGPASSWORD={password}",
                "pgbouncer", "psql", "-h", f"/run/pgbouncer/{instance}", "-p", str(port),
                "-U", "pgbouncer_admin", "-d", "pgbouncer", "-At",
                "-c", "SHOW STATS;", "-c", "SHOW POOLS;", "-c", "SHOW LISTS;", "-c", "SHOW MEM;",
            ],
            text=True,
            capture_output=True,
        )
        if result.returncode == 0:
            return {"instance": instance, "port": port, "admin_output": result.stdout.splitlines()}
        errors.append(result.stderr.strip())
    return {"instance": instance, "error": " | ".join(errors)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--processes", type=int, required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    password = os.environ.get("PGBOUNCER_ADMIN_PASSWORD", "pgbouncer-admin-password")
    with args.output.open("w") as output:
        while running:
            sample = {
                "timestamp_unix": time.time(),
                "instances": [query(instance, password) for instance in range(1, args.processes + 1)],
            }
            output.write(json.dumps(sample) + "\n")
            output.flush()
            time.sleep(1)


if __name__ == "__main__":
    main()
