# Runbook

## Local functional gate

1. Copy `.env.example` to `.env`.
2. Run `make local-smoke`.
3. Confirm every smoke run is under `results/accepted/`.
4. Run `make validate-results`.
5. Optionally run `make local-tls-smoke`.
6. Run `make local-reset` when finished.

Do not use local results for throughput or latency claims.

## AWS preparation gate

1. Set Terraform variables, passwords, and an expiry.
2. Export `SSH_PRIVATE_KEY` with the path to the private key matching the EC2
   key-pair name.
3. Run `make infra-init`, `make infra-plan`, and `make estimate-cost`.
4. Review instance types, one-AZ placement, security groups, volume settings,
   and automatic termination behavior.
5. Run `make infra-up` only after reviewing the plan.
6. Run `make configure` and `make seed-data`.
7. Export the six public/private host variables shown in the README.

## Infrastructure ceiling gate

Run the network preflight and direct PostgreSQL matrix. Do not continue when:

- a generator cannot maintain the offered rate;
- direct PostgreSQL lacks at least 2x the expected single-PgBouncer headroom;
- network utilization approaches the measured ceiling;
- collector overhead exceeds 3% CPU.

## Discovery and final runs

1. Run `make discover` to locate the single-process knee.
2. Update final offered rates in the matrices and record the reason.
3. Run TLS attribution and 1/2/4/8 scaling matrices.
4. Run shared versus isolated workloads.
5. Run cancellation and explicit operational tests.
6. Run `make validate-results`, `make summarize`, and `make plots`.

Final runs use 60 seconds of warm-up, 180 seconds of measurement, and at least
three repeats. Increase to five repeats when variation exceeds 3%.

## Teardown gate

1. Copy every required result to the local repository.
2. Run `make infra-down`.
3. Confirm the Terraform state contains no managed resources.
4. Confirm no project-tagged EC2 instance remains.

## Automated focused AWS run

1. Configure the ignored `.env`, Terraform tfvars, SSH key, Telegram bot token,
   and Telegram chat ID on the laptop.
2. Ensure the repository is clean.
3. Run `aws login --profile "$AWS_PROFILE"`.
4. Run `make aws-run` and keep the laptop awake and connected.
5. Wait for the final Telegram notification.
6. Confirm `.local/aws-results.tar.gz` exists.
7. Confirm `results/aws-run-status.json` reports completed worker cleanup.
