# Troubleshooting

## Docker says it is unavailable in WSL

Enable Docker Desktop integration for the active WSL distribution, restart the
distribution, and verify both `docker version` and `docker compose version`.

## Local dataset changes do not appear

PostgreSQL initialization scripts run only for a new volume. Run
`make local-reset`, change `LOCAL_DATASET_ROWS`, and start again.

## Local TLS certificates expired

Run `make local-reset`. The certificate generator creates a fresh seven-day CA
and server certificates in the new Docker volume.

## PgBouncer does not start

Inspect `make local-logs`. Common causes are an invalid process count, a pool
budget that does not divide evenly, a stale certificate volume, or passwords in
`.env` that differ from an existing PostgreSQL volume.

## Ansible cannot reach AWS hosts

Confirm `SSH_PRIVATE_KEY` points to the private key matching `ssh_key_name`, its
mode is `0600`, and regenerate inventory with `make inventory`. Ansible connects
directly to each host's public IP. Measured traffic still uses private IPs.

## AWS run is rejected

Read `results/rejected/<run-id>/manifest.json` first. It contains the rejection
reason and hashes of the retained raw artifacts. Correct the environment issue;
do not move the rejected directory into `accepted` manually.
