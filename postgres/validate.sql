SELECT count(*) AS dataset_rows FROM events;

SELECT
    pg_relation_size('events') AS heap_bytes,
    pg_indexes_size('events') AS index_bytes,
    pg_total_relation_size('events') AS total_bytes;

SELECT count(DISTINCT tenant_id) AS tenant_count,
       min(tenant_id) AS first_tenant,
       max(tenant_id) AS last_tenant
FROM events;
