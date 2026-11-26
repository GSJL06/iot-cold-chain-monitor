"""Validación y clasificación pura de mensajes MQTT para el ingestor."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn, Self

from jsonschema import Draft202012Validator

MAX_PAYLOAD_BYTES = 4096
MAX_SEQ = 2_147_483_647

_TOPIC_RE = re.compile(
    r"\Acoldchain/v1/([a-z0-9][a-z0-9-]{1,23})/([A-Z0-9][A-Z0-9-]{3,31})/(telemetry|status)\Z"
)
_TS_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z\Z")


@dataclass(frozen=True)
class ParsedReading:
    """Lectura de telemetría validada y lista para persistirse."""

    device_id: str
    ts: datetime
    seq: int
    temp_c: float
    humidity_pct: float | None
    rssi_dbm: int
    battery_pct: float
    on_backup_power: bool


@dataclass(frozen=True)
class ParsedStatus:
    """Mensaje de estado online/offline validado."""

    device_id: str
    state: str
    ts: datetime


@dataclass(frozen=True)
class Rejection:
    """Mensaje rechazado con su motivo y payload saneado para auditoría."""

    reason: str
    topic: str
    payload_text: str


def _load_schema_file(path: Path) -> dict[str, Any]:
    """Lee un archivo JSON Schema desde disco en UTF-8."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@dataclass(frozen=True)
class Schemas:
    """Validadores Draft 2020-12 compilados para telemetry.v1 y status.v1."""

    telemetry: Draft202012Validator
    status: Draft202012Validator

    @classmethod
    def load(cls, schema_dir: Path) -> Self:
        """Carga ambos contratos desde schema_dir y crea un validador por cada uno."""
        telemetry_schema = _load_schema_file(schema_dir / "telemetry.v1.schema.json")
        status_schema = _load_schema_file(schema_dir / "status.v1.schema.json")
        return cls(
            telemetry=Draft202012Validator(telemetry_schema),
            status=Draft202012Validator(status_schema),
        )


def _format_rejection_payload(payload: bytes) -> str:
    """Decodifica el payload sustituyendo bytes inválidos y NUL, y trunca a 4096 caracteres."""
    return payload.decode("utf-8", errors="replace").replace("\x00", "\ufffd")[:MAX_PAYLOAD_BYTES]


def _reject(reason: str, topic: str, payload: bytes) -> Rejection:
    """Construye un Rejection con el payload saneado."""
    return Rejection(
        reason=reason,
        topic=topic,
        payload_text=_format_rejection_payload(payload),
    )


def _reject_json_constant(value: str) -> NoReturn:
    """Rechaza constantes JSON no estándar como NaN, Infinity o -Infinity."""
    raise ValueError(f"Invalid JSON constant: {value}")


def _parse_utc_ts(ts_text: str) -> datetime | None:
    """Valida el formato exacto y parsea un timestamp ISO-8601 UTC."""
    if not _TS_RE.fullmatch(ts_text):
        return None
    try:
        parsed = datetime.fromisoformat(ts_text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def classify(
    topic: str,
    payload: bytes,
    is_known: Callable[[str], bool],
    now: datetime,
    schemas: Schemas,
    skew_s: int = 300,
) -> ParsedReading | ParsedStatus | Rejection:
    """Clasifica un mensaje MQTT aplicando el orden estricto de precedencia del contrato."""
    # 1. Validación estructural del topic e identificadores
    if not isinstance(topic, str):
        return _reject("topic_mismatch", str(topic), payload)
    match = _TOPIC_RE.fullmatch(topic)
    if match is None:
        return _reject("topic_mismatch", topic, payload)
    _, topic_device_id, topic_kind = match.groups()

    # 2. Límite de tamaño, decodificación UTF-8 estricta y JSON objeto sin NaN/Infinity
    if len(payload) > MAX_PAYLOAD_BYTES:
        return _reject("schema_invalid", topic, payload)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return _reject("schema_invalid", topic, payload)
    try:
        data = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return _reject("schema_invalid", topic, payload)
    if not isinstance(data, dict):
        return _reject("schema_invalid", topic, payload)

    # 3. Versión del contrato: solo cadenas distintas de la esperada; ausente/no-str va al paso 4
    expected_schema = f"{topic_kind}.v1"
    raw_schema = data.get("schema")
    if isinstance(raw_schema, str) and raw_schema != expected_schema:
        return _reject("unsupported_schema", topic, payload)

    # 4. Validación contra JSON Schema, rango de seq en 32 bits y fecha válida
    validator = schemas.telemetry if topic_kind == "telemetry" else schemas.status
    if not validator.is_valid(data):
        return _reject("schema_invalid", topic, payload)
    if topic_kind == "telemetry" and data["seq"] > MAX_SEQ:
        return _reject("schema_invalid", topic, payload)
    ts = _parse_utc_ts(data["ts"])
    if ts is None:
        return _reject("schema_invalid", topic, payload)

    # 5. Coincidencia del device_id del payload con el del topic
    device_id = data["device_id"]
    if device_id != topic_device_id:
        return _reject("topic_mismatch", topic, payload)

    # 6. Existencia del dispositivo en el directorio conocido
    if not is_known(device_id):
        return _reject("unknown_device", topic, payload)

    # 7. Tolerancia de reloj hacia el futuro respecto a now
    now_utc = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    if ts - now_utc > timedelta(seconds=skew_s):
        return _reject("clock_skew", topic, payload)

    if topic_kind == "status":
        return ParsedStatus(
            device_id=device_id,
            state=data["state"],
            ts=ts,
        )

    raw_humidity = data.get("humidity_pct")
    return ParsedReading(
        device_id=device_id,
        ts=ts,
        seq=int(data["seq"]),
        temp_c=float(data["temp_c"]),
        humidity_pct=float(raw_humidity) if raw_humidity is not None else None,
        rssi_dbm=int(data["rssi_dbm"]),
        battery_pct=float(data["battery_pct"]),
        on_backup_power=bool(data["on_backup_power"]),
    )
