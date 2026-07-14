#!/usr/bin/env bash
set -euo pipefail

processes="${PGBOUNCER_PROCESSES:-4}"
topology="${PGBOUNCER_TOPOLOGY:-shared}"
total_budget="${TOTAL_POOL_BUDGET:-128}"
config_root=/run/pgbouncer/config
mkdir -p "$config_root"

for password in \
    "${BENCH_PASSWORD:-bench-password}" \
    "${BENCH_BACKEND_PASSWORD:-bench-backend-password}" \
    "${METRICS_PASSWORD:-metrics-password}" \
    "${PGBOUNCER_ADMIN_PASSWORD:-pgbouncer-admin-password}"; do
    if [[ ! "$password" =~ ^[A-Za-z0-9._~-]+$ ]]; then
        echo "Experiment passwords must use URL-safe characters: A-Z a-z 0-9 . _ ~ -" >&2
        exit 2
    fi
done

case "$processes" in
    1|2|4|8) ;;
    *) echo "PGBOUNCER_PROCESSES must be 1, 2, 4, or 8" >&2; exit 2 ;;
esac

if [[ "$topology" == "isolated" && "$processes" != "4" ]]; then
    echo "The isolated topology requires exactly four processes" >&2
    exit 2
fi

pool_size=$((total_budget / processes))
if (( pool_size * processes != total_budget )); then
    echo "TOTAL_POOL_BUDGET must divide evenly across PGBOUNCER_PROCESSES" >&2
    exit 2
fi

users_file="$config_root/users.txt"
cat >"$users_file" <<EOF
"bench_login" "${BENCH_PASSWORD:-bench-password}"
"metrics" "${METRICS_PASSWORD:-metrics-password}"
"pgbouncer_admin" "${PGBOUNCER_ADMIN_PASSWORD:-pgbouncer-admin-password}"
EOF
chmod 600 "$users_file"

client_tls_settings="client_tls_sslmode = ${CLIENT_TLS_SSLMODE:-disable}"
if [[ "${CLIENT_TLS_SSLMODE:-disable}" != "disable" ]]; then
    client_tls_settings+=$'\nclient_tls_ca_file = /certs/ca.crt'
    client_tls_settings+=$'\nclient_tls_cert_file = /certs/pgbouncer.crt'
    client_tls_settings+=$'\nclient_tls_key_file = /certs/pgbouncer.key'
fi
server_tls_settings="server_tls_sslmode = ${SERVER_TLS_SSLMODE:-disable}"
if [[ "${SERVER_TLS_SSLMODE:-disable}" != "disable" ]]; then
    server_tls_settings+=$'\nserver_tls_ca_file = /certs/ca.crt'
fi

peer_lines=""
for id in $(seq 1 "$processes"); do
    if [[ "$topology" == "isolated" && "$id" == "4" ]]; then
        port=6433
    else
        port=6432
    fi
    socket_dir="/run/pgbouncer/$id"
    mkdir -p "$socket_dir"
    if [[ "${PGBOUNCER_PEERING:-1}" == "1" ]]; then
        peer_lines+="$id = host=$socket_dir port=$port"$'\n'
    fi
done

pids=()
for id in $(seq 1 "$processes"); do
    if [[ "$topology" == "isolated" && "$id" == "4" ]]; then
        port=6433
    else
        port=6432
    fi
    socket_dir="/run/pgbouncer/$id"
    config="$config_root/pgbouncer-$id.ini"

    cat >"$config" <<EOF
[databases]
bench = host=postgres port=5432 dbname=bench user=bench_backend password=${BENCH_BACKEND_PASSWORD:-bench-backend-password}

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = $port
unix_socket_dir = $socket_dir
so_reuseport = 1
peer_id = $id
pidfile = /run/pgbouncer/pgbouncer-$id.pid
logfile = /var/log/pgbouncer/pgbouncer-$id.log

pool_mode = transaction
max_client_conn = 50000
default_pool_size = $pool_size
reserve_pool_size = 0
min_pool_size = 0
max_prepared_statements = 200

auth_type = scram-sha-256
auth_file = $users_file
admin_users = pgbouncer_admin
stats_users = metrics

stats_period = 1
log_stats = 0
log_connections = 0
log_disconnections = 0
log_pooler_errors = 1

sbuf_loopcnt = 5
pkt_buf = 4096
$client_tls_settings
$server_tls_settings
server_reset_query = DISCARD ALL
ignore_startup_parameters = extra_float_digits

[peers]
$peer_lines
EOF

    /opt/pgbouncer/bin/pgbouncer "$config" &
    pids+=("$!")
done

terminate() {
    kill -TERM "${pids[@]}" 2>/dev/null || true
}
trap terminate TERM INT

status=0
for pid in "${pids[@]}"; do
    wait "$pid" || status=$?
done
exit "$status"
