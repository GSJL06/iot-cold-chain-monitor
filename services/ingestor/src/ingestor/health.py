"""Estado de salud entre hilos, latido en disco y comprobación de vida."""

import math
import os
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_HEARTBEAT_PATH = Path(tempfile.gettempdir()) / "ingestor.alive"


@dataclass
class HealthState:
    """Indicadores de salud compartidos de forma segura entre hilos."""

    mqtt_connected: bool = False
    db_ok: bool = False
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    def __setattr__(self, name: str, value: object) -> None:
        """Protege con lock las escrituras sobre los campos de salud."""
        if name in {"mqtt_connected", "db_ok"} and "_lock" in self.__dict__:
            lock = object.__getattribute__(self, "_lock")
            with lock:
                object.__setattr__(self, name, value)
        else:
            object.__setattr__(self, name, value)

    def __getattribute__(self, name: str) -> Any:
        """Protege con lock las lecturas directas de los campos de salud."""
        if name in {"mqtt_connected", "db_ok"}:
            lock = object.__getattribute__(self, "_lock")
            with lock:
                return object.__getattribute__(self, name)
        return object.__getattribute__(self, name)

    def is_healthy(self) -> bool:
        """Evalúa atómicamente si MQTT y base de datos están operativos."""
        lock = object.__getattribute__(self, "_lock")
        with lock:
            mqtt_ok = bool(object.__getattribute__(self, "mqtt_connected"))
            db_ok = bool(object.__getattribute__(self, "db_ok"))
            return mqtt_ok and db_ok


class Heartbeat:
    """Escritor del fichero de latido para el healthcheck del contenedor."""

    def __init__(
        self,
        path: Path = DEFAULT_HEARTBEAT_PATH,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Configura la ruta del fichero y el reloj a registrar."""
        self._path = path
        self._clock = clock

    def beat(self, state: HealthState) -> None:
        """Escribe la marca temporal actual solo si mqtt_connected y db_ok son verdaderos."""
        if not state.is_healthy():
            return
        now = self._clock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_name(f".{self._path.name}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(f"{now:.6f}\n")
        tmp_path.replace(self._path)


def is_alive(
    path: Path = DEFAULT_HEARTBEAT_PATH,
    max_age_s: float = 30.0,
    clock: Callable[[], float] = time.time,
) -> bool:
    """Devuelve True si el latido existe y tiene una antigüedad menor que max_age_s."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    try:
        recorded_ts = float(raw)
    except ValueError:
        return False
    if not math.isfinite(recorded_ts):
        return False
    return (clock() - recorded_ts) < max_age_s


def main() -> int:
    """Punto de entrada CLI para comprobar el latido del ingestor."""
    raw_path = os.environ.get("HEARTBEAT_PATH", "").strip()
    target_path = Path(raw_path) if raw_path else DEFAULT_HEARTBEAT_PATH
    return 0 if is_alive(target_path) else 1


if __name__ == "__main__":
    sys.exit(main())
