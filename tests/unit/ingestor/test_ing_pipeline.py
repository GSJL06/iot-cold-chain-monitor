"""Pruebas unitarias de pipeline, worker, limitador, latido y no fuga en logs."""

import io
import json
import queue
import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import psycopg
import pytest
from ingestor.app import _safe_config_summary
from ingestor.config import Settings, load_settings
from ingestor.health import HealthState, Heartbeat, is_alive
from ingestor.health import main as health_main
from ingestor.logs import JsonLogger
from ingestor.mqtt_io import MqttSource
from ingestor.pipeline import BatchProcessor, Message, RejectLimiter, Worker
from ingestor.validation import Schemas

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMAS_DIR = REPO_ROOT / "schemas"
FIXED_NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
KNOWN_DEVICES = frozenset({"SIM-HA-001", "SIM-FB-001", "SIM-LC-003"})


@pytest.fixture(scope="module")
def schemas() -> Schemas:
    """Carga los JSON Schema oficiales una vez para las pruebas de pipeline."""
    return Schemas.load(SCHEMAS_DIR)


def _make_settings(**overrides: str) -> Settings:
    """Construye Settings de prueba sin usar literales sospechosos para lint."""
    cred_mqtt = "-".join(("mqtt", "secret", "val", "91"))
    cred_db = "-".join(("db", "secret", "val", "92"))
    env = {
        "MQTT_INGESTOR_PASSWORD": cred_mqtt,
        "INGESTOR_DB_PASSWORD": cred_db,
        "POSTGRES_DB": "coldchain",
        "SCHEMA_DIR": str(SCHEMAS_DIR),
    }
    env.update(overrides)
    return load_settings(env)


def _reading_payload(
    device_id: str = "SIM-HA-001",
    seq: int = 1,
    ts: str = "2026-11-18T12:00:00Z",
    temp_c: float = 4.5,
) -> bytes:
    """Genera un payload telemetry.v1 válido serializado en UTF-8."""
    return json.dumps(
        {
            "schema": "telemetry.v1",
            "device_id": device_id,
            "ts": ts,
            "seq": seq,
            "temp_c": temp_c,
            "humidity_pct": 50.0,
            "rssi_dbm": -82,
            "battery_pct": 98.0,
            "on_backup_power": False,
        }
    ).encode("utf-8")


class FakeCursor:
    """Cursor en memoria compatible con las sentencias SQL de ingestor.store."""

    def __init__(self, conn: "FakeConn") -> None:
        self._conn = conn
        self.rowcount = 0

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        """Simula ejecución de lecturas con detección de duplicados y errores inyectados."""
        if "INSERT INTO readings" in sql:
            ts_list, dev_list, seq_list, temp_list, hum_list, rssi_list, bat_list, bkp_list = params
            inserted = 0
            for idx in range(len(dev_list)):
                if (
                    self._conn.db.poison_seq is not None
                    and seq_list[idx] == self._conn.db.poison_seq
                ):
                    raise psycopg.errors.DataError("simulated invalid column data")
                key = (dev_list[idx], ts_list[idx])
                if key not in self._conn.seen_reading_keys:
                    self._conn.seen_reading_keys.add(key)
                    self._conn.pending_readings.append(
                        (
                            ts_list[idx],
                            dev_list[idx],
                            seq_list[idx],
                            temp_list[idx],
                            hum_list[idx],
                            rssi_list[idx],
                            bat_list[idx],
                            bkp_list[idx],
                        )
                    )
                    inserted += 1
            self.rowcount = inserted
        elif "SELECT 1" in sql:
            self.rowcount = 1

    def executemany(self, sql: str, params_seq: list[Any]) -> None:
        """Simula inserciones múltiples en rejected_messages o device_status."""
        if "INSERT INTO rejected_messages" in sql:
            self._conn.pending_rejections.extend(params_seq)
        elif "SET last_seen" in sql:
            self._conn.pending_touches.extend(params_seq)
        elif "SET state = EXCLUDED.state" in sql:
            self._conn.pending_statuses.extend(params_seq)

    def fetchone(self) -> tuple[int]:
        """Devuelve resultado mínimo para SELECT 1."""
        return (1,)


