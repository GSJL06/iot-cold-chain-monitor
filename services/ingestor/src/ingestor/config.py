"""Carga y validación de la configuración del ingestor desde variables de entorno."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

_INT_RE = re.compile(r"[+-]?\d+")
_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ConfigError(Exception):
    """Excepción para variables de entorno ausentes o fuera de rango."""


@dataclass(frozen=True)
class Settings:
    """Parámetros de configuración validados para el servicio ingestor."""

    mqtt_host: str
    mqtt_port: int
    mqtt_password: str = field(repr=False)
    shared_group: str
    ingestor_id: str
    db_host: str
    db_port: int
    db_name: str
    db_password: str = field(repr=False)
    batch_max_size: int
    batch_max_wait_ms: int
    clock_skew_s: int
    schema_dir: Path
    log_level: str


def _require_non_empty(env: Mapping[str, str], name: str, *, strip_value: bool) -> str:
    """Obtiene una variable obligatoria comprobando que exista y no esté vacía."""
    raw = env.get(name)
    if raw is None or not raw.strip():
        raise ConfigError(f"Missing required environment variable: {name}")
    return raw.strip() if strip_value else raw


def _optional_non_empty(env: Mapping[str, str], name: str, default: str) -> str:
    """Obtiene una variable opcional rechazando cadenas vacías si está presente."""
    if name not in env:
        return default
    value = env[name].strip()
    if not value:
        raise ConfigError(f"Environment variable {name} cannot be empty")
    return value


def _parse_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Parsea un entero desde el entorno y valida sus límites sin exponer su valor."""
    if name not in env:
        return default
    raw = env[name].strip()
    if not _INT_RE.fullmatch(raw):
        raise ConfigError(f"Environment variable {name} must be an integer")
    value = int(raw)
    if minimum is not None and maximum is not None and not (minimum <= value <= maximum):
        raise ConfigError(f"Environment variable {name} must be between {minimum} and {maximum}")
    if minimum is not None and value < minimum:
        raise ConfigError(f"Environment variable {name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"Environment variable {name} must be <= {maximum}")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Construye y valida Settings a partir de un mapa de variables de entorno."""
    source = os.environ if env is None else env

    mqtt_password = _require_non_empty(source, "MQTT_INGESTOR_PASSWORD", strip_value=False)
    db_password = _require_non_empty(source, "INGESTOR_DB_PASSWORD", strip_value=False)
    db_name = _require_non_empty(source, "POSTGRES_DB", strip_value=True)

    shared_group = (
        source["MQTT_SHARED_GROUP"].strip() if "MQTT_SHARED_GROUP" in source else "ingestor"
    )

    log_level = _optional_non_empty(source, "LOG_LEVEL", "INFO").upper()
    if log_level not in _VALID_LOG_LEVELS:
        raise ConfigError("Environment variable LOG_LEVEL has an unsupported level")

    return Settings(
        mqtt_host=_optional_non_empty(source, "MQTT_HOST", "localhost"),
        mqtt_port=_parse_int(source, "MQTT_PORT", 1883, minimum=1, maximum=65535),
        mqtt_password=mqtt_password,
        shared_group=shared_group,
        ingestor_id=_optional_non_empty(source, "INGESTOR_ID", "ingestor"),
        db_host=_optional_non_empty(source, "DB_HOST", "localhost"),
        db_port=_parse_int(source, "DB_PORT", 5432, minimum=1, maximum=65535),
        db_name=db_name,
        db_password=db_password,
        batch_max_size=_parse_int(source, "BATCH_MAX_SIZE", 10, minimum=1, maximum=20),
        batch_max_wait_ms=_parse_int(source, "BATCH_MAX_WAIT_MS", 250, minimum=50, maximum=2000),
        clock_skew_s=_parse_int(source, "CLOCK_SKEW_S", 300, minimum=0),
        schema_dir=Path(_optional_non_empty(source, "SCHEMA_DIR", "/app/schemas")),
        log_level=log_level,
    )
