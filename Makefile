SHELL := /bin/bash

.PHONY: check-tools local-up local-up-tls local-up-isolated local-down local-reset local-logs local-smoke local-tls-smoke inventory \
	build-loadgen test format lint validate-config infra-init infra-plan infra-up infra-down configure seed-data \
	discover run-direct run-tls run-scaling run-mixed run-isolation run-cancellation run-operations \
	run-fairness run-pool-multiplication aws-run validate-results summarize plots emergency-stop estimate-cost

check-tools:
	@command -v docker >/dev/null
	@command -v uv >/dev/null
	@command -v cargo >/dev/null
	@command -v terraform >/dev/null
	@docker compose version >/dev/null

local-up:
	docker compose up --build --detach postgres pgbouncer

local-up-tls:
	POSTGRES_SSL=on CLIENT_TLS_SSLMODE=require SERVER_TLS_SSLMODE=verify-full docker compose up --build --detach postgres pgbouncer

local-up-isolated:
	PGBOUNCER_TOPOLOGY=isolated PGBOUNCER_PROCESSES=4 docker compose up --build --detach postgres pgbouncer

local-down:
	docker compose down

local-reset:
	docker compose down --volumes

local-logs:
	docker compose logs --follow postgres pgbouncer

local-smoke: local-up build-loadgen
	uv run experiments/runner/runner.py --matrix experiments/matrices/smoke.yaml --environment local

local-tls-smoke: local-up-tls
	docker compose --profile tools run --rm --entrypoint sh loadgen -c 'exec loadgen validate --dsn "postgresql://bench_login:$${BENCH_PASSWORD}@pgbouncer:6432/bench" --tls-mode verify-full --ca-cert /certs/ca.crt --expected-rows "$${LOCAL_DATASET_ROWS}"'

build-loadgen:
	cargo build --manifest-path loadgen/Cargo.toml --release

test:
	cargo test --manifest-path loadgen/Cargo.toml
	uv run -m unittest discover -s tests

format:
	cargo fmt --manifest-path loadgen/Cargo.toml
	terraform fmt -recursive infra/terraform

lint:
	cargo clippy --manifest-path loadgen/Cargo.toml --all-targets -- -D warnings
	ANSIBLE_CONFIG=infra/ansible/ansible.cfg uv run ansible-playbook -i infra/ansible/inventory/local.yml --syntax-check infra/ansible/playbooks/site.yml

validate-config:
	docker compose config --quiet
	terraform -chdir=infra/terraform validate

infra-init:
	terraform -chdir=infra/terraform init

infra-plan:
	terraform -chdir=infra/terraform plan

infra-up:
	terraform -chdir=infra/terraform apply

infra-down:
	terraform -chdir=infra/terraform destroy

inventory:
	uv run infra/ansible/inventory/from-terraform.py --output infra/ansible/inventory/generated.yml

configure: build-loadgen inventory
	uv run ansible-galaxy collection install -r infra/ansible/requirements.yml
	ANSIBLE_CONFIG=infra/ansible/ansible.cfg uv run ansible-playbook -i infra/ansible/inventory/generated.yml infra/ansible/playbooks/site.yml

seed-data:
	ANSIBLE_CONFIG=infra/ansible/ansible.cfg uv run ansible-playbook -i infra/ansible/inventory/generated.yml infra/ansible/playbooks/seed.yml

discover:
	uv run experiments/runner/runner.py --matrix experiments/matrices/discovery.yaml --environment aws

run-direct:
	uv run experiments/runner/runner.py --matrix experiments/matrices/direct.yaml --environment aws

run-tls:
	uv run experiments/runner/runner.py --matrix experiments/matrices/tls.yaml --environment aws

run-scaling:
	uv run experiments/runner/runner.py --matrix experiments/matrices/scaling.yaml --environment aws

run-mixed:
	uv run experiments/runner/runner.py --matrix experiments/matrices/mixed.yaml --environment aws

run-isolation:
	uv run experiments/runner/runner.py --matrix experiments/matrices/isolation.yaml --environment aws

run-cancellation:
	uv run experiments/runner/runner.py --matrix experiments/matrices/cancellation.yaml --environment aws

run-fairness:
	uv run experiments/runner/runner.py --matrix experiments/matrices/fairness.yaml --environment aws

run-pool-multiplication:
	uv run experiments/runner/runner.py --matrix experiments/matrices/pool-multiplication.yaml --environment aws

run-operations:
	uv run experiments/scripts/aws_operations.py --help

aws-run:
	uv run experiments/scripts/aws_run.py

validate-results:
	uv run analysis/validate_runs.py results

summarize:
	uv run analysis/summarize.py results

plots:
	uv run analysis/plots.py results/summaries/runs.csv results/summaries

estimate-cost:
	uv run experiments/scripts/estimate_cost.py --hours 8

emergency-stop:
	CONFIRM_PROJECT=pgbouncer-multicore-limit experiments/scripts/emergency-stop.sh