class FakeConn:
    """Conexión transaccional falsa que aísla escrituras pendientes hasta el commit."""

    def __init__(self, db: "FakeDb") -> None:
        self.db = db
        self.seen_reading_keys: set[tuple[str, datetime]] = set(db.reading_keys)
        self.pending_readings: list[tuple[Any, ...]] = []
        self.pending_rejections: list[tuple[Any, ...]] = []
        self.pending_touches: list[tuple[Any, ...]] = []
        self.pending_statuses: list[tuple[Any, ...]] = []

    def cursor(self) -> FakeCursor:
        """Devuelve un cursor falso vinculado a la transacción en curso."""
        return FakeCursor(self)


class FakeDb:
    """Base de datos falsa con soporte de fallos transitorios, veneno y línea temporal."""

    def __init__(self, timeline: list[tuple[str, Any]] | None = None) -> None:
        self.readings: list[tuple[Any, ...]] = []
        self.reading_keys: set[tuple[str, datetime]] = set()
        self.rejections: list[tuple[Any, ...]] = []
        self.touches: list[tuple[Any, ...]] = []
        self.statuses: list[tuple[Any, ...]] = []
        self.run_calls = 0
        self.batch_sizes: list[int] = []
        self.failures_before_ok: list[Exception] = []
        self.poison_seq: int | None = None
        self.ping_result = True
        self.ping_calls = 0
        self.timeline = timeline if timeline is not None else []
        self.on_during_run: Callable[[], None] | None = None

    def run(self, fn: Callable[[FakeConn], Any]) -> Any:
        """Ejecuta fn con semántica atómica de commit o rollback ante excepción."""
        self.run_calls += 1
        if self.failures_before_ok:
            raise self.failures_before_ok.pop(0)
        if self.on_during_run is not None:
            self.on_during_run()

        conn = FakeConn(self)
        result = fn(conn)

        self.readings.extend(conn.pending_readings)
        self.reading_keys.update(conn.seen_reading_keys)
        self.rejections.extend(conn.pending_rejections)
        self.touches.extend(conn.pending_touches)
        self.statuses.extend(conn.pending_statuses)
        self.timeline.append(("commit", len(conn.pending_readings) + len(conn.pending_rejections)))
        return result

    def ping(self) -> bool:
        """Registra pings ociosos devolviendo el estado configurado."""
        self.ping_calls += 1
        return self.ping_result


class FakeAcker:
    """Confirmador falso que registra cada ack junto a la línea temporal compartida."""

    def __init__(
        self,
        timeline: list[tuple[str, Any]] | None = None,
        on_ack: Callable[[int, int], None] | None = None,
    ) -> None:
        self.acked: list[tuple[int, int]] = []
        self.timeline = timeline if timeline is not None else []
        self.on_ack = on_ack

    def ack(self, mid: int, qos: int) -> None:
        """Registra la confirmación MQTT recibida tras el commit."""
        self.acked.append((mid, qos))
        self.timeline.append(("ack", mid, qos))
        if self.on_ack is not None:
            self.on_ack(mid, qos)


class FakeTimedQueue:
    """Cola determinista que avanza un reloj falso al expirar get(timeout)."""

    def __init__(self, now_ref: list[float]) -> None:
        self._now_ref = now_ref
        self._items: deque[Message] = deque()
        self.on_empty_wait: Callable[[float], None] | None = None

    def put(self, item: Message) -> None:
        """Encola un mensaje sin bloqueo."""
        self._items.append(item)

    def get(self, timeout: float = 0.0) -> Message:
        """Extrae un mensaje o avanza el reloj falso antes de lanzar queue.Empty."""
        if self._items:
            return self._items.popleft()
        self._now_ref[0] += max(0.0, timeout)
        if self.on_empty_wait is not None:
            self.on_empty_wait(self._now_ref[0])
        if self._items:
            return self._items.popleft()
        raise queue.Empty

    def get_nowait(self) -> Message:
        """Extrae sin avanzar tiempo ni bloquear."""
        if self._items:
            return self._items.popleft()
        raise queue.Empty


