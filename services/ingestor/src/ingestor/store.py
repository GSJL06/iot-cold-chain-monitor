"""Acceso SQL parametrizado y pool de conexiones PostgreSQL para el ingestor."""

from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Any, TypeVar

import psycopg
from psycopg import Connection
from psycopg.conninfo import make_conninfo

from ingestor.config import Settings
from ingestor.validation import ParsedReading, ParsedStatus, Rejection

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

T = TypeVar("T")

_SQL_LOAD_DEVICES = "SELECT device_id, site_id FROM devices ORDER BY device_id"

_SQL_INSERT_READINGS = """
INSERT INTO readings (
    ts, device_id, seq, temp_c, humidity_pct, rssi_dbm, battery_pct, on_backup_power
)
SELECT * FROM unnest(
    %s::timestamptz[], %s::text[], %s::integer[], %s::float8[],
    %s::float8[], %s::smallint[], %s::real[], %s::boolean[])
ON CONFLICT (device_id, ts) DO NOTHING
"""

_SQL_INSERT_REJECTIONS = (
    "INSERT INTO rejected_messages (topic, payload, reason) VALUES (%s, %s, %s)"
)

_SQL_TOUCH_DEVICES = """
INSERT INTO device_status (device_id, state, last_seen) VALUES (%s, 'online', now())
ON CONFLICT (device_id) DO UPDATE
    SET last_seen = GREATEST(device_status.last_seen, EXCLUDED.last_seen)
"""

_SQL_APPLY_STATUSES = """
INSERT INTO device_status (device_id, state, last_status_ts)
VALUES (%s, %s::device_state, %s)
ON CONFLICT (device_id) DO UPDATE
    SET state = EXCLUDED.state, last_status_ts = EXCLUDED.last_status_ts
"""


def build_conninfo(settings: Settings) -> str:
    """Construye el conninfo de PostgreSQL para el rol ingestor sin registrar secretos."""
    return make_conninfo(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user="ingestor",
        password=settings.db_password,
    )


def load_devices(conn: Connection[Any]) -> dict[str, str]:
    """Carga el mapa device_id -> site_id desde la tabla devices sin confirmar transacción."""
    with conn.cursor() as cur:
        cur.execute(_SQL_LOAD_DEVICES)
        return {str(row[0]): str(row[1]) for row in cur.fetchall()}


def insert_readings(conn: Connection[Any], rows: Sequence[ParsedReading]) -> int:
    """Inserta un lote de lecturas con unnest y devuelve el número de filas insertadas."""
    if not rows:
        return 0
    params = (
        [r.ts for r in rows],
        [r.device_id for r in rows],
        [r.seq for r in rows],
        [r.temp_c for r in rows],
        [r.humidity_pct for r in rows],
        [r.rssi_dbm for r in rows],
        [r.battery_pct for r in rows],
        [r.on_backup_power for r in rows],
    )
    with conn.cursor() as cur:
        cur.execute(_SQL_INSERT_READINGS, params)
        return max(cur.rowcount, 0)


def insert_rejections(conn: Connection[Any], rows: Sequence[Rejection]) -> None:
    """Inserta mensajes rechazados en rejected_messages sin confirmar transacción."""
    if not rows:
        return
    params = [(r.topic, r.payload_text, r.reason) for r in rows]
    with conn.cursor() as cur:
        cur.executemany(_SQL_INSERT_REJECTIONS, params)


def touch_devices(conn: Connection[Any], device_ids: Iterable[str]) -> None:
    """Actualiza last_seen con la hora del servidor una vez por dispositivo distinto."""
    distinct_ids = sorted(dict.fromkeys(device_ids))
    if not distinct_ids:
        return
    params = [(device_id,) for device_id in distinct_ids]
    with conn.cursor() as cur:
        cur.executemany(_SQL_TOUCH_DEVICES, params)


def apply_statuses(conn: Connection[Any], rows: Sequence[ParsedStatus]) -> None:
    """Aplica mensajes de estado en el orden recibido sobre device_status."""
    if not rows:
        return
    params = [(r.device_id, r.state, r.ts) for r in rows]
    with conn.cursor() as cur:
        cur.executemany(_SQL_APPLY_STATUSES, params)


class PgDb:
    """Envoltorio transaccional sobre psycopg_pool.ConnectionPool."""

    def __init__(self, conninfo: str, pool_size: int = 2) -> None:
        """Inicializa el pool de conexiones hacia PostgreSQL."""
        import psycopg_pool

        self._pool: ConnectionPool[Connection[Any]] = psycopg_pool.ConnectionPool(
            conninfo=conninfo,
            min_size=1,
            max_size=max(1, pool_size),
            open=True,
        )

    def run(self, fn: Callable[[Connection[Any]], T]) -> T:
        """Ejecuta fn dentro de una transacción con commit o rollback automático."""
        with self._pool.connection() as conn, conn.transaction():
            return fn(conn)

    def ping(self) -> bool:
        """Comprueba conectividad básica ejecutando SELECT 1."""
        try:
            with self._pool.connection(timeout=5.0) as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except psycopg.Error:
            return False

    def close(self) -> None:
        """Cierra ordenadamente el pool de conexiones."""
        self._pool.close()
