from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RunManifest:
    run_id: str
    status: str
    rejection_reason: str | None
    started_at: str
    ended_at: str | None
    git_commit: str
    dirty_tree: bool
    environment: str
    matrix: str
    workload: str
    topology: str
    pgbouncer_processes: int
    pool_size_per_process: int
    total_pool_budget: int
    tls_mode: str
    offered_rate: int | None
    warmup_seconds: int
    measure_seconds: int
    repeat_number: int
    random_seed: int
    validation_status: str = "pending"
    collector_status: str = "pending"
    loadgen_sha256: str | None = None
    raw_file_sha256: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