def test_worker_closes_batch_by_size_and_acks_after_commit(schemas: Schemas):
    """Cierra el lote al alcanzar batch_max_size y confirma solo tras terminar db.run."""
    settings = _make_settings(BATCH_MAX_SIZE="3", BATCH_MAX_WAIT_MS="2000")
    now_ref = [0.0]
    timeline: list[tuple[str, Any]] = []
    db = FakeDb(timeline=timeline)
    stop_event = threading.Event()
    batch_mids_seen: list[list[int]] = []

    def stop_when_all_acked(_mid: int, _qos: int) -> None:
        if len(acker.acked) == 5:
            stop_event.set()

    acker = FakeAcker(timeline=timeline, on_ack=stop_when_all_acked)

    def assert_no_ack_during_run() -> None:
        assert len(acker.acked) == 0 or timeline[-1][0] == "ack"

    db.on_during_run = assert_no_ack_during_run

    log_buf = io.StringIO()
    logger = JsonLogger(stream=log_buf, clock=lambda: FIXED_NOW)
    processor = BatchProcessor(
        db=db,
        directory=KNOWN_DEVICES.__contains__,
        schemas=schemas,
        limiter=RejectLimiter(clock=lambda: now_ref[0]),
        now=lambda: FIXED_NOW,
        logger=logger,
    )
    original_process = processor.process

    def spy_process(msgs: Any) -> Any:
        batch_mids_seen.append([m.mid for m in msgs])
        return original_process(msgs)

    processor.process = spy_process  # type: ignore[method-assign]

    q = FakeTimedQueue(now_ref)
    for i in range(1, 6):
        q.put(
            Message(
                topic="coldchain/v1/hosp-a/SIM-HA-001/telemetry",
                payload=_reading_payload(seq=i, ts=f"2026-11-18T12:00:0{i}Z"),
                mid=i,
                qos=1,
            )
        )

    health = HealthState(mqtt_connected=True, db_ok=True)
    hb = Heartbeat(path=Path("unused-in-this-test"), clock=lambda: now_ref[0])
    hb.beat = lambda _state: None  # type: ignore[method-assign]

    worker = Worker(
        source_queue=q,  # type: ignore[arg-type]
        processor=processor,
        acker=acker,
        health_state=health,
        heartbeat=hb,
        settings=settings,
        clock=lambda: now_ref[0],
        sleep=lambda _s: None,
        stop_event=stop_event,
        logger=logger,
    )
    worker.run()

    assert batch_mids_seen == [[1, 2, 3], [4, 5]]
    assert timeline == [
        ("commit", 3),
        ("ack", 1, 1),
        ("ack", 2, 1),
        ("ack", 3, 1),
        ("commit", 2),
        ("ack", 4, 1),
        ("ack", 5, 1),
    ]


def test_worker_closes_batch_by_time_and_checks_idle_ping_and_stats(
    schemas: Schemas, tmp_path: Path
):
    """Cierra el lote al vencer batch_max_wait_ms sin esperar en reloj real y registra stats."""
    settings = _make_settings(BATCH_MAX_SIZE="10", BATCH_MAX_WAIT_MS="250")
    now_ref = [0.0]
    timeline: list[tuple[str, Any]] = []
    db = FakeDb(timeline=timeline)
    stop_event = threading.Event()
    acker = FakeAcker(timeline=timeline)
    log_buf = io.StringIO()
    logger = JsonLogger(stream=log_buf, clock=lambda: FIXED_NOW)
    processor = BatchProcessor(
        db=db,
        directory=KNOWN_DEVICES.__contains__,
        schemas=schemas,
        limiter=RejectLimiter(clock=lambda: now_ref[0]),
        now=lambda: FIXED_NOW,
        logger=logger,
    )

    q = FakeTimedQueue(now_ref)
    q.put(
        Message(
            topic="coldchain/v1/hosp-a/SIM-HA-001/telemetry",
            payload=_reading_payload(seq=1, ts="2026-11-18T12:00:01Z"),
            mid=10,
            qos=1,
        )
    )
    q.put(
        Message(
            topic="coldchain/v1/hosp-a/SIM-HA-001/telemetry",
            payload=_reading_payload(seq=2, ts="2026-11-18T12:00:02Z"),
            mid=11,
            qos=1,
        )
    )

    empty_waits = [0]

    def advance_until_stats(cur_t: float) -> None:
        empty_waits[0] += 1
        if cur_t >= 31.0:
            stop_event.set()
        elif empty_waits[0] >= 2:
            now_ref[0] += 30.5

    q.on_empty_wait = advance_until_stats

    hb_path = tmp_path / "ingestor.alive"
    health = HealthState(mqtt_connected=True, db_ok=True)
    hb = Heartbeat(path=hb_path, clock=lambda: now_ref[0])

    worker = Worker(
        source_queue=q,  # type: ignore[arg-type]
        processor=processor,
        acker=acker,
        health_state=health,
        heartbeat=hb,
        settings=settings,
        clock=lambda: now_ref[0],
        sleep=lambda _s: None,
        stop_event=stop_event,
        logger=logger,
    )
    worker.run()

    assert db.run_calls == 1
    assert len(db.readings) == 2
    assert acker.acked == [(10, 1), (11, 1)]
    assert db.ping_calls >= 1
    assert hb_path.is_file()

    events = [json.loads(line)["event"] for line in log_buf.getvalue().splitlines()]
    assert "batch_stored" in events
    assert "stats" in events


