-- Compresión y retención de readings (docs/architecture.md, sección 7) [SUPUESTO].
-- Archivo aislado a propósito: si una versión futura de TimescaleDB retira esta API,
-- se sustituye por enable_columnstore + add_columnstore_policy sin tocar 001_schema.sql.

ALTER TABLE readings SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id',
    timescaledb.compress_orderby = 'ts DESC'
);

-- Comprime los trozos con más de 7 días.
SELECT add_compression_policy('readings', compress_after => INTERVAL '7 days');

-- Borra lecturas con más de 90 días.
SELECT add_retention_policy('readings', drop_after => INTERVAL '90 days');
