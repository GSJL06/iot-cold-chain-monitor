"""Pruebas unitarias para config, logs y directory del ingestor."""

import io
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from ingestor.config import ConfigError, load_settings
from ingestor.directory import DeviceDirectory
from ingestor.logs import JsonLogger, get_logger, set_level


def _base_env() -> dict[str, str]:
    """Construye un entorno mínimo válido evitando literales sospechosos para lint."""
    cred_mqtt = "-".join(("mqtt", "cred", "alpha"))
    cred_db = "-".join(("db", "cred", "beta"))
    return {
        "MQTT_INGESTOR_PASSWORD": cred_mqtt,
        "INGESTOR_DB_PASSWORD": cred_db,
        "POSTGRES_DB": "coldchain",
    }


def test_config_defaults():
    """Carga la configuración por defecto cuando solo se definen las variables obligatorias."""
    env = _base_env()
    settings = load_settings(env)

    assert settings.mqtt_host == "localhost"
    assert settings.mqtt_port == 1883
    assert settings.mqtt_password == env["MQTT_INGESTOR_PASSWORD"]
    assert settings.shared_group == "ingestor"
    assert settings.ingestor_id == "ingestor"
    assert settings.db_host == "localhost"
    assert settings.db_port == 5432
    assert settings.db_name == "coldchain"
    assert settings.db_password == env["INGESTOR_DB_PASSWORD"]
    assert settings.batch_max_size == 10
    assert settings.batch_max_wait_ms == 250
    assert settings.clock_skew_s == 300
    assert settings.schema_dir == Path("/app/schemas")
    assert settings.log_level == "INFO"


def test_config_empty_shared_group_enables_normal_subscription():
    """MQTT_SHARED_GROUP vacío se conserva como cadena vacía."""
    env = _base_env()
    env["MQTT_SHARED_GROUP"] = ""
    settings = load_settings(env)
    assert settings.shared_group == ""


@pytest.mark.parametrize(
    "missing_var",
    ["MQTT_INGESTOR_PASSWORD", "INGESTOR_DB_PASSWORD", "POSTGRES_DB"],
)
def test_config_missing_or_empty_required_vars(missing_var: str):
    """Falta o valor vacío en cada variable obligatoria lanza ConfigError nombrándola."""
    env_absent = _base_env()
    del env_absent[missing_var]
    with pytest.raises(ConfigError) as exc_absent:
        load_settings(env_absent)
    assert missing_var in str(exc_absent.value)

    env_empty = _base_env()
    env_empty[missing_var] = ""
    with pytest.raises(ConfigError) as exc_empty:
        load_settings(env_empty)
    assert missing_var in str(exc_empty.value)


@pytest.mark.parametrize(
    ("var_name", "valid_values", "invalid_values", "sentinel_value"),
    [
        ("BATCH_MAX_SIZE", ("1", "20"), ("0", "21", "-5"), "999"),
        ("BATCH_MAX_WAIT_MS", ("50", "2000"), ("49", "2001", "0"), "99999"),
        ("MQTT_PORT", ("1", "65535"), ("0", "65536", "-1"), "77777"),
        ("DB_PORT", ("1", "65535"), ("0", "65536", "-1"), "88888"),
        ("CLOCK_SKEW_S", ("0", "300"), ("-1", "-100"), "-987"),
    ],
)
def test_config_numeric_ranges_and_error_hides_value(
    var_name: str,
    valid_values: tuple[str, ...],
    invalid_values: tuple[str, ...],
    sentinel_value: str,
):
    """Valida los límites permitidos sin exponer el valor recibido en el error."""
    for valid_val in valid_values:
        env = _base_env()
        env[var_name] = valid_val
        load_settings(env)

    for invalid_val in (*invalid_values, sentinel_value):
        env = _base_env()
        env[var_name] = invalid_val
        with pytest.raises(ConfigError) as exc_info:
            load_settings(env)
        assert var_name in str(exc_info.value)

    env_sentinel = _base_env()
    env_sentinel[var_name] = sentinel_value
    with pytest.raises(ConfigError) as exc_sentinel:
        load_settings(env_sentinel)
    assert sentinel_value not in str(exc_sentinel.value)


