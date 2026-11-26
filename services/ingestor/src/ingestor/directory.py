"""Caché en memoria de dispositivos conocidos con recarga periódica y ante fallos."""

import threading
import time
from collections.abc import Callable


class DeviceDirectory:
    """Directorio de dispositivos conocidos con carga perezosa y limitación de recargas."""

    def __init__(
        self,
        loader: Callable[[], dict[str, str]],
        clock: Callable[[], float] = time.monotonic,
        refresh_s: float = 60,
        miss_refresh_s: float = 10,
    ) -> None:
        """Inicializa el directorio sin consultar el loader hasta la primera comprobación."""
        self._loader = loader
        self._clock = clock
        self._refresh_s = refresh_s
        self._miss_refresh_s = miss_refresh_s
        self._devices: dict[str, str] = {}
        self._last_refresh: float | None = None
        self._lock = threading.Lock()
        self.last_error: Exception | None = None

    def _reload(self, now: float) -> None:
        """Invoca el loader y conserva la caché previa si se produce una excepción."""
        self._last_refresh = now
        try:
            loaded = self._loader()
        except Exception as exc:
            self.last_error = exc
            return
        self._devices = dict(loaded)
        self.last_error = None

    def is_known(self, device_id: str) -> bool:
        """Devuelve True si el dispositivo es conocido, recargando según la política temporal."""
        with self._lock:
            now = self._clock()
            if self._last_refresh is None or (now - self._last_refresh) >= self._refresh_s:
                self._reload(now)

            if device_id in self._devices:
                return True

            if (
                self._last_refresh is not None
                and (now - self._last_refresh) >= self._miss_refresh_s
            ):
                self._reload(now)
                return device_id in self._devices

            return False