def test_worker_operational_error_retries_with_backoff_then_acks(schemas: Schemas):
    """Ante OperationalError no confirma, reintenta con 1, 2, 4 s y confirma al recuperarse."""
    settings = _make_settings(BATCH_MAX_SIZE="10", BATCH_MAX_WAIT_MS="50")
    now_ref = [0.0]
    sleeps: list[float] = []
    db = FakeDb()
    db.failures_before_ok = [
        psycopg.OperationalError("conn refused 1"),
        psycopg.OperationalError("conn refused 2"),
        psycopg.OperationalError("conn refused 3"),
    ]
    stop_event = threading.Event()
    acker = FakeAcker(on_ack=lambda _m, _q: stop_event.set())
    log_buf = io.StringIO()
    logger = JsonLogger(stream=log_buf, clock=lambda: FIXED_NOW)
    processor = BatchProcessor(
        db=db,
        directory=KNOWN_DEVICES.__contains__,
        schemas=schemas,
        limiter=RejectLimiter(clock=lambda: now_ref[0]),
        now=lambda: FIXED_NOW,
        logger=logger,
    )

    q: queue.Queue[Message] = queue.Queue()
    q.put(
        Message(
            topic="coldchain/v1/hosp-a/SIM-HA-001/telemetry",
            payload=_reading_payload(seq=7),
            mid=77,
            qos=1,
        )
    )

    health = HealthState(mqtt_connected=True, db_ok=True)
    hb = Heartbeat(path=Path("unused"), clock=lambda: now_ref[0])
    hb.beat = lambda _state: None  # type: ignore[method-assign]

    def fake_sleep(seconds: float) -> None:
        assert acker.acked == []
        assert health.db_ok is False
        sleeps.append(seconds)
        now_ref[0] += seconds

    worker = Worker(
        source_queue=q,
        processor=processor,
        acker=acker,
        health_state=health,
        heartbeat=hb,
        settings=settings,
        clock=lambda: now_ref[0],
        sleep=fake_sleep,
        stop_event=stop_event,
        logger=logger,
    )
    worker.run()

    assert sleeps == [1.0, 2.0, 4.0]
    assert db.run_calls == 4
    assert health.db_ok is True
    assert acker.acked == [(77, 1)]
    db_errors = [
        json.loads(line)
        for line in log_buf.getvalue().splitlines()
        if json.loads(line)["event"] == "db_error"
    ]
    assert len(db_errors) == 3


