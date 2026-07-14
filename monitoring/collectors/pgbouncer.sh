#!/usr/bin/env bash
set -euo pipefail

output=${1:?usage: pgbouncer.sh OUTPUT.log PROCESS_COUNT}
processes=${2:?usage: pgbouncer.sh OUTPUT.log PROCESS_COUNT}
running=1
trap 'running=0' TERM INT

while (( running )); do
    timestamp=$(date +%s)
    for instance in $(seq 1 "$processes"); do
        port=6432
        if ! stats=$(psql -h "/run/pgbouncer/$instance" -p "$port" -U pgbouncer_admin -d pgbouncer -At -c 'SHOW STATS;' 2>/dev/null); then
            port=6433
            stats=$(psql -h "/run/pgbouncer/$instance" -p "$port" -U pgbouncer_admin -d pgbouncer -At -c 'SHOW STATS;' 2>/dev/null || true)
        fi
        if [[ -n "$stats" ]]; then
            while IFS= read -r line; do
                printf '%s|%s|%s|stats|%s\n' "$timestamp" "$instance" "$port" "$line" >>"$output"
            done <<<"$stats"
        fi
        for command in 'SHOW POOLS;' 'SHOW LISTS;' 'SHOW MEM;'; do
            psql -h "/run/pgbouncer/$instance" -p "$port" -U pgbouncer_admin -d pgbouncer -At -c "$command" 2>/dev/null |
                while IFS= read -r line; do
                    printf '%s|%s|%s|%s|%s\n' "$timestamp" "$instance" "$port" "${command%;}" "$line" >>"$output"
                done || true
        done
    done
    sleep 1 &
    wait $! || true
done
