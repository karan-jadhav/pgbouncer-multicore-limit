#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import signal
import subprocess
import time
from pathlib import Path

running = True


def stop(_signum: int, _frame: object) -> None:
    global running
    running = False


QUERY = """
SELECT json_build_object(
  'active_backends', (SELECT count(*) FROM pg_stat_activity WHERE state = 'active'),
  'idle_backends', (SELECT count(*) FROM pg_stat_activity WHERE state = 'idle'),
  'connections', (SELECT count(*) FROM pg_stat_activity),
  'transactions', (SELECT xact_commit + xact_rollback FROM pg_stat_database WHERE datname = current_database()),
  'blocks_hit', (SELECT blks_hit FROM pg_stat_database WHERE datname = current_database()),
  'blocks_read', (SELECT blks_read FROM pg_stat_database WHERE datname = current_database()),
  'temp_bytes', (SELECT temp_bytes FROM pg_stat_database WHERE datname = current_database())
);
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with args.output.open("w") as output:
        while running:
            result = subprocess.run(
                ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "bench", "-At", "-c", QUERY],
                text=True,
                capture_output=True,
            )
            record: dict[str, object] = {"timestamp_unix": time.time()}
            if result.returncode == 0:
                try:
                    record["postgres"] = json.loads(result.stdout)
                except json.JSONDecodeError:
                    record["error"] = result.stdout.strip()
            else:
                record["error"] = result.stderr.strip()
            output.write(json.dumps(record) + "\n")
            output.flush()
            time.sleep(1)


if __name__ == "__main__":
    main()
