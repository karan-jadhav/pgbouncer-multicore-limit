from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path


COPY_ATTEMPTS = 5
COPY_RETRY_DELAY_SECONDS = 5


def private_key() -> str:
    configured = os.environ.get("SSH_PRIVATE_KEY")
    if not configured:
        raise RuntimeError("SSH_PRIVATE_KEY must point to the EC2 private key")
    path = Path(configured).expanduser()
    if not path.is_file():
        raise RuntimeError(f"SSH private key not found: {path}")
    return str(path)


def ssh_prefix(host: str, *, user: str = "ubuntu") -> list[str]:
    command = [
        "ssh",
        "-i",
        private_key(),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "BatchMode=yes",
    ]
    command.append(f"{user}@{host}")
    return command


def run_ssh(
    host: str,
    command: list[str],
    *,
    user: str = "ubuntu",
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    remote_command = shlex.join(command)
    return subprocess.run(
        [*ssh_prefix(host, user=user), remote_command],
        check=check,
        text=True,
        capture_output=True,
    )


def copy_from(host: str, remote_path: str, local_path: Path, *, user: str = "ubuntu") -> None:
    command = [
        "scp",
        "-i",
        private_key(),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    for attempt in range(1, COPY_ATTEMPTS + 1):
        try:
            subprocess.run(
                [*command, f"{user}@{host}:{remote_path}", str(local_path)],
                check=True,
            )
            return
        except subprocess.CalledProcessError:
            if attempt == COPY_ATTEMPTS:
                raise
            time.sleep(COPY_RETRY_DELAY_SECONDS)


def start_ssh(host: str, command: list[str], *, user: str = "ubuntu") -> subprocess.Popen[str]:
    return subprocess.Popen(
        [*ssh_prefix(host, user=user), shlex.join(command)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
