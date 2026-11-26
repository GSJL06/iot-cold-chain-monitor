"""Utilidades compartidas de lectura de configuración y conexión solo lectura a BD."""

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_env(path: str | Path = ".env") -> dict[str, str]:
    """Lee un archivo .env simple (KEY=VALUE) ignorando comentarios y líneas vacías."""
    env_path = Path(path)
    if not env_path.is_absolute() and not env_path.is_file():
        env_path = REPO_ROOT / env_path
    env_vars: dict[str, str] = {}
    if not env_path.is_file():
        return env_vars
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, val = stripped.split("=", 1)
            cleaned_val = val.strip()
            if len(cleaned_val) >= 2 and (
                (cleaned_val[0] == '"' and cleaned_val[-1] == '"')
                or (cleaned_val[0] == "'" and cleaned_val[-1] == "'")
            ):
                cleaned_val = cleaned_val[1:-1]
            env_vars[key.strip()] = cleaned_val
    return env_vars


def get_env_value(env: Mapping[str, str], name: str, default: str = "") -> str:
    """Obtiene una variable priorizando el entorno del proceso sobre el mapa .env."""
    raw = os.environ.get(name)
    if raw is not None and raw.strip():
        return raw.strip()
    return env.get(name, default).strip()


def resolve_local_host(host: str) -> str:
    """Mapea localhost a 127.0.0.1 para evitar retardos IPv6 frente a Docker Desktop."""
    return "127.0.0.1" if host == "localhost" else host


def db_connect(
    env_path: str | Path = ".env",
    *,
    connect_timeout: int = 5,
) -> psycopg.Connection[Any]:
    """Abre una conexión PostgreSQL de solo lectura con el rol grafana_ro."""
    env = load_env(env_path)
    host = resolve_local_host(get_env_value(env, "DB_HOST", "localhost") or "localhost")
    port = int(get_env_value(env, "DB_PORT", "5432") or "5432")
    dbname = get_env_value(env, "POSTGRES_DB")
    password = get_env_value(env, "GRAFANA_RO_DB_PASSWORD")

    if not dbname or not password:
        raise RuntimeError("Missing POSTGRES_DB or GRAFANA_RO_DB_PASSWORD in .env or environment")

    conninfo = make_conninfo(
        host=host,
        port=port,
        dbname=dbname,
        user="grafana_ro",
        password=password,
    )
    conn = psycopg.connect(conninfo, autocommit=True, connect_timeout=connect_timeout)
    conn.read_only = True
    return conn


def clock_offset_s(conn: psycopg.Connection[Any], samples: int = 5) -> float:
    """Estima (hora BD - hora local UTC) en segundos promediando 5 lecturas."""
    offsets: list[float] = []
    with conn.cursor() as cur:
        for _ in range(samples):
            t_before = datetime.now(UTC)
            cur.execute("SELECT clock_timestamp()")
            row = cur.fetchone()
            t_after = datetime.now(UTC)
            if row is None or row[0] is None:
                raise RuntimeError("Failed to query clock_timestamp() from database")
            raw_ts = row[0]
            db_ts = raw_ts.replace(tzinfo=UTC) if raw_ts.tzinfo is None else raw_ts.astimezone(UTC)
            local_mid = t_before + (t_after - t_before) / 2
            offsets.append((db_ts - local_mid).total_seconds())
    return sum(offsets) / len(offsets)


def truncate_ms(ts: datetime) -> datetime:
    """Normaliza un datetime a UTC y trunca los microsegundos a milisegundos."""
    ts_utc = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
    ms = ts_utc.microsecond // 1000
    return ts_utc.replace(microsecond=ms * 1000)


def format_ts_ms(ts: datetime) -> str:
    """Formatea un datetime UTC con milisegundos y sufijo Z."""
    truncated = truncate_ms(ts)
    ms = truncated.microsecond // 1000
    return f"{truncated:%Y-%m-%dT%H:%M:%S}.{ms:03d}Z"
