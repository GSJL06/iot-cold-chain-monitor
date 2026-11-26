"""Verificación extremo a extremo del ingestor contra Mosquitto y TimescaleDB."""

import contextlib
import json
import sys
import threading
import time
from datetime import UTC, timedelta
from typing import Any

import _common
import paho.mqtt.client as mqtt
import psycopg

DEVICE_ID = "SIM-HA-001"
TOPIC = f"coldchain/v1/hosp-a/{DEVICE_ID}/telemetry"


def _close_client(client: mqtt.Client | None) -> None:
    """Cierra limpiamente el cliente MQTT si sigue activo."""
    if client is not None:
        with contextlib.suppress(Exception):
            client.disconnect()
        with contextlib.suppress(Exception):
            client.loop_stop()


def _publish_json(client: mqtt.Client, payload: dict[str, Any]) -> None:
    """Publica un payload JSON en QoS 1 esperando confirmación PUBACK del broker."""
    info = client.publish(TOPIC, json.dumps(payload, separators=(",", ":")), qos=1)
    info.wait_for_publish(timeout=5.0)


def main() -> int:
    """Ejecuta las 5 pruebas en vivo (deduplicación y 4 rechazos) y devuelve 0 si pasan 5/5."""
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

    client: mqtt.Client | None = None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT now()")
            row = cur.fetchone()
            if row is None or row[0] is None:
                sys.stderr.write("Error: no se pudo leer now() de la base de datos\n")
                return 1
            db_now = row[0].replace(tzinfo=UTC) if row[0].tzinfo is None else row[0].astimezone(UTC)

        connected_event = threading.Event()
        connect_failed = [False]

        def on_connect(
            _c: mqtt.Client,
            _userdata: Any,
            _flags: mqtt.ConnectFlags,
            reason_code: mqtt.ReasonCode,
            _properties: Any,
        ) -> None:
            if reason_code.is_failure:
                connect_failed[0] = True
            connected_event.set()

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="verify-ingestor",
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(username=DEVICE_ID, password=device_pw)
        client.on_connect = on_connect
        client.connect(mqtt_host, mqtt_port, keepalive=30)
        client.loop_start()

        if not connected_event.wait(timeout=5.0) or connect_failed[0]:
            sys.stderr.write(f"Error conectando a Mosquitto en {mqtt_host}:{mqtt_port}\n")
            return 1

        # (a) Lectura válida enviada DOS veces idéntica para verificar deduplicación
        ts_valid = _common.truncate_ms(db_now - timedelta(days=2))
        valid_payload = {
            "schema": "telemetry.v1",
            "device_id": DEVICE_ID,
            "ts": _common.format_ts_ms(ts_valid),
            "seq": 1000000001,
            "temp_c": 4.5,
            "humidity_pct": 55.0,
            "rssi_dbm": -70,
            "battery_pct": 100,
            "on_backup_power": False,
            "fw": "verify-1",
        }
        _publish_json(client, valid_payload)
        _publish_json(client, valid_payload)

        # Base común para los 4 rechazos verificables a través del broker.
        # Nota: unknown_device no se prueba aquí porque Mosquitto ACL y passwd solo
        # permiten publicar a usuarios que corresponden a dispositivos existentes;
        # queda cubierto por la prueba de integración en BD.
        ts_now_str = _common.format_ts_ms(db_now)
        base_reading = {
            "schema": "telemetry.v1",
            "device_id": DEVICE_ID,
            "ts": ts_now_str,
            "temp_c": 4.5,
            "humidity_pct": 55.0,
            "rssi_dbm": -70,
            "battery_pct": 100,
            "on_backup_power": False,
        }

        # (b) schema_invalid: temp_c=999
        _publish_json(client, {**base_reading, "seq": 777000001, "temp_c": 999})
        # (c) unsupported_schema: telemetry.v2
        _publish_json(client, {**base_reading, "seq": 777000002, "schema": "telemetry.v2"})
        # (d) topic_mismatch: device_id SIM-HA-002 en topic de SIM-HA-001
        _publish_json(client, {**base_reading, "seq": 777000003, "device_id": "SIM-HA-002"})
        # (e) clock_skew: ts = ahora + 10 min
        ts_future_str = _common.format_ts_ms(db_now + timedelta(minutes=10))
        _publish_json(client, {**base_reading, "seq": 777000004, "ts": ts_future_str})

        expected_rejections = [
            ("777000001", "schema_invalid", "b_schema_invalid"),
            ("777000002", "unsupported_schema", "c_unsupported_schema"),
            ("777000003", "topic_mismatch", "d_topic_mismatch"),
            ("777000004", "clock_skew", "e_clock_skew"),
        ]

        dedup_ok = False
        rej_results: dict[str, str | None] = {m: None for m, _, _ in expected_rejections}
        deadline = time.monotonic() + 10.0

        while time.monotonic() < deadline:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM readings WHERE device_id = %s AND ts = %s",
                    (DEVICE_ID, ts_valid),
                )
                cnt_row = cur.fetchone()
                dedup_ok = cnt_row is not None and int(cnt_row[0]) == 1

                for marker, _, _ in expected_rejections:
                    cur.execute(
                        "SELECT reason FROM rejected_messages "
                        "WHERE received_at >= %s AND payload LIKE %s "
                        "ORDER BY id DESC LIMIT 1",
                        (db_now, f"%{marker}%"),
                    )
                    rej_row = cur.fetchone()
                    rej_results[marker] = str(rej_row[0]) if rej_row is not None else None

            if dedup_ok and all(
                rej_results[marker] == expected for marker, expected, _ in expected_rejections
            ):
                break
            time.sleep(0.25)

        checks: list[tuple[str, bool, str]] = [
            (
                "a_dedup_valid_reading",
                dedup_ok,
                "1 fila en readings" if dedup_ok else "no se encontró exactamente 1 fila",
            )
        ]
        for marker, expected, label in expected_rejections:
            actual = rej_results[marker]
            ok = actual == expected
            detail = (
                f"reason={actual}" if actual is not None else "no encontrado en rejected_messages"
            )
            checks.append((label, ok, detail))

        passed = 0
        for label, ok, detail in checks:
            status = "PASS" if ok else "FAIL"
            if ok:
                passed += 1
            print(f"{status} {label}: {detail}")

        print(f"{passed}/5 PASS")
        return 0 if passed == 5 else 1
    except Exception as exc:
        sys.stderr.write(f"Error durante la verificación: {type(exc).__name__}\n")
        return 1
    finally:
        _close_client(client)
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
