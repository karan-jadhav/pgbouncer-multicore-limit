#!/usr/bin/env bash
set -euo pipefail

output=${1:?usage: postgres.sh OUTPUT.jsonl}
running=1
trap 'running=0' TERM INT

query="SELECT json_build_object('active_backends',(SELECT count(*) FROM pg_stat_activity WHERE state='active'),'idle_backends',(SELECT count(*) FROM pg_stat_activity WHERE state='idle'),'connections',(SELECT count(*) FROM pg_stat_activity),'transactions',(SELECT xact_commit+xact_rollback FROM pg_stat_database WHERE datname=current_database()),'blocks_hit',(SELECT blks_hit FROM pg_stat_database WHERE datname=current_database()),'blocks_read',(SELECT blks_read FROM pg_stat_database WHERE datname=current_database()),'temp_bytes',(SELECT temp_bytes FROM pg_stat_database WHERE datname=current_database()));"

while (( running )); do
    value=$(psql -h 127.0.0.1 -U metrics -d bench -At -c "$query" 2>/dev/null || echo '{"error":"postgres query failed"}')
    printf '{"timestamp_unix":%s,"postgres":%s}\n' "$(date +%s)" "$value" >>"$output"
    sleep 1 &
    wait $! || true
done
