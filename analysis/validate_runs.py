#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_run(run_dir: Path) -> list[str]:
    errors = []
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return ["manifest.json is missing"]
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("run_id") != run_dir.name:
        errors.append("manifest run_id does not match directory name")
    for relative, expected in manifest.get("raw_file_sha256", {}).items():
        path = run_dir / "raw" / relative
        if not path.exists():
            errors.append(f"raw file is missing: {relative}")
        elif sha256_file(path) != expected:
            errors.append(f"raw file hash differs: {relative}")
    if manifest.get("status") == "accepted":
        if manifest.get("validation_status") != "accepted":
            errors.append("accepted run lacks accepted validation status")
        if manifest.get("rejection_reason"):
            errors.append("accepted run has a rejection reason")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    invalid = 0
    checked = 0
    for status in ("accepted", "rejected"):
        directory = args.results / status
        if not directory.exists():
            continue
        for run_dir in sorted(path for path in directory.iterdir() if path.is_dir()):
            checked += 1
            errors = validate_run(run_dir)
            if errors:
                invalid += 1
                print(f"INVALID {run_dir.name}: {'; '.join(errors)}")
    print(f"validated {checked} runs; {invalid} invalid")
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