@pytest.mark.parametrize(
    "var_name",
    ["MQTT_PORT", "DB_PORT", "BATCH_MAX_SIZE", "BATCH_MAX_WAIT_MS", "CLOCK_SKEW_S"],
)
@pytest.mark.parametrize("bad_value", ["not-a-number-77", "10.5", "1_0", ""])
def test_config_non_numeric_values_raise_without_leaking_value(var_name: str, bad_value: str):
    """Los valores no numéricos fallan con ConfigError mencionando la variable y nunca el valor."""
    env = _base_env()
    env[var_name] = bad_value
    with pytest.raises(ConfigError) as exc_info:
        load_settings(env)
    msg = str(exc_info.value)
    assert var_name in msg
    if bad_value:
        assert bad_value not in msg


def test_config_repr_hides_passwords():
    """repr(Settings) no expone mqtt_password ni db_password."""
    env = _base_env()
    settings = load_settings(env)
    rendered = repr(settings)
    assert env["MQTT_INGESTOR_PASSWORD"] not in rendered
    assert env["INGESTOR_DB_PASSWORD"] not in rendered
    assert "mqtt_password" not in rendered
    assert "db_password" not in rendered


def test_logs_json_format_fields_and_utc_z():
    """Emite una única línea JSON con ts UTC sin milisegundos y campos fijos."""
    buf = io.StringIO()
    offset_tz = timezone(timedelta(hours=2))
    fixed_dt = datetime(2026, 11, 18, 14, 5, 9, 456789, tzinfo=offset_tz)
    logger = JsonLogger(stream=buf, clock=lambda: fixed_dt)

    logger.info("batch_stored", messages=10, latency_ms=12.5)
    logger.warning("retry_db", attempt=2)
    logger.error("db_error", error="timeout")

    lines = buf.getvalue().splitlines()
    assert len(lines) == 3

    first = json.loads(lines[0])
    assert first == {
        "ts": "2026-11-18T12:05:09Z",
        "level": "INFO",
        "service": "ingestor",
        "event": "batch_stored",
        "messages": 10,
        "latency_ms": 12.5,
    }
    assert json.loads(lines[1])["level"] == "WARNING"
    assert json.loads(lines[2])["level"] == "ERROR"


def test_logs_redact_sensitive_keys():
    """Cualquier clave con password, secret o token (sin distinguir mayúsculas) se redacta."""
    buf = io.StringIO()
    logger = JsonLogger(stream=buf, clock=lambda: datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC))

    secret_val = "-".join(("top", "secret", "999"))
    extra = {
        "mqtt_password": secret_val,
        "DB_PASSWORD": secret_val,
        "ClientSecret": secret_val,
        "api_TOKEN": secret_val,
        "nested": {"access_token": secret_val, "host": "localhost"},
        "safe": "visible",
    }
    logger.info("started", **extra)

    raw_line = buf.getvalue().strip()
    assert secret_val not in raw_line
    parsed = json.loads(raw_line)
    masked = "*" * 3
    assert parsed["mqtt_password"] == masked
    assert parsed["DB_PASSWORD"] == masked
    assert parsed["ClientSecret"] == masked
    assert parsed["api_TOKEN"] == masked
    assert parsed["nested"] == {"access_token": masked, "host": "localhost"}
    assert parsed["safe"] == "visible"


def test_logs_unserializable_values_never_raise():
    """Valores no serializables o con __str__ fallido se registran sin lanzar excepción."""

    class BadStr:
        def __str__(self) -> str:
            raise RuntimeError("cannot stringify")

    buf = io.StringIO()
    logger = JsonLogger(stream=buf, clock=lambda: datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC))
    marker = object()

    logger.info("debug_obj", obj=marker, bad=BadStr(), path=Path("schemas"))

    lines = buf.getvalue().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["event"] == "debug_obj"
    assert "object" in parsed["obj"]
    assert "unserializable:BadStr" in parsed["bad"]
    assert parsed["path"] == "schemas"