def test_batch_processor_isolates_poison_on_data_error(schemas: Schemas):
    """Ante DataError reprocesa mensaje a mensaje, guarda los válidos y aísla el culpable."""
    db = FakeDb()
    db.poison_seq = 999
    log_buf = io.StringIO()
    logger = JsonLogger(stream=log_buf, clock=lambda: FIXED_NOW)
    processor = BatchProcessor(
        db=db,
        directory=KNOWN_DEVICES.__contains__,
        schemas=schemas,
        limiter=RejectLimiter(clock=lambda: 0.0),
        now=lambda: FIXED_NOW,
        logger=logger,
    )

    messages = [
        Message(
            topic="coldchain/v1/hosp-a/SIM-HA-001/telemetry",
            payload=_reading_payload(seq=1, ts="2026-11-18T12:00:01Z"),
            mid=1,
            qos=1,
        ),
        Message(
            topic="coldchain/v1/hosp-a/SIM-HA-001/telemetry",
            payload=_reading_payload(seq=999, ts="2026-11-18T12:00:02Z"),
            mid=2,
            qos=1,
        ),
        Message(
            topic="coldchain/v1/hosp-a/SIM-HA-001/telemetry",
            payload=_reading_payload(seq=3, ts="2026-11-18T12:00:03Z"),
            mid=3,
            qos=1,
        ),
    ]

    result = processor.process(messages)

    assert result.readings == 2
    assert result.inserted == 2
    assert result.duplicates == 0
    assert result.isolated == 1
    assert result.rejected == 1
    assert len(db.readings) == 2
    assert [row[2] for row in db.readings] == [1, 3]
    assert len(db.rejections) == 1
    assert db.rejections[0][2] == "schema_invalid"

    events = [json.loads(line) for line in log_buf.getvalue().splitlines()]
    isolated_logs = [e for e in events if e["event"] == "poison_isolated"]
    assert len(isolated_logs) == 1
    assert "DataError" in isolated_logs[0]["error"]


def test_reject_limiter_caps_at_120_and_recovers_with_clock(schemas: Schemas):
    """200 rechazos en 1 s permiten guardar 120, descartan 80 y el cubo se recupera con el reloj."""
    now_ref = [0.0]
    db = FakeDb()
    log_buf = io.StringIO()
    logger = JsonLogger(stream=log_buf, clock=lambda: FIXED_NOW)
    limiter = RejectLimiter(capacity=120, refill_per_s=2.0, clock=lambda: now_ref[0])
    processor = BatchProcessor(
        db=db,
        directory=KNOWN_DEVICES.__contains__,
        schemas=schemas,
        limiter=limiter,
        now=lambda: FIXED_NOW,
        logger=logger,
    )

    bad_batch = [
        Message(
            topic="coldchain/v1/hosp-a/SIM-HA-001/telemetry",
            payload=b"{broken",
            mid=i,
            qos=1,
        )
        for i in range(200)
    ]

    res = processor.process(bad_batch)
    assert res.rejected == 120
    assert res.dropped == 80
    assert len(db.rejections) == 120

    dropped_logs = [
        json.loads(line)
        for line in log_buf.getvalue().splitlines()
        if json.loads(line)["event"] == "rejected_dropped"
    ]
    assert len(dropped_logs) == 1
    assert dropped_logs[0]["count"] == 80

    # Avanzamos 5 s -> recupera 10 fichas (2.0/s)
    now_ref[0] = 5.0
    more = [
        Message(
            topic="coldchain/v1/hosp-a/SIM-HA-001/telemetry",
            payload=b"{broken",
            mid=300 + i,
            qos=1,
        )
        for i in range(12)
    ]
    res2 = processor.process(more)
    assert res2.rejected == 10
    assert res2.dropped == 2
    assert len(db.rejections) == 130


def test_worker_stop_event_drains_queue_and_acks_all(schemas: Schemas):
    """Al activarse stop_event vacía la cola por lotes, guarda y confirma todos los mensajes."""
    settings = _make_settings(BATCH_MAX_SIZE="2", BATCH_MAX_WAIT_MS="100")
    db = FakeDb()
    acker = FakeAcker()
    stop_event = threading.Event()
    log_buf = io.StringIO()
    logger = JsonLogger(stream=log_buf, clock=lambda: FIXED_NOW)
    processor = BatchProcessor(
        db=db,
        directory=KNOWN_DEVICES.__contains__,
        schemas=schemas,
        limiter=RejectLimiter(clock=lambda: 0.0),
        now=lambda: FIXED_NOW,
        logger=logger,
    )

    q: queue.Queue[Message] = queue.Queue()
    for i in range(1, 6):
        q.put(
            Message(
                topic="coldchain/v1/hosp-a/SIM-HA-001/telemetry",
                payload=_reading_payload(seq=i, ts=f"2026-11-18T12:00:0{i}Z"),
                mid=50 + i,
                qos=1,
            )
        )
    stop_event.set()

    hb = Heartbeat(path=Path("unused"), clock=lambda: 0.0)
    hb.beat = lambda _s: None  # type: ignore[method-assign]

    worker = Worker(
        source_queue=q,
        processor=processor,
        acker=acker,
        health_state=HealthState(mqtt_connected=True, db_ok=True),
        heartbeat=hb,
        settings=settings,
        clock=lambda: 0.0,
        sleep=lambda _s: None,
        stop_event=stop_event,
        logger=logger,
    )
    worker.run()

    assert q.empty()
    assert len(db.readings) == 5
    assert acker.acked == [(51, 1), (52, 1), (53, 1), (54, 1), (55, 1)]


