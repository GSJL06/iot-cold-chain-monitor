"""Logger JSON estructurado de una línea por evento para el ingestor."""

import json
import sys
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, TextIO

_LEVELS: dict[str, int] = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}
_FIXED_FIELDS = frozenset({"ts", "level", "service", "event"})
_SENSITIVE_KEY_PARTS = ("password", "secret", "token")
_MASK = "***"


def _utc_now() -> datetime:
    """Devuelve el instante actual en UTC."""
    return datetime.now(UTC)


def _format_ts(ts: datetime) -> str:
    """Formatea un datetime como ISO-8601 UTC sin milisegundos y con sufijo Z."""
    ts_utc = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
    return ts_utc.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_level(level: str) -> int:
    """Convierte el nombre de nivel a su prioridad numérica."""
    normalized = level.strip().upper()
    if normalized not in _LEVELS:
        raise ValueError(f"Unsupported log level: {level}")
    return _LEVELS[normalized]


def _redact_item(value: Any) -> Any:
    """Aplica redacción recursiva sobre estructuras anidadas."""
    if isinstance(value, Mapping):
        return {k: _redact_field(str(k), v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_item(item) for item in value]
    return value


def _redact_field(key: str, value: Any) -> Any:
    """Oculta valores cuya clave contenga credenciales o tokens."""
    lowered = key.lower()
    if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
        return _MASK
    return _redact_item(value)


def _safe_str(value: Any) -> str:
    """Convierte cualquier valor a texto evitando excepciones en __str__."""
    try:
        return str(value)
    except Exception:
        return f"<unserializable:{type(value).__name__}>"


class JsonLogger:
    """Emisor de logs JSON en una sola línea con redacción automática de secretos."""

    def __init__(
        self,
        stream: TextIO | None = None,
        clock: Callable[[], datetime] | None = None,
        level: str = "INFO",
    ) -> None:
        """Inicializa el logger con flujo de salida, reloj y nivel mínimo."""
        self._stream = stream
        self._clock = clock or _utc_now
        self._level_num = _parse_level(level)
        self._lock = threading.Lock()

    def set_level(self, level: str) -> None:
        """Cambia el nivel mínimo emitido por este logger."""
        self._level_num = _parse_level(level)

    def log(self, level: str, event: str, **fields: Any) -> None:
        """Emite una línea JSON si el nivel cumple el umbral configurado."""
        level_name = level.strip().upper()
        level_num = _LEVELS.get(level_name, _LEVELS["INFO"])
        if level_num < self._level_num:
            return

        entry: dict[str, Any] = {
            "ts": _format_ts(self._clock()),
            "level": level_name,
            "service": "ingestor",
            "event": event,
        }
        for key, value in fields.items():
            if key in _FIXED_FIELDS:
                continue
            entry[key] = _redact_field(key, value)

        try:
            line = json.dumps(entry, separators=(",", ":"), ensure_ascii=False, default=str)
        except (TypeError, ValueError, Exception):
            # Respaldo ante __str__ defectuoso, referencias circulares o claves no válidas
            safe_entry = {k: v if k in _FIXED_FIELDS else _safe_str(v) for k, v in entry.items()}
            line = json.dumps(safe_entry, separators=(",", ":"), ensure_ascii=False, default=str)

        target = self._stream if self._stream is not None else sys.stdout
        with self._lock:
            target.write(line + "\n")
            target.flush()

    def info(self, event: str, **fields: Any) -> None:
        """Registra un evento de nivel INFO."""
        self.log("INFO", event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        """Registra un evento de nivel WARNING."""
        self.log("WARNING", event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        """Registra un evento de nivel ERROR."""
        self.log("ERROR", event, **fields)


_process_logger = JsonLogger()


def get_logger() -> JsonLogger:
    """Devuelve el logger global del proceso hacia stdout con reloj UTC real."""
    return _process_logger


def set_level(level: str) -> None:
    """Actualiza el nivel mínimo de log del logger global del proceso."""
    _process_logger.set_level(level)
