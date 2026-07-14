CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pg_prewarm;

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bench_login') THEN
        CREATE ROLE bench_login LOGIN PASSWORD 'bench-password';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bench_backend') THEN
        CREATE ROLE bench_backend LOGIN PASSWORD 'bench-backend-password';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'metrics') THEN
        CREATE ROLE metrics LOGIN PASSWORD 'metrics-password';
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS events (
    id          bigint PRIMARY KEY,
    tenant_id   integer NOT NULL,
    created_at  timestamptz NOT NULL,
    event_type  smallint NOT NULL,
    payload     text NOT NULL
);

CREATE INDEX IF NOT EXISTS events_tenant_id_id_idx
    ON events (tenant_id, id);

GRANT CONNECT ON DATABASE bench TO bench_login, bench_backend, metrics;
GRANT USAGE ON SCHEMA public TO bench_login, bench_backend, metrics;
GRANT SELECT ON events TO bench_login, bench_backend, metrics;
GRANT pg_monitor TO metrics;
