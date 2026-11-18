#!/usr/bin/env bash
# Crea los roles de mínimo privilegio (docs/architecture.md, sección 10).
# Las contraseñas llegan por variables de entorno desde docker-compose.yml (.env).
# Este script solo se ejecuta cuando el volumen de datos está vacío.
set -eo pipefail

for var in INGESTOR_DB_PASSWORD ALERT_ENGINE_DB_PASSWORD API_DB_PASSWORD ANALYTICS_DB_PASSWORD GRAFANA_RO_DB_PASSWORD; do
  if [ -z "${!var}" ]; then
    echo "ERROR: falta la variable $var en .env" >&2
    exit 1
  fi
done

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v ing_pw="$INGESTOR_DB_PASSWORD" \
  -v alr_pw="$ALERT_ENGINE_DB_PASSWORD" \
  -v api_pw="$API_DB_PASSWORD" \
  -v ana_pw="$ANALYTICS_DB_PASSWORD" \
  -v grf_pw="$GRAFANA_RO_DB_PASSWORD" <<'EOSQL'

CREATE ROLE ingestor     LOGIN PASSWORD :'ing_pw';
CREATE ROLE alert_engine LOGIN PASSWORD :'alr_pw';
CREATE ROLE api_svc      LOGIN PASSWORD :'api_pw';
CREATE ROLE analytics    LOGIN PASSWORD :'ana_pw';
CREATE ROLE grafana_ro   LOGIN PASSWORD :'grf_pw';

GRANT USAGE ON SCHEMA public TO ingestor, alert_engine, api_svc, analytics, grafana_ro;

-- ingestor: inserta lecturas y rechazos; consulta dispositivos; mantiene device_status.
GRANT SELECT ON sites, devices TO ingestor;
GRANT SELECT, INSERT ON readings TO ingestor;
GRANT INSERT ON rejected_messages TO ingestor;
GRANT SELECT, INSERT, UPDATE ON device_status TO ingestor;

-- alert_engine: lee datos y gestiona alertas.
GRANT SELECT ON sites, devices, readings, device_status TO alert_engine;
GRANT SELECT, INSERT, UPDATE ON alerts TO alert_engine;

-- api_svc: lectura de todo; el ACK solo puede cambiar state y acked_at.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO api_svc;
GRANT UPDATE (state, acked_at) ON alerts TO api_svc;
ALTER ROLE api_svc SET statement_timeout = '5s';

-- analytics: lee lecturas y escribe KPIs.
GRANT SELECT ON sites, devices, readings TO analytics;
GRANT SELECT, INSERT, UPDATE ON kpi_daily, anomaly_scores TO analytics;

-- grafana_ro: solo lectura.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_ro;
ALTER ROLE grafana_ro SET statement_timeout = '15s';

-- Tablas o vistas futuras creadas por el administrador: lectura para API y Grafana.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO api_svc, grafana_ro;
EOSQL
