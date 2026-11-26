"""Pruebas de integración del almacén PostgreSQL con cambio a rol ingestor y ROLLBACK."""

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from ingestor.directory import DeviceDirectory
from ingestor.logs import JsonLogger
from ingestor.pipeline import BatchProcessor, Message, RejectLimiter
from ingestor.store import (
    apply_statuses,
    insert_readings,
    load_devices,
    touch_devices,
)
from ingestor.validation import ParsedReading, ParsedStatus, Schemas
from psycopg.conninfo import make_conninfo

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = REPO_ROOT / ".env"
SCHEMAS_DIR = REPO_ROOT / "schemas"
FIXED_NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)


class _ForceRollback(Exception):
    """Excepción interna para forzar el ROLLBACK al finalizar cada prueba."""


def _parse_simple_env(path: Path) -> dict[str, str]:
    """Lee variables clave=valor desde .env sin dependencias externas."""
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw_val = stripped.partition("=")
        k = key.strip()
        v = raw_val.strip()
        if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
            v = v[1:-1]
        result[k] = v
    return result


class RollbackDb:
    """Adaptador Db que ejecuta fn con SET ROLE ingestor dentro de un savepoint y ROLLBACK."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self.conn = conn

    def run(self, fn: Any) -> Any:
        """Ejecuta fn como rol ingestor en un savepoint y restaura el rol administrador."""
        try:
            with self.conn.transaction():
                with self.conn.cursor() as cur:
                    cur.execute("SET ROLE ingestor")
                return fn(self.conn)
        finally:
            with self.conn.cursor() as cur:
                cur.execute("RESET ROLE")


@pytest.fixture
def rollback_db() -> Iterator[RollbackDb]:
    """Conecta como administrador y revierte toda escritura al terminar el test."""
    env_file = _parse_simple_env(ENV_PATH)
    host = env_file.get("DB_HOST") or os.environ.get("DB_HOST", "localhost")
    if host == "localhost":
        host = "127.0.0.1"
    port = int(env_file.get("DB_PORT") or os.environ.get("DB_PORT", "5432"))
    dbname = env_file.get("POSTGRES_DB") or os.environ.get("POSTGRES_DB", "")
    user = env_file.get("POSTGRES_USER") or os.environ.get("POSTGRES_USER", "")
    password = env_file.get("POSTGRES_PASSWORD") or os.environ.get("POSTGRES_PASSWORD", "")

    if not dbname or not user or not password:
        pytest.skip("Missing POSTGRES_DB, POSTGRES_USER or POSTGRES_PASSWORD in .env/environment")

    conninfo = make_conninfo(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
    )
    try:
        conn = psycopg.connect(conninfo, connect_timeout=3)
    except (psycopg.OperationalError, OSError) as exc:
        pytest.skip(f"PostgreSQL unavailable for integration tests: {type(exc).__name__}")

    with conn:
        try:
            with conn.transaction():
                yield RollbackDb(conn)
                raise _ForceRollback
        except _ForceRollback:
            pass


def _make_processor(db: RollbackDb) -> BatchProcessor:
    """Construye BatchProcessor conectado al directorio real de la base de datos."""
    schemas = Schemas.load(SCHEMAS_DIR)
    directory = DeviceDirectory(loader=lambda: db.run(load_devices), clock=lambda: 0.0)
    logger = JsonLogger(stream=None, clock=lambda: FIXED_NOW)
    return BatchProcessor(
        db=db,
        directory=directory,
        schemas=schemas,
        limiter=RejectLimiter(clock=lambda: 0.0),
        now=lambda: FIXED_NOW,
        logger=logger,
    )


def _valid_reading_bytes(device_id: str, ts: str, seq: int, **extra: Any) -> bytes:
    """Genera un payload JSON válido telemetry.v1 para pruebas de integración."""
    data: dict[str, Any] = {
        "schema": "telemetry.v1",
        "device_id": device_id,
        "ts": ts,
        "seq": seq,
        "temp_c": 4.2,
        "humidity_pct": 51.0,
        "rssi_dbm": -84,
        "battery_pct": 99.0,
        "on_backup_power": False,
    }
    data.update(extra)
    return json.dumps(data).encode("utf-8")


def test_five_rejection_reasons_stored_in_rejected_messages(rollback_db: RollbackDb):
    """Los 5 motivos de rechazo llegan a rejected_messages (unknown_device con ID inexistente)."""
    processor = _make_processor(rollback_db)
    future_ts = (FIXED_NOW + timedelta(seconds=360)).strftime("%Y-%m-%dT%H:%M:%SZ")

    messages = [
        Message(
            topic="coldchain/v1/hosp-a/SIM-HA-001/telemetry",
            payload=_valid_reading_bytes("SIM-HA-001", "2026-11-18T12:00:00Z", 1, temp_c=150.0),
            mid=1,
            qos=1,
        ),
        Message(
            topic="coldchain/v1/hosp-a/SIM-HA-001/telemetry",
            payload=_valid_reading_bytes(
                "SIM-HA-001", "2026-11-18T12:00:00Z", 2, schema="telemetry.v2"
            ),
            mid=2,
            qos=1,
        ),
        Message(
            topic="coldchain/v1/hosp-a/SIM-HA-001/telemetry",
            payload=_valid_reading_bytes("SIM-HA-002", "2026-11-18T12:00:00Z", 3),
            mid=3,
            qos=1,
        ),
        Message(
            topic="coldchain/v1/hosp-a/SIM-NOPE-999/telemetry",
            payload=_valid_reading_bytes("SIM-NOPE-999", "2026-11-18T12:00:00Z", 4),
            mid=4,
            qos=1,
        ),
        Message(
            topic="coldchain/v1/hosp-a/SIM-HA-001/telemetry",
            payload=_valid_reading_bytes("SIM-HA-001", future_ts, 5),
            mid=5,
            qos=1,
        ),
    ]

    result = processor.process(messages)
    assert result.rejected == 5

    with rollback_db.conn.cursor() as cur:
        cur.execute("SELECT reason FROM rejected_messages ORDER BY id DESC LIMIT 5")
        reasons = {row[0] for row in cur.fetchall()}

    assert reasons == {
        "schema_invalid",
        "unsupported_schema",
        "topic_mismatch",
        "unknown_device",
        "clock_skew",
    }


def test_deduplication_same_batch_and_different_batches(rollback_db: RollbackDb):
    """El mismo (device_id, ts) en el mismo lote y en otro posterior deja 1 fila y 1 duplicado."""
    processor = _make_processor(rollback_db)
    ts_iso = "2026-11-18T12:00:00Z"
    payload = _valid_reading_bytes("SIM-HA-001", ts_iso, seq=101)
    topic = "coldchain/v1/hosp-a/SIM-HA-001/telemetry"

    res_same = processor.process(
        [
            Message(topic=topic, payload=payload, mid=1, qos=1),
            Message(topic=topic, payload=payload, mid=2, qos=1),
        ]
    )
    assert res_same.readings == 2
    assert res_same.inserted == 1
    assert res_same.duplicates == 1

    res_next = processor.process([Message(topic=topic, payload=payload, mid=3, qos=1)])
    assert res_next.readings == 1
    assert res_next.inserted == 0
    assert res_next.duplicates == 1

    with rollback_db.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM readings WHERE device_id = %s AND ts = %s",
            ("SIM-HA-001", FIXED_NOW),
        )
        assert cur.fetchone() == (1,)


def test_ultra_frozen_batch_all_none_humidity(rollback_db: RollbackDb):
    """Un lote de ultracongeladores con humidity_pct=None se inserta sin error de tipo."""
    processor = _make_processor(rollback_db)
    p1 = json.dumps(
        {
            "schema": "telemetry.v1",
            "device_id": "SIM-LC-003",
            "ts": "2026-11-18T12:00:00Z",
            "seq": 10,
            "temp_c": -80.5,
            "rssi_dbm": -75,
            "battery_pct": 85.0,
            "on_backup_power": False,
        }
    ).encode("utf-8")
    p2 = json.dumps(
        {
            "schema": "telemetry.v1",
            "device_id": "SIM-LC-004",
            "ts": "2026-11-18T12:00:00Z",
            "seq": 11,
            "temp_c": -79.8,
            "rssi_dbm": -78,
            "battery_pct": 88.0,
            "on_backup_power": True,
        }
    ).encode("utf-8")

    res = processor.process(
        [
            Message(topic="coldchain/v1/lab-c/SIM-LC-003/telemetry", payload=p1, mid=1, qos=1),
            Message(topic="coldchain/v1/lab-c/SIM-LC-004/telemetry", payload=p2, mid=2, qos=1),
        ]
    )
    assert res.inserted == 2

    with rollback_db.conn.cursor() as cur:
        cur.execute(
            "SELECT humidity_pct FROM readings WHERE device_id IN ('SIM-LC-003', 'SIM-LC-004') "
            "AND ts = %s ORDER BY device_id",
            (FIXED_NOW,),
        )
        assert cur.fetchall() == [(None,), (None,)]


def test_payload_with_nul_stored_with_replacement_char(rollback_db: RollbackDb):
    """Un payload con byte NUL se almacena sustituido por U+FFFD en rejected_messages."""
    processor = _make_processor(rollback_db)
    topic = "coldchain/v1/hosp-a/SIM-HA-001/telemetry"
    res = processor.process([Message(topic=topic, payload=b"bad\x00payload", mid=1, qos=1)])
    assert res.rejected == 1

    with rollback_db.conn.cursor() as cur:
        cur.execute("SELECT payload FROM rejected_messages ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "bad\ufffdpayload"
        assert "\x00" not in row[0]


def test_insert_readings_seq_overflow_raises_data_error(rollback_db: RollbackDb):
    """Pasar seq=2**40 directamente a insert_readings provoca psycopg.errors.DataError."""
    bad_reading = ParsedReading(
        device_id="SIM-HA-001",
        ts=FIXED_NOW,
        seq=2**40,
        temp_c=4.5,
        humidity_pct=50.0,
        rssi_dbm=-80,
        battery_pct=95.0,
        on_backup_power=False,
    )
    with pytest.raises(psycopg.errors.DataError):
        rollback_db.run(lambda conn: insert_readings(conn, [bad_reading]))


def test_apply_statuses_order_and_touch_devices_creates_missing_row(rollback_db: RollbackDb):
    """apply_statuses respeta el orden recibido y touch_devices crea la fila si no existe."""
    t1 = FIXED_NOW
    t2 = FIXED_NOW + timedelta(seconds=10)
    t3 = FIXED_NOW + timedelta(seconds=20)
    statuses = [
        ParsedStatus(device_id="SIM-HA-001", state="online", ts=t1),
        ParsedStatus(device_id="SIM-HA-001", state="offline", ts=t2),
        ParsedStatus(device_id="SIM-HA-001", state="online", ts=t3),
    ]
    rollback_db.run(lambda conn: apply_statuses(conn, statuses))

    with rollback_db.conn.cursor() as cur:
        cur.execute(
            "SELECT state::text, last_status_ts FROM device_status WHERE device_id = %s",
            ("SIM-HA-001",),
        )
        assert cur.fetchone() == ("online", t3)

    # Verifica creación de fila ausente mediante touch_devices
    with rollback_db.conn.cursor() as cur:
        cur.execute("DELETE FROM device_status WHERE device_id = %s", ("SIM-HA-002",))

    rollback_db.run(lambda conn: touch_devices(conn, ["SIM-HA-002", "SIM-HA-002"]))

    with rollback_db.conn.cursor() as cur:
        cur.execute(
            "SELECT state::text, last_seen IS NOT NULL FROM device_status WHERE device_id = %s",
            ("SIM-HA-002",),
        )
        assert cur.fetchone() == ("online", True)


def test_ingestor_role_cannot_read_or_delete(rollback_db: RollbackDb):
    """Documenta el mínimo privilegio: ingestor no puede leer rechazos ni borrar device_status."""

    def _select_rejected(conn: psycopg.Connection[Any]) -> None:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM rejected_messages LIMIT 1")

    def _delete_status(conn: psycopg.Connection[Any]) -> None:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM device_status WHERE device_id = %s", ("SIM-HA-001",))

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        rollback_db.run(_select_rejected)

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        rollback_db.run(_delete_status)
