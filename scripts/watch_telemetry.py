"""Script de monitoreo y validación de telemetría MQTT en vivo."""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import jsonschema
import paho.mqtt.client as mqtt

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "telemetry.v1.schema.json"


def load_env_file(path: Path) -> dict[str, str]:
    """Lee un archivo .env simple sin dependencias externas."""
    env_vars: dict[str, str] = {}
    if not path.is_file():
        return env_vars
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                k, v = stripped.split("=", 1)
                env_vars[k.strip()] = v.strip().strip("'\"")
    return env_vars


def main() -> int:
    """Ejecuta la suscripción MQTT, valida el contrato y genera el reporte."""
    parser = argparse.ArgumentParser(
        description="Monitorea y valida la telemetría publicada en Mosquitto",
    )
    parser.add_argument(
        "--seconds",
        type=int,
        default=300,
        help="Tiempo de observación en segundos (por defecto 300)",
    )
    parser.add_argument(
        "--period-s",
        type=int,
        default=60,
        help="Periodo esperado de emisión en segundos (por defecto 60)",
    )
    args = parser.parse_args()

    env = load_env_file(REPO_ROOT / ".env")
    host = os.environ.get("MQTT_HOST") or env.get("MQTT_HOST", "localhost")
    port = int(os.environ.get("MQTT_PORT") or env.get("MQTT_PORT", "1883"))
    password = os.environ.get("MQTT_INGESTOR_PASSWORD") or env.get("MQTT_INGESTOR_PASSWORD", "")

    if not password:
        sys.stderr.write("Aviso: MQTT_INGESTOR_PASSWORD no encontrado en .env ni en entorno\n")

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    device_counts: dict[str, int] = {}
    invalid_payloads: list[str] = []

    def on_message(client: Any, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            validator.validate(payload)
            dev_id = payload.get("device_id")
            if dev_id:
                device_counts[dev_id] = device_counts.get(dev_id, 0) + 1
        except Exception as exc:
            invalid_payloads.append(f"{msg.topic}: {exc}")

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="watch-telemetry",
        protocol=mqtt.MQTTv311,
    )
    if password:
        client.username_pw_set(username="ingestor", password=password)

    client.on_message = on_message

    try:
        client.connect(host, port, keepalive=60)
    except Exception as exc:
        sys.stderr.write(f"Error conectando al broker MQTT {host}:{port}: {exc}\n")
        return 1

    # Suscripción normal (no compartida)
    client.subscribe("coldchain/v1/+/+/telemetry", qos=1)
    client.loop_start()

    print(f"Monitoreando telemetría durante {args.seconds} segundos...")
    try:
        time.sleep(args.seconds)
    finally:
        client.loop_stop()
        client.disconnect()

    expected = args.seconds // args.period_s
    all_devices = sorted(device_counts.keys())

    print("\n" + "=" * 55)
    print(f"{'Dispositivo':<16} {'Mensajes':<10} {'Esperados':<12} {'Estado'}")
    print("-" * 55)

    all_in_range = True
    for dev in all_devices:
        cnt = device_counts[dev]
        in_range = (expected - 1) <= cnt <= (expected + 1)
        status = "OK" if in_range else "FUERA_RANGO"
        if not in_range:
            all_in_range = False
        print(f"{dev:<16} {cnt:<10} {expected:<12} {status}")

    print("-" * 55)
    print(f"Total dispositivos: {len(all_devices)} (esperados 12)")
    print(f"Payloads inválidos: {len(invalid_payloads)}")

    pass_criteria = (len(all_devices) == 12) and all_in_range and (len(invalid_payloads) == 0)

    if pass_criteria:
        print("RESULTADO: PASS")
        return 0

    print("RESULTADO: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
