#!/usr/bin/env bash
set -euo pipefail

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --file /project/postgres/schema.sql

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set dataset_rows="${LOCAL_DATASET_ROWS:-100000}" \
    --file /project/postgres/generate.sql

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --command "ALTER ROLE bench_login PASSWORD '${BENCH_PASSWORD}';" \
    --command "ALTER ROLE bench_backend PASSWORD '${BENCH_BACKEND_PASSWORD}';" \
    --command "ALTER ROLE metrics PASSWORD '${METRICS_PASSWORD}';"