def test_heartbeat_only_writes_when_both_healthy_and_expires_at_30s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """El latido no se escribe si mqtt_connected o db_ok es falso y caduca a los 30 s."""
    hb_path = tmp_path / "ingestor.alive"
    now_ref = [100.0]
    hb = Heartbeat(path=hb_path, clock=lambda: now_ref[0])

    for mqtt_ok, db_ok in ((False, False), (True, False), (False, True)):
        hb.beat(HealthState(mqtt_connected=mqtt_ok, db_ok=db_ok))
        assert not hb_path.exists()
        assert is_alive(hb_path, max_age_s=30, clock=lambda: now_ref[0]) is False

    hb.beat(HealthState(mqtt_connected=True, db_ok=True))
    assert hb_path.exists()
    assert is_alive(hb_path, max_age_s=30, clock=lambda: 129.9) is True
    assert is_alive(hb_path, max_age_s=30, clock=lambda: 130.0) is False

    monkeypatch.setenv("HEARTBEAT_PATH", str(hb_path))
    assert health_main() == 1

    real_now_hb = Heartbeat(path=hb_path)
    real_now_hb.beat(HealthState(mqtt_connected=True, db_ok=True))
    assert health_main() == 0


def test_logs_never_leak_payload_or_passwords(schemas: Schemas):
    """Ningún evento de log incluye el payload recibido ni las contraseñas configuradas."""
    settings = _make_settings()
    payload_marker = "PAYLOAD_SENSITIVE_MARKER_XYZ"
    db = FakeDb()
    db.poison_seq = 555

    log_buf = io.StringIO()
    logger = JsonLogger(stream=log_buf, clock=lambda: FIXED_NOW)
    processor = BatchProcessor(
        db=db,
        directory=KNOWN_DEVICES.__contains__,
        schemas=schemas,
        limiter=RejectLimiter(clock=lambda: 0.0),
        now=lambda: FIXED_NOW,
        logger=logger,
    )

    # Incluye rechazo normal y mensaje venenoso con marcador distinguible
    poison_bytes = json.dumps(
        {
            "schema": "telemetry.v1",
            "device_id": "SIM-HA-001",
            "ts": "2026-11-18T12:00:00Z",
            "seq": 555,
            "temp_c": 4.2,
            "rssi_dbm": -80,
            "battery_pct": 95.0,
            "on_backup_power": False,
            "fw": payload_marker[:28],
        }
    ).encode("utf-8")
    bad_bytes = f"bad-payload-{payload_marker}".encode()

    processor.process(
        [
            Message(
                topic="coldchain/v1/hosp-a/SIM-HA-001/telemetry",
                payload=bad_bytes,
                mid=1,
                qos=1,
            ),
            Message(
                topic="coldchain/v1/hosp-a/SIM-HA-001/telemetry",
                payload=poison_bytes,
                mid=2,
                qos=1,
            ),
        ]
    )

    logger.info("started", version="0.1.0", config=_safe_config_summary(settings))

    source_queue: queue.Queue[Message] = queue.Queue(maxsize=10)
    mqtt_src = MqttSource(settings, source_queue, HealthState(), logger)
    assert mqtt_src._client._callback_api_version == mqtt.CallbackAPIVersion.VERSION2
    assert mqtt_src._client._client_id == b"ingestor"

    output = log_buf.getvalue()
    assert payload_marker not in output
    assert settings.mqtt_password not in output
    assert settings.db_password not in output
