"""Procesamiento por lotes, limitación de rechazos, aislamiento y bucle del worker."""

import queue
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

import psycopg

from ingestor.config import Settings
from ingestor.directory import DeviceDirectory
from ingestor.health import HealthState, Heartbeat
from ingestor.logs import JsonLogger
from ingestor.store import apply_statuses, insert_readings, insert_rejections, touch_devices
from ingestor.validation import (
    MAX_PAYLOAD_BYTES,
    ParsedReading,
    ParsedStatus,
    Rejection,
    Schemas,
    classify,
)

T = TypeVar("T")

_TOPIC_DEVICE_RE = re.compile(
    r"\Acoldchain/v1/[a-z0-9][a-z0-9-]{1,23}/([A-Z0-9][A-Z0-9-]{3,31})/(?:telemetry|status)\Z"
)


@dataclass(frozen=True)
class Message:
    """Mensaje MQTT pendiente de clasificación, persistencia y confirmación."""

    topic: str
    payload: bytes
    mid: int
    qos: int


@dataclass(frozen=True)
class BatchResult:
    """Contadores resultantes de procesar un lote de mensajes."""

    readings: int
    inserted: int
    duplicates: int
    statuses: int
    rejected: int
    dropped: int
    isolated: int


class Db(Protocol):
    """Puerto de acceso transaccional a la base de datos."""

    def run(self, fn: Callable[[Any], T]) -> T:
        """Ejecuta una función dentro de una transacción."""
        ...


class Acker(Protocol):
    """Puerto para confirmar entregas MQTT tras persistir el lote."""

    def ack(self, mid: int, qos: int) -> None:
        """Envía confirmación de entrega para un mensaje."""
        ...


