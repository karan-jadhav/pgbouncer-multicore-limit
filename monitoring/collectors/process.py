#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import time
from pathlib import Path

running = True


def stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def collect(match: str) -> list[dict[str, object]]:
    records = []
    page_size = os.sysconf("SC_PAGE_SIZE")
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        if int(entry.name) == os.getpid():
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            if match not in command:
                continue
            fields = (entry / "stat").read_text().split()
            status = {}
            for line in (entry / "status").read_text().splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    status[key] = value.strip()
            records.append(
                {
                    "pid": int(entry.name),
                    "command": command,
                    "utime_ticks": int(fields[13]),
                    "stime_ticks": int(fields[14]),
                    "rss_bytes": int(fields[23]) * page_size,
                    "virtual_memory_kb": int(status.get("VmSize", "0 kB").split()[0]),
                    "minor_faults": int(fields[9]),
                    "major_faults": int(fields[11]),
                    "last_cpu": int(fields[38]),
                    "threads": int(status.get("Threads", "0")),
                    "voluntary_context_switches": int(status.get("voluntary_ctxt_switches", "0")),
                    "involuntary_context_switches": int(status.get("nonvoluntary_ctxt_switches", "0")),
                    "file_descriptors": len(list((entry / "fd").iterdir())),
                    "cpu_affinity": status.get("Cpus_allowed_list", ""),
                }
            )
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
            continue
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--match", required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with args.output.open("w") as output:
        while running:
            output.write(json.dumps({"timestamp_unix": time.time(), "processes": collect(args.match)}) + "\n")
            output.flush()
            time.sleep(1)


if __name__ == "__main__":
    main()
