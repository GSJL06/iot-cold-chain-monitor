"""Prueba de carga MQTT → ingestor → TimescaleDB midiendo pérdidas y latencia p95."""

import argparse
import contextlib
import json
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import _common
import paho.mqtt.client as mqtt
import psycopg

_SQL_DEVICES = """
SELECT device_id, site_id, storage_class::text, min_c, max_c
FROM devices
ORDER BY device_id
"""

_SQL_LOAD_STATS = """
SELECT
    count(*),
    percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM received_at - ts))
FROM readings
WHERE seq >= 1000000000
  AND ts >= %s
  AND received_at >= %s
"""


@dataclass(frozen=True)
class DeviceTarget:
    """Metadatos de dispositivo necesarios para publicar lecturas de carga válidas."""

    device_id: str
    site_id: str
    storage_class: str
    mid_temp_c: float


def _close_clients(clients: dict[str, mqtt.Client]) -> None:
    """Desconecta y detiene los hilos de red de todos los clientes MQTT."""
    for client in clients.values():
        with contextlib.suppress(Exception):
            client.disconnect()
        with contextlib.suppress(Exception):
            client.loop_stop()


def main() -> int:
    """Publica lecturas a tasa fija y valida 0 pérdidas y latencia p95 < 2 s."""
    parser = argparse.ArgumentParser(
        description="Prueba de carga sostenida del pipeline MQTT -> ingestor -> TimescaleDB",
    )
    parser.add_argument(
        "--rate",
        type=int,
        default=20,
        help="Mensajes por segundo totales (por defecto 20)",
    )
    parser.add_argument(
        "--seconds",
        type=int,
        default=60,
        help="Duración de la emisión en segundos (por defecto 60)",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=15,
        help="Espera máxima en segundos tras finalizar para consultar BD (por defecto 15)",
    )
    args = parser.parse_args()

    if args.rate < 1 or args.seconds < 1 or args.wait < 1:
        sys.stderr.write("Error: --rate, --seconds y --wait deben ser >= 1\n")
        return 1

    env = _common.load_env()
    mqtt_host = _common.resolve_local_host(
        _common.get_env_value(env, "MQTT_HOST", "localhost") or "localhost"
    )
    mqtt_port = int(_common.get_env_value(env, "MQTT_PORT", "1883") or "1883")
    device_pw = _common.get_env_value(env, "MQTT_DEVICE_PASSWORD")
    if not device_pw:
        sys.stderr.write("Error: falta MQTT_DEVICE_PASSWORD en .env o entorno\n")
        return 1

    try:
        conn = _common.db_connect()
    except (psycopg.Error, RuntimeError, OSError, ValueError) as exc:
        sys.stderr.write(f"Error conectando a la base de datos: {type(exc).__name__}\n")
        return 1

    clients: dict[str, mqtt.Client] = {}
    try:
        offset_s = _common.clock_offset_s(conn)
        with conn.cursor() as cur:
            cur.execute(_SQL_DEVICES)
            dev_rows = cur.fetchall()
            cur.execute("SELECT now()")
            now_row = cur.fetchone()

        if len(dev_rows) != 12:
            sys.stderr.write(f"Error: se esperaban 12 dispositivos en BD, hay {len(dev_rows)}\n")
            return 1
        if now_row is None or now_row[0] is None:
            sys.stderr.write("Error: no se pudo leer now() de la base de datos\n")
            return 1

        db_start = (
            now_row[0].replace(tzinfo=UTC)
            if now_row[0].tzinfo is None
            else now_row[0].astimezone(UTC)
        )

        devices = [
            DeviceTarget(
                device_id=str(row[0]),
                site_id=str(row[1]),
                storage_class=str(row[2]),
                mid_temp_c=round((float(row[3]) + float(row[4])) / 2.0, 2),
            )
            for row in dev_rows
        ]

        connect_events: dict[str, threading.Event] = {}
        connect_failures: list[str] = []

        for dev in devices:
            ev = threading.Event()
            connect_events[dev.device_id] = ev

            def _make_on_connect(dev_id: str, done_ev: threading.Event) -> Any:
                def on_connect(
                    _c: mqtt.Client,
                    _userdata: Any,
                    _flags: mqtt.ConnectFlags,
                    reason_code: mqtt.ReasonCode,
                    _properties: Any,
                ) -> None:
                    if reason_code.is_failure:
                        connect_failures.append(dev_id)
                    done_ev.set()

                return on_connect

            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"load-{dev.device_id}",
                protocol=mqtt.MQTTv311,
            )
            client.username_pw_set(username=dev.device_id, password=device_pw)
            client.max_inflight_messages_set(200)
            client.on_connect = _make_on_connect(dev.device_id, ev)
            client.connect(mqtt_host, mqtt_port, keepalive=60)
            client.loop_start()
            clients[dev.device_id] = client

        for dev_id, ev in connect_events.items():
            if not ev.wait(timeout=5.0) or dev_id in connect_failures:
                sys.stderr.write(f"Error conectando cliente MQTT para {dev_id}\n")
                return 1

        total_messages = args.rate * args.seconds
        seq_counts = {dev.device_id: 0 for dev in devices}
        last_ts_by_dev: dict[str, datetime] = {}
        min_sent_ts: datetime | None = None
        publishes: list[mqtt.MQTTMessageInfo] = []

        start_mono = time.monotonic()
        for idx in range(total_messages):
            target_mono = start_mono + idx / args.rate
            sleep_s = target_mono - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)

            dev = devices[idx % len(devices)]
            seq_counts[dev.device_id] += 1
            seq = 1_000_000_000 + seq_counts[dev.device_id]

            ts_dt = _common.truncate_ms(datetime.now(UTC) + timedelta(seconds=offset_s))
            prev_ts = last_ts_by_dev.get(dev.device_id)
            if prev_ts is not None and ts_dt <= prev_ts:
                ts_dt = prev_ts + timedelta(milliseconds=1)
            last_ts_by_dev[dev.device_id] = ts_dt
            if min_sent_ts is None or ts_dt < min_sent_ts:
                min_sent_ts = ts_dt

            payload: dict[str, Any] = {
                "schema": "telemetry.v1",
                "device_id": dev.device_id,
                "ts": _common.format_ts_ms(ts_dt),
                "seq": seq,
                "temp_c": dev.mid_temp_c,
                "rssi_dbm": -75,
                "battery_pct": 100,
                "on_backup_power": False,
                "fw": "load-1.0.0",
            }
            if dev.storage_class != "ULTRA_FROZEN":
                payload["humidity_pct"] = 50.0

            topic = f"coldchain/v1/{dev.site_id}/{dev.device_id}/telemetry"
            info = clients[dev.device_id].publish(
                topic,
                json.dumps(payload, separators=(",", ":")),
                qos=1,
            )
            publishes.append(info)

        for info in publishes:
            info.wait_for_publish(timeout=5.0)
        _close_clients(clients)
        clients.clear()

        if min_sent_ts is None:
            min_sent_ts = db_start

        window_received_start = db_start - timedelta(seconds=2)
        stored = 0
        p95_s: float | None = None
        wait_deadline = time.monotonic() + args.wait

        while True:
            with conn.cursor() as cur:
                cur.execute(_SQL_LOAD_STATS, (min_sent_ts, window_received_start))
                row = cur.fetchone()
                if row is not None:
                    stored = int(row[0])
                    p95_s = float(row[1]) if row[1] is not None else None
            if stored >= total_messages or time.monotonic() >= wait_deadline:
                break
            time.sleep(0.25)

        lost = total_messages - stored
        p95_display = f"{p95_s:.3f}" if p95_s is not None else "N/A"
        passed = lost == 0 and p95_s is not None and p95_s < 2.0

        print(f"Enviados: {total_messages}")
        print(f"Almacenados: {stored}")
        print(f"Perdidos: {lost}")
        print(f"Latencia p95: {p95_display} s")
        print(f"Desfase de reloj: {offset_s * 1000.0:+.1f} ms")
        print(f"RESULTADO: {'PASS' if passed else 'FAIL'}")
        print("Limpieza (admin): DELETE FROM readings WHERE seq >= 1000000000;")
        return 0 if passed else 1
    except Exception as exc:
        sys.stderr.write(f"Error durante la prueba de carga: {type(exc).__name__}\n")
        return 1
    finally:
        _close_clients(clients)
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