class RejectLimiter:
    """Limitador por cubo de fichas para acotar inserciones en rejected_messages."""

    def __init__(
        self,
        capacity: int = 120,
        refill_per_s: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Inicializa el cubo lleno con capacidad y tasa de recarga configurables."""
        self._capacity = float(capacity)
        self._refill_per_s = refill_per_s
        self._clock = clock
        self._tokens = float(capacity)
        self._last: float | None = None

    def allow(self) -> bool:
        """Consume una ficha si hay disponibilidad tras recargar según el reloj."""
        now = self._clock()
        if self._last is None:
            self._last = now
        else:
            elapsed = max(0.0, now - self._last)
            self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_s)
            self._last = now

        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


def _extract_valid_device_id(topic: str) -> str | None:
    """Extrae device_id del topic solo si cumple el patrón oficial."""
    match = _TOPIC_DEVICE_RE.fullmatch(topic)
    return match.group(1) if match is not None else None


def _sanitize_payload(payload: bytes) -> str:
    """Decodifica payload sustituyendo NUL y bytes inválidos, truncado a 4096 caracteres."""
    return payload.decode("utf-8", errors="replace").replace("\x00", "\ufffd")[:MAX_PAYLOAD_BYTES]


def _format_error(exc: Exception) -> str:
    """Formatea clase y primera línea del error evitando exponer parámetros."""
    detail = str(exc).splitlines()[0] if str(exc) else ""
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


class BatchProcessor:
    """Clasifica, persiste en una transacción y aísla mensajes venenosos por lote."""

    def __init__(
        self,
        db: Db,
        directory: DeviceDirectory | Callable[[str], bool],
        schemas: Schemas,
        limiter: RejectLimiter,
        now: Callable[[], datetime],
        logger: JsonLogger,
        skew_s: int = 300,
        mono_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Inicializa el procesador con dependencias inyectables para pruebas."""
        self._db = db
        self._is_known = directory.is_known if isinstance(directory, DeviceDirectory) else directory
        self._schemas = schemas
        self._limiter = limiter
        self._now = now
        self._logger = logger
        self._skew_s = skew_s
        self._mono_clock = mono_clock

    def ping(self) -> bool | None:
        """Ejecuta ping sobre el backend si soporta comprobación de salud."""
        ping_fn = getattr(self._db, "ping", None)
        if callable(ping_fn):
            return bool(ping_fn())
        return None

    def _log_rejection(self, rej: Rejection) -> None:
        """Registra un rechazo sin incluir jamás el payload ni credenciales."""
        fields: dict[str, Any] = {"reason": rej.reason, "topic": rej.topic}
        device_id = _extract_valid_device_id(rej.topic)
        if device_id is not None:
            fields["device_id"] = device_id
        self._logger.warning("message_rejected", **fields)

    def _process_isolated(
        self,
        items: Sequence[tuple[Message, ParsedReading | ParsedStatus | Rejection, bool]],
    ) -> BatchResult:
        """Reprocesa el lote mensaje a mensaje tras un DataError o IntegrityError."""
        readings_count = 0
        inserted_total = 0
        statuses_count = 0
        rejected_count = 0
        dropped_count = 0
        isolated_count = 0

        for msg, parsed, allow_rej in items:
            if isinstance(parsed, Rejection):
                if not allow_rej:
                    dropped_count += 1
                    continue
                try:
                    self._db.run(lambda conn, r=parsed: insert_rejections(conn, [r]))
                except (psycopg.OperationalError, psycopg.InterfaceError):
                    raise
                except (psycopg.errors.DataError, psycopg.errors.IntegrityError) as exc:
                    isolated_count += 1
                    self._logger.error(
                        "poison_isolated",
                        topic=msg.topic,
                        error=_format_error(exc),
                    )
                else:
                    rejected_count += 1
                continue

            try:
                if isinstance(parsed, ParsedReading):

                    def _save_reading(conn: Any, row: ParsedReading = parsed) -> int:
                        ins = insert_readings(conn, [row])
                        touch_devices(conn, [row.device_id])
                        return ins

                    ins = self._db.run(_save_reading)
                    readings_count += 1
                    inserted_total += ins
                else:

                    def _save_status(conn: Any, row: ParsedStatus = parsed) -> None:
                        apply_statuses(conn, [row])

                    self._db.run(_save_status)
                    statuses_count += 1
            except (psycopg.OperationalError, psycopg.InterfaceError):
                raise
            except (psycopg.errors.DataError, psycopg.errors.IntegrityError) as exc:
                isolated_count += 1
                self._logger.error(
                    "poison_isolated",
                    topic=msg.topic,
                    error=_format_error(exc),
                )
                poison_rej = Rejection(
                    reason="schema_invalid",
                    topic=msg.topic,
                    payload_text=_sanitize_payload(msg.payload),
                )
                if self._limiter.allow():
                    try:
                        self._db.run(lambda conn, r=poison_rej: insert_rejections(conn, [r]))
                    except (psycopg.OperationalError, psycopg.InterfaceError):
                        raise
                    except (psycopg.errors.DataError, psycopg.errors.IntegrityError):
                        pass
                    else:
                        rejected_count += 1
                else:
                    dropped_count += 1

        duplicates = readings_count - inserted_total
        return BatchResult(
            readings=readings_count,
            inserted=inserted_total,
            duplicates=duplicates,
            statuses=statuses_count,
            rejected=rejected_count,
            dropped=dropped_count,
            isolated=isolated_count,
        )

    def process(self, messages: Sequence[Message]) -> BatchResult:
        """Clasifica y persiste un lote completo dentro de una única transacción."""
        if not messages:
            return BatchResult(0, 0, 0, 0, 0, 0, 0)

        t0 = self._mono_clock()
        now_dt = self._now()
        now_utc = now_dt.replace(tzinfo=UTC) if now_dt.tzinfo is None else now_dt.astimezone(UTC)

        classified: list[tuple[Message, ParsedReading | ParsedStatus | Rejection, bool]] = []
        readings: list[ParsedReading] = []
        statuses: list[ParsedStatus] = []
        allowed_rejections: list[Rejection] = []
        dropped = 0

        for msg in messages:
            parsed = classify(
                msg.topic,
                msg.payload,
                self._is_known,
                now_utc,
                self._schemas,
                skew_s=self._skew_s,
            )
            if isinstance(parsed, ParsedReading):
                readings.append(parsed)
                classified.append((msg, parsed, False))
            elif isinstance(parsed, ParsedStatus):
                statuses.append(parsed)
                classified.append((msg, parsed, False))
            else:
                self._log_rejection(parsed)
                allowed = self._limiter.allow()
                if allowed:
                    allowed_rejections.append(parsed)
                else:
                    dropped += 1
                classified.append((msg, parsed, allowed))

        try:

            def _write_batch(conn: Any) -> int:
                inserted_rows = insert_readings(conn, readings)
                touch_devices(conn, (r.device_id for r in readings))
                apply_statuses(conn, statuses)
                insert_rejections(conn, allowed_rejections)
                return inserted_rows

            inserted = self._db.run(_write_batch)
            result = BatchResult(
                readings=len(readings),
                inserted=inserted,
                duplicates=len(readings) - inserted,
                statuses=len(statuses),
                rejected=len(allowed_rejections),
                dropped=dropped,
                isolated=0,
            )
        except (psycopg.OperationalError, psycopg.InterfaceError):
            raise
        except (psycopg.errors.DataError, psycopg.errors.IntegrityError):
            result = self._process_isolated(classified)

        if result.dropped > 0:
            self._logger.warning("rejected_dropped", count=result.dropped)

        valid_timestamps = [r.ts for r in readings] + [s.ts for s in statuses]
        max_age_ms = (
            round(
                max(max(0.0, (now_utc - ts).total_seconds() * 1000.0) for ts in valid_timestamps),
                2,
            )
            if valid_timestamps
            else 0.0
        )
        latency_ms = round(max(0.0, (self._mono_clock() - t0) * 1000.0), 2)

        self._logger.info(
            "batch_stored",
            messages=len(messages),
            readings=result.readings,
            inserted=result.inserted,
            duplicates=result.duplicates,
            statuses=result.statuses,
            rejected=result.rejected,
            latency_ms=latency_ms,
            max_age_ms=max_age_ms,
        )
        return result


class Worker:
    """Bucle consumidor que agrupa mensajes por tamaño/tiempo y gestiona reintentos."""

    def __init__(
        self,
        source_queue: queue.Queue[Message],
        processor: BatchProcessor,
        acker: Acker,
        health_state: HealthState,
        heartbeat: Heartbeat,
        settings: Settings,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
        stop_event: threading.Event,
        logger: JsonLogger,
    ) -> None:
        """Inicializa el worker con dependencias inyectables para pruebas deterministas."""
        self._queue = source_queue
        self._processor = processor
        self._acker = acker
        self._health_state = health_state
        self._heartbeat = heartbeat
        self._settings = settings
        self._clock = clock
        self._sleep = sleep
        self._stop_event = stop_event
        self._logger = logger

        self._last_beat: float | None = None
        self._last_db_check: float = self._clock()
        self._last_stats: float = self._clock()
        self._stats: dict[str, int] = {
            "messages": 0,
            "readings": 0,
            "inserted": 0,
            "duplicates": 0,
            "statuses": 0,
            "rejected": 0,
            "dropped": 0,
            "isolated": 0,
        }

    def _maybe_beat(self) -> None:
        """Escribe el latido como máximo una vez por segundo según el reloj inyectado."""
        now = self._clock()
        if self._last_beat is None or (now - self._last_beat) >= 1.0:
            self._heartbeat.beat(self._health_state)
            self._last_beat = now

    def _maybe_log_stats(self) -> None:
        """Registra contadores acumulados cada 30 s."""
        now = self._clock()
        if (now - self._last_stats) >= 30.0:
            self._logger.info("stats", **self._stats)
            self._last_stats = now

    def _maybe_idle_ping(self) -> None:
        """Comprueba conectividad con la base de datos cada 30 s cuando no hay tráfico."""
        now = self._clock()
        if (now - self._last_db_check) >= 30.0:
            ping_result = self._processor.ping()
            if ping_result is not None:
                self._health_state.db_ok = ping_result
                if not ping_result:
                    self._logger.error("db_error", error="PingFailed: SELECT 1 returned false")
            self._last_db_check = now

    def _accumulate(self, msg_count: int, res: BatchResult) -> None:
        """Suma los resultados del lote a las estadísticas acumuladas del worker."""
        self._stats["messages"] += msg_count
        self._stats["readings"] += res.readings
        self._stats["inserted"] += res.inserted
        self._stats["duplicates"] += res.duplicates
        self._stats["statuses"] += res.statuses
        self._stats["rejected"] += res.rejected
        self._stats["dropped"] += res.dropped
        self._stats["isolated"] += res.isolated

    def _pause_for_retry(self, delay_s: float) -> None:
        """Espera antes de reintentar respetando el sleep inyectado o stop_event."""
        if self._sleep is time.sleep:
            self._stop_event.wait(timeout=delay_s)
        else:
            self._sleep(delay_s)

    def _process_with_retry(self, batch: Sequence[Message]) -> bool:
        """Procesa un lote reintentando con retroceso exponencial ante caídas de BD."""
        backoff_s = 1.0
        while True:
            try:
                result = self._processor.process(batch)
            except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                self._health_state.db_ok = False
                self._logger.error("db_error", error=_format_error(exc))
                self._maybe_beat()
                if self._stop_event.is_set():
                    return False
                self._pause_for_retry(backoff_s)
                backoff_s = min(backoff_s * 2.0, 10.0)
                if self._stop_event.is_set():
                    return False
            else:
                self._health_state.db_ok = True
                self._last_db_check = self._clock()
                for msg in batch:
                    self._acker.ack(msg.mid, msg.qos)
                self._accumulate(len(batch), result)
                return True

    def _collect_batch(self, first: Message) -> list[Message]:
        """Agrupa hasta batch_max_size o hasta vencer batch_max_wait_ms."""
        batch = [first]
        wait_s = self._settings.batch_max_wait_ms / 1000.0
        deadline = self._clock() + wait_s

        while len(batch) < self._settings.batch_max_size:
            if self._stop_event.is_set():
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
                continue

            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            try:
                batch.append(self._queue.get(timeout=remaining))
            except queue.Empty:
                break
        return batch

    def _drain_remaining(self) -> None:
        """Vacía la cola por lotes al activarse la parada y confirma cada lote persistido."""
        while True:
            batch: list[Message] = []
            while len(batch) < self._settings.batch_max_size:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            if not batch:
                break
            if not self._process_with_retry(batch):
                break
            self._maybe_beat()

    def run(self) -> None:
        """Ejecuta el bucle principal del worker hasta recibir stop_event y vaciar la cola."""
        idle_poll_s = min(0.5, max(0.05, self._settings.batch_max_wait_ms / 1000.0))

        while not self._stop_event.is_set():
            self._maybe_beat()
            self._maybe_log_stats()

            try:
                first = self._queue.get(timeout=idle_poll_s)
            except queue.Empty:
                self._maybe_idle_ping()
                continue

            batch = self._collect_batch(first)
            self._process_with_retry(batch)
            self._maybe_beat()
            self._maybe_log_stats()

        self._drain_remaining()
        self._maybe_beat()
