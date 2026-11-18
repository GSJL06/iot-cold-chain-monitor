-- ColdChain Monitor — esquema base (Fase 4A)
-- Modelo de datos: docs/architecture.md, sección 7.
-- Estos scripts solo se ejecutan cuando el volumen de datos está vacío.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TYPE storage_class AS ENUM ('REFRIGERATED', 'FROZEN', 'ULTRA_FROZEN');
CREATE TYPE alert_severity AS ENUM ('INFO', 'WARNING', 'CRITICAL');
CREATE TYPE alert_state AS ENUM ('OPEN', 'ACKNOWLEDGED', 'RESOLVED');
CREATE TYPE device_state AS ENUM ('online', 'offline');

CREATE TABLE sites (
    site_id text PRIMARY KEY CHECK (site_id ~ '^[a-z0-9][a-z0-9-]{1,23}$'),
    name    text NOT NULL,
    city    text NOT NULL
);

CREATE TABLE devices (
    device_id     text PRIMARY KEY CHECK (device_id ~ '^[A-Z0-9][A-Z0-9-]{3,31}$'),
    site_id       text NOT NULL REFERENCES sites (site_id),
    label         text NOT NULL,
    storage_class storage_class NOT NULL,
    min_c         numeric(5, 1) NOT NULL,
    max_c         numeric(5, 1) NOT NULL,
    fw_version    text,
    CHECK (min_c < max_c)
);

CREATE TABLE readings (
    ts              timestamptz NOT NULL,
    device_id       text        NOT NULL REFERENCES devices (device_id),
    received_at     timestamptz NOT NULL DEFAULT now(),
    seq             integer     NOT NULL CHECK (seq >= 0),
    temp_c          double precision NOT NULL CHECK (temp_c BETWEEN -100 AND 60),
    humidity_pct    double precision CHECK (humidity_pct BETWEEN 0 AND 100),
    rssi_dbm        smallint    NOT NULL CHECK (rssi_dbm BETWEEN -140 AND 0),
    battery_pct     real        NOT NULL CHECK (battery_pct BETWEEN 0 AND 100),
    on_backup_power boolean     NOT NULL,
    PRIMARY KEY (device_id, ts)
);

-- Hypertable: TimescaleDB la divide en trozos (chunks) de 1 día.
SELECT create_hypertable('readings', by_range('ts', INTERVAL '1 day'));

CREATE TABLE device_status (
    device_id      text PRIMARY KEY REFERENCES devices (device_id),
    state          device_state NOT NULL,
    last_seen      timestamptz,
    last_status_ts timestamptz
);

CREATE TABLE alerts (
    alert_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    device_id        text           NOT NULL REFERENCES devices (device_id),
    rule_id          text           NOT NULL CHECK (rule_id IN ('R01', 'R02', 'R03', 'R04', 'R05', 'R06')),
    severity         alert_severity NOT NULL,
    state            alert_state    NOT NULL DEFAULT 'OPEN',
    opened_at        timestamptz    NOT NULL DEFAULT now(),
    acked_at         timestamptz,
    resolved_at      timestamptz,
    last_notified_at timestamptz,
    context          jsonb          NOT NULL DEFAULT '{}'::jsonb,
    CHECK ((state = 'RESOLVED') = (resolved_at IS NOT NULL)),
    CHECK (state <> 'ACKNOWLEDGED' OR acked_at IS NOT NULL)
);

-- Una sola alerta activa por par (dispositivo, regla): lo garantiza la base de datos.
CREATE UNIQUE INDEX alerts_one_active_per_rule
    ON alerts (device_id, rule_id)
    WHERE state <> 'RESOLVED';

CREATE INDEX alerts_state_opened_idx ON alerts (state, opened_at DESC);

CREATE TABLE rejected_messages (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    received_at timestamptz NOT NULL DEFAULT now(),
    topic       text        NOT NULL,
    payload     text        NOT NULL,
    reason      text        NOT NULL CHECK (reason IN (
        'schema_invalid', 'unsupported_schema', 'topic_mismatch', 'unknown_device', 'clock_skew'
    ))
);

CREATE INDEX rejected_messages_received_idx ON rejected_messages (received_at DESC);

CREATE TABLE kpi_daily (
    device_id         text NOT NULL REFERENCES devices (device_id),
    day               date NOT NULL,
    time_in_range_pct real,
    mkt_c             real,
    excursion_count   integer,
    max_excursion_min real,
    PRIMARY KEY (device_id, day)
);

CREATE TABLE anomaly_scores (
    device_id     text        NOT NULL REFERENCES devices (device_id),
    ts            timestamptz NOT NULL,
    score         real        NOT NULL,
    model_version text        NOT NULL,
    PRIMARY KEY (device_id, ts)
);