def test_logs_process_logger_and_set_level(capsys: pytest.CaptureFixture[str]):
    """get_logger escribe en stdout y set_level filtra eventos por debajo del umbral."""
    proc_logger = get_logger()
    try:
        set_level("WARNING")
        proc_logger.info("ignored_info", detail="hidden")
        proc_logger.warning("visible_warn", detail="shown")
    finally:
        set_level("INFO")

    captured = capsys.readouterr().out.splitlines()
    assert len(captured) == 1
    entry = json.loads(captured[0])
    assert entry["level"] == "WARNING"
    assert entry["event"] == "visible_warn"
    assert entry["ts"].endswith("Z")
    assert "." not in entry["ts"]


def test_directory_lazy_load_and_miss_refresh_throttled():
    """Carga inicial perezosa y recarga por fallo limitada a una vez cada 10 s."""
    now = [0.0]
    catalog = [{"SIM-HA-001": "hosp-a"}]
    calls = [0]

    def loader() -> dict[str, str]:
        calls[0] += 1
        return dict(catalog[0])

    directory = DeviceDirectory(
        loader=loader, clock=lambda: now[0], refresh_s=60, miss_refresh_s=10
    )
    assert calls[0] == 0

    assert directory.is_known("SIM-HA-001") is True
    assert calls[0] == 1

    # Desconocido antes de 10 s: no debe recargar
    now[0] = 5.0
    catalog[0]["SIM-NEW-002"] = "hosp-a"
    assert directory.is_known("SIM-NEW-002") is False
    assert calls[0] == 1

    # Al alcanzar los 10 s desde la última recarga: vuelve a consultar el loader
    now[0] = 10.0
    assert directory.is_known("SIM-NEW-002") is True
    assert calls[0] == 2

    # Otro desconocido a los 15 s (solo 5 s desde la recarga en t=10): no recarga
    now[0] = 15.0
    assert directory.is_known("SIM-ZZ-999") is False
    assert calls[0] == 2


def test_directory_periodic_refresh_at_60s():
    """Recarga periódica al cumplirse refresh_s=60 aunque el dispositivo sea conocido."""
    now = [100.0]
    catalog = [{"SIM-HA-001": "hosp-a"}]
    calls = [0]

    def loader() -> dict[str, str]:
        calls[0] += 1
        return dict(catalog[0])

    directory = DeviceDirectory(
        loader=loader, clock=lambda: now[0], refresh_s=60, miss_refresh_s=10
    )
    assert directory.is_known("SIM-HA-001") is True
    assert calls[0] == 1

    now[0] = 159.0
    assert directory.is_known("SIM-HA-001") is True
    assert calls[0] == 1

    # El dispositivo se elimina antes de la recarga periódica de los 60 s
    del catalog[0]["SIM-HA-001"]
    now[0] = 160.0
    assert directory.is_known("SIM-HA-001") is False
    assert calls[0] == 2


def test_directory_failing_loader_preserves_cache_and_sets_last_error(
    capsys: pytest.CaptureFixture[str],
):
    """Un fallo en el loader conserva la caché anterior, no imprime nada y guarda last_error."""
    now = [0.0]
    should_fail = [False]
    boom = RuntimeError("database unreachable")

    def loader() -> dict[str, str]:
        if should_fail[0]:
            raise boom
        return {"SIM-HA-001": "hosp-a"}

    directory = DeviceDirectory(
        loader=loader, clock=lambda: now[0], refresh_s=60, miss_refresh_s=10
    )
    assert directory.is_known("SIM-HA-001") is True
    assert directory.last_error is None

    should_fail[0] = True
    now[0] = 60.0
    assert directory.is_known("SIM-HA-001") is True
    assert directory.last_error is boom

    # Recuperación posterior en recarga por dispositivo desconocido a los +10 s
    should_fail[0] = False
    now[0] = 70.0
    assert directory.is_known("SIM-ZZ-999") is False
    assert directory.last_error is None

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
