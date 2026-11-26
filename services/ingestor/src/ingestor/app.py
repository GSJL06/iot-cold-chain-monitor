"""Composición del servicio ingestor y gestión de señales de parada limpia."""

import queue
import signal
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ingestor import __version__
from ingestor.config import ConfigError, Settings, load_settings
from ingestor.directory import DeviceDirectory
from ingestor.health import DEFAULT_HEARTBEAT_PATH, HealthState, Heartbeat
from ingestor.logs import get_logger, set_level
from ingestor.mqtt_io import MqttSource
from ingestor.pipeline import BatchProcessor, Message, RejectLimiter, Worker
from ingestor.store import PgDb, build_conninfo, load_devices
from ingestor.validation import Schemas


def _safe_config_summary(settings: Settings) -> dict[str, Any]:
    """Serializa la configuración activa omitiendo explícitamente contraseñas."""
    return {
        "mqtt_host": settings.mqtt_host,
        "mqtt_port": settings.mqtt_port,
        "shared_group": settings.shared_group,
        "ingestor_id": settings.ingestor_id,
        "db_host": settings.db_host,
        "db_port": settings.db_port,
        "db_name": settings.db_name,
        "batch_max_size": settings.batch_max_size,
        "batch_max_wait_ms": settings.batch_max_wait_ms,
        "clock_skew_s": settings.clock_skew_s,
        "schema_dir": str(settings.schema_dir),
        "log_level": settings.log_level,
    }


def main() -> int:
    """Inicializa dependencias, arranca el bucle de ingestión y coordina el apagado."""
    import os

    try:
        settings = load_settings(os.environ)
    except ConfigError as exc:
        sys.stderr.write(f"Configuration error: {exc}\n")
        return 2

    set_level(settings.log_level)
    logger = get_logger()

    schemas = Schemas.load(settings.schema_dir)
    db = PgDb(build_conninfo(settings))
    directory = DeviceDirectory(
        loader=lambda: db.run(load_devices),
        clock=time.monotonic,
    )
    limiter = RejectLimiter(capacity=120, refill_per_s=2.0, clock=time.monotonic)
    processor = BatchProcessor(
        db=db,
        directory=directory,
        schemas=schemas,
        limiter=limiter,
        now=lambda: datetime.now(UTC),
        logger=logger,
        skew_s=settings.clock_skew_s,
    )

    source_queue: queue.Queue[Message] = queue.Queue(maxsize=1000)
    health_state = HealthState(mqtt_connected=False, db_ok=db.ping())
    raw_hb = os.environ.get("HEARTBEAT_PATH", "").strip()
    heartbeat_path = Path(raw_hb) if raw_hb else DEFAULT_HEARTBEAT_PATH
    heartbeat = Heartbeat(path=heartbeat_path, clock=time.time)

    mqtt_source = MqttSource(
        settings=settings,
        source_queue=source_queue,
        health_state=health_state,
        logger=logger,
    )

    stop_event = threading.Event()

    def _handle_signal(signum: int, frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    worker = Worker(
        source_queue=source_queue,
        processor=processor,
        acker=mqtt_source,
        health_state=health_state,
        heartbeat=heartbeat,
        settings=settings,
        clock=time.monotonic,
        sleep=time.sleep,
        stop_event=stop_event,
        logger=logger,
    )

    worker_thread = threading.Thread(target=worker.run, name="ingestor-worker")
    worker_thread.start()
    mqtt_source.start()
    logger.info("started", version=__version__, config=_safe_config_summary(settings))

    try:
        while worker_thread.is_alive():
            worker_thread.join(timeout=0.5)
    finally:
        stop_event.set()
        worker_thread.join()
        mqtt_source.stop()
        db.close()
        logger.info("stopped", version=__version__)

    return 0
