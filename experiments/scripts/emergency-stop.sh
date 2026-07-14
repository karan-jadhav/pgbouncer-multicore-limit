#!/usr/bin/env bash
set -euo pipefail

project=pgbouncer-multicore-limit
if [[ "${CONFIRM_PROJECT:-}" != "$project" ]]; then
    echo "Set CONFIRM_PROJECT=$project to terminate tagged experiment instances." >&2
    exit 2
fi

mapfile -t instance_ids < <(
    aws ec2 describe-instances \
        --filters "Name=tag:Project,Values=$project" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
        --query 'Reservations[].Instances[].InstanceId' \
        --output text | tr '\t' '\n'
)

if (( ${#instance_ids[@]} == 0 )); then
    echo "No tagged instances found."
    exit 0
fi

aws ec2 terminate-instances --instance-ids "${instance_ids[@]}"
