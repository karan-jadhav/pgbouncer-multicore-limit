\if :{?dataset_rows}
\else
\set dataset_rows 100000
\endif

TRUNCATE events;

INSERT INTO events (
    id,
    tenant_id,
    created_at,
    event_type,
    payload
)
SELECT
    g,
    LEAST(((g - 1) / GREATEST(:dataset_rows / 100, 1))::integer, 99),
    timestamptz '2025-01-01 00:00:00+00'
        + ((g % 31536000) * interval '1 second'),
    (g % 32)::smallint,
    repeat(md5(g::text), 8)
FROM generate_series(1, :dataset_rows) AS g;

CLUSTER events USING events_tenant_id_id_idx;
VACUUM (ANALYZE, FREEZE) events;
