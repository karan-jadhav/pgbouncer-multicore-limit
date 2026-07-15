# PgBouncer Multicore Limit

This repository reproduces an experiment around PgBouncer's single-process CPU
limit. It measures how 1, 2, 4, and 8 processes scale while the total
PostgreSQL connection budget stays fixed.

Run the functional experiment locally with Docker Compose or run the measured
version on AWS using Terraform and Ansible.

No AWS resource is created by setup, tests, Docker commands, `terraform init`,
or `terraform validate`. AWS creation begins only when you explicitly run
`make infra-up` and approve the Terraform apply.

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

The completed focused AWS run used 256 persistent clients, TLS on both
PostgreSQL protocol legs, and a fixed combined PostgreSQL pool budget of 128.
The table reports the median of three randomized 60-second measurements:

| PgBouncer processes | Queries/sec | Speedup | Closed-loop p99 |
|---:|---:|---:|---:|
| 1 | 45,122 | 1.00x | 8.19 ms |
| 2 | 80,788 | 1.79x | 4.57 ms |
| 4 | 153,369 | 3.40x | 2.76 ms |
| 8 | 209,779 | 4.65x | 2.36 ms |

One PgBouncer process filled one CPU core while the PostgreSQL host was at
about 22% CPU. At eight processes, PostgreSQL reached about 97% CPU, so the
bottleneck had moved from the proxy layer to the database. Direct PostgreSQL
reached 345,769 queries/sec at 256 clients in a single baseline run; PgBouncer
improved the capacity of the pooled architecture but did not outperform the
direct path.

Read the full methodology, analysis, and limitations in
[PgBouncer: From 45K to 210K Queries per Second](https://jadhav.dev/blog/pgbouncer-multicore-limit/pgbouncer-from-45k-to-210k-queries-per-second).

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

## Automated focused AWS run

Run the complete experiment from your laptop. Set the AWS profile, SSH key, and
Telegram values in the ignored `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Authenticate the AWS CLI profile configured in `.env`, then start the run:

```console
$ aws login --profile "$AWS_PROFILE"
$ make aws-run
```

Keep the laptop awake and connected until the command finishes. The automation
sends Telegram notifications for every stage and individual run. It creates
only the four measured workers, configures and seeds them, runs the focused
matrices, copies and validates results locally, creates summaries and plots,
and attempts `terraform destroy` for the complete worker stack on both success
and failure. Results remain on the laptop in:

```text
.local/aws-results.tar.gz
results/aws-run-status.json
```

The focused matrices schedule about 59 minutes of load; provisioning, package
installation, dataset generation, topology changes, analysis, and cleanup bring
the expected end-to-end time to roughly one to two hours.

## Development checks

```bash
make test
make format
make lint
make validate-config
```

`make validate-config` does not create infrastructure. Terraform must have been
initialized once so its provider schema is available locally.

See [docs/runbook.md](docs/runbook.md) for the run sequence and acceptance
gates.
