# PgBouncer Multicore Limit

This repository builds a controlled experiment for finding when a single
PgBouncer process becomes the connection-layer bottleneck, measuring how
multiple `SO_REUSEPORT` processes scale with a fixed PostgreSQL connection
budget, and testing whether isolating bulk exports protects API tail latency.

It has two execution paths:

- Docker Compose runs a scaled-down functional experiment on a laptop.
- Terraform and Ansible provision the final AWS topology when explicitly run.

No AWS resource is created by setup, tests, Docker commands, `terraform init`,
or `terraform validate`. AWS creation begins only when you explicitly run
`make infra-up` and approve the Terraform apply.

Article and social-post writing are intentionally out of scope.

## Local quick start

Requirements:

- Docker Engine with the Compose plugin
- Rust 1.97 (the repository toolchain file pins it)
- uv
- Terraform 1.8 or newer for infrastructure validation

On Windows with WSL, enable Docker Desktop integration for the WSL distribution
before running these commands.

```bash
cp .env.example .env
make check-tools
make local-smoke
```

`make local-smoke` builds PostgreSQL 18.4, builds PgBouncer 1.25.2 from the
checksum-verified upstream source tarball, builds the Rust load generator, and
runs this end-to-end sequence:

1. validate the deterministic dataset through PostgreSQL directly;
2. run closed-loop API traffic through one PgBouncer process;
3. run fixed-rate API traffic through four processes sharing port 6432;
4. stream and checksum an export;
5. collect one-second metrics and PostgreSQL snapshots;
6. validate and route every run to `results/accepted/` or `results/rejected/`.

Local defaults use 100,000 rows so the functional test is laptop-friendly.
They are not suitable for performance conclusions.

Useful local commands:

```bash
make local-up              # Shared four-process topology, no TLS
make local-up-isolated     # Three API processes + one export process
make local-up-tls          # TLS on both PostgreSQL protocol legs
make local-tls-smoke       # Verify the local CA and TLS path
make local-logs
make local-down
make local-reset           # Also removes local database and certificate volumes
```

Endpoints:

| Endpoint | Address | Purpose |
|---|---|---|
| PostgreSQL | `localhost:5432` | Direct baseline |
| PgBouncer shared/API | `localhost:6432` | API and shared workloads |
| PgBouncer export | `localhost:6433` | Isolated export topology |

## Load generator

The Rust binary provides all experiment workload modes:

```text
loadgen api
loadgen export
loadgen connections
loadgen cancel
loadgen mixed
loadgen validate
```

Build and inspect it with:

```bash
make build-loadgen
loadgen/target/release/pgbouncer-loadgen --help
```

API mode supports closed-loop saturation and open-loop fixed-rate scheduling.
The JSON output records scheduled, started, completed, failed, timed-out, and
skipped operations; per-second samples; queue, service, and end-to-end latency;
and complete serialized HDR histograms. Export mode records byte counts,
SHA-256 checksums, first-byte latency, completion latency, and pacing time.

## Results

Every run is stored under one of these paths:

```text
results/accepted/<run-id>/
results/rejected/<run-id>/
```

Each directory contains a manifest, the load-generator JSON, raw collectors,
PostgreSQL snapshots, logs, validation status, and SHA-256 hashes for raw files.
Rejected runs remain available with their rejection reason.

Generate validated summaries and charts from accepted runs:

```bash
make validate-results
make summarize
make plots
```

Outputs are written to `results/summaries/`.

## AWS experiment

AWS hosts are kept in one region, availability zone, VPC, and subnet. Each of
the four experiment hosts has a public IP for direct SSH from this machine and
a private IP for measured traffic. There is no NAT gateway or bastion host.
Database traffic always uses private addresses.

### 1. Prepare configuration

```bash
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
```

Set an existing EC2 key pair, owner, expiry, and runtime limit. SSH is open to
`0.0.0.0/0` for this temporary environment and accepts only key authentication,
so protect the matching private key and destroy the environment promptly.

Point WSL at the private key that matches `ssh_key_name`:

```bash
chmod 600 ~/.ssh/pgbouncer-experiment.pem
export SSH_PRIVATE_KEY="$HOME/.ssh/pgbouncer-experiment.pem"
```

The private key stays on this machine and is ignored by Git. Terraform receives
only the AWS key-pair name, never the private key.

Export fresh experiment-only passwords. They are not written to Git:

```bash
export BENCH_PASSWORD='...'
export BENCH_BACKEND_PASSWORD='...'
export METRICS_PASSWORD='...'
export PGBOUNCER_ADMIN_PASSWORD='...'
```

Use long generated values containing only `A-Z`, `a-z`, digits, `.`, `_`, `~`,
and `-`. Provisioning rejects other characters so the same values remain safe in
PgBouncer connection strings and PostgreSQL URLs.

### 2. Review without creating resources

```bash
make infra-init
make infra-plan
make estimate-cost
```

Review the plan and current AWS pricing before continuing.

### 3. Create and configure only when ready

```bash
make infra-up
make configure
make seed-data
```

Terraform applies the `Project`, `Environment`, `Owner`, and `ExpiresAt` tags.
Every instance also schedules its own shutdown and uses
`instance_initiated_shutdown_behavior = "terminate"`.

### 4. Export runner connection details

```bash
export AWS_POSTGRES_HOST="$(terraform -chdir=infra/terraform output -raw postgres_public_ip)"
export AWS_PGBOUNCER_HOST="$(terraform -chdir=infra/terraform output -raw pgbouncer_public_ip)"
export AWS_API_LOADGEN_HOST="$(terraform -chdir=infra/terraform output -json api_loadgen_public_ips | jq -r '.[0]')"
export AWS_EXPORT_LOADGEN_HOST="$(terraform -chdir=infra/terraform output -raw export_loadgen_public_ip)"
export AWS_POSTGRES_PRIVATE_IP="$(terraform -chdir=infra/terraform output -raw postgres_private_ip)"
export AWS_PGBOUNCER_PRIVATE_IP="$(terraform -chdir=infra/terraform output -raw pgbouncer_private_ip)"
```

The runner connects directly to each public SSH address with
`SSH_PRIVATE_KEY`. Workloads target only the private PostgreSQL and PgBouncer
addresses. At the end of every run, `scp` copies load-generator output and raw
collector files from all four servers into the local ignored `results/` tree.

### 5. Run matrices

```bash
make discover
make run-direct
make run-tls
make run-scaling
make run-mixed
make run-isolation
make run-cancellation
```

Final matrices randomize topology order within repeats and keep the total
PgBouncer server pool budget at 128.

Operational tests are explicit subcommands:

```bash
uv run experiments/scripts/aws_operations.py network-preflight
uv run experiments/scripts/aws_operations.py process-failure
uv run experiments/scripts/aws_operations.py rolling-restart
uv run experiments/scripts/aws_operations.py dynamic-scale
```

### 6. Tear down

```bash
make infra-down
```

If normal teardown cannot be used, `make emergency-stop` terminates EC2
instances carrying the exact project tag. Terraform teardown is still required
afterward to remove networking and other resources.

## Development checks

```bash
make test
make format
make lint
make validate-config
```

`make validate-config` does not create infrastructure. Terraform must have been
initialized once so its provider schema is available locally.

See [plan.md](plan.md) for the research design and [docs/runbook.md](docs/runbook.md)
for the run sequence and acceptance gates.
