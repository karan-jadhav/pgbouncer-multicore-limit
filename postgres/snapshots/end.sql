SELECT clock_timestamp() AS captured_at, * FROM pg_stat_database WHERE datname = current_database();
SELECT clock_timestamp() AS captured_at, * FROM pg_stat_io;
SELECT clock_timestamp() AS captured_at, * FROM pg_stat_bgwriter;
SELECT clock_timestamp() AS captured_at, * FROM pg_stat_wal;
SELECT clock_timestamp() AS captured_at, * FROM pg_stat_statements;
