#!/bin/sh
set -eu

if [ -f /certs/ca.crt ]; then
    exit 0
fi

openssl genrsa -out /certs/ca.key 4096
openssl req -x509 -new -key /certs/ca.key -sha256 -days 7 \
    -subj /CN=pgbouncer-local-ca -out /certs/ca.crt

for service in postgres pgbouncer; do
    openssl genrsa -out "/certs/$service.key" 3072
    if [ "$service" = postgres ]; then
        names="DNS:postgres,DNS:localhost,IP:127.0.0.1"
    else
        names="DNS:pgbouncer,DNS:localhost,IP:127.0.0.1"
    fi
    openssl req -new -key "/certs/$service.key" -subj "/CN=$service" \
        -addext "subjectAltName=$names" -out "/certs/$service.csr"
    openssl x509 -req -in "/certs/$service.csr" -CA /certs/ca.crt \
        -CAkey /certs/ca.key -CAcreateserial -days 7 -sha256 \
        -copy_extensions copy -out "/certs/$service.crt"
done

chown -R 999:999 /certs
chmod 600 /certs/ca.key /certs/postgres.key /certs/pgbouncer.key
chmod 644 /certs/ca.crt /certs/postgres.crt /certs/pgbouncer.crt
