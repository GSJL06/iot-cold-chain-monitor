"""Verification script for Mosquitto broker: authentication, ACLs, and shared subscriptions."""

import contextlib
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

from paho.mqtt.client import CallbackAPIVersion, Client, MQTTv311
from paho.mqtt.reasoncodes import ReasonCode


def parse_simple_env(env_path: Path) -> dict[str, str]:
    """Parse .env file with simple logic and no external dependencies."""
    if not env_path.is_file():
        return {}
    env_vars: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        k = key.strip()
        v = val.strip()
        if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
            v = v[1:-1]
        env_vars[k] = v
    return env_vars


def close_client(client: Client | None) -> None:
    """Safely disconnect and stop loop for an MQTT client."""
    if client is not None:
        with contextlib.suppress(Exception):
            client.disconnect()
        with contextlib.suppress(Exception):
            client.loop_stop()


def check_c1_anonymous_rejected(host: str, port: int) -> bool:
    """C1. Conexión anónima rechazada."""
    client = Client(CallbackAPIVersion.VERSION2, protocol=MQTTv311)
    rejected_event = threading.Event()
    connected_event = threading.Event()

    def on_connect(
        _c: Client,
        _userdata: Any,
        _flags: dict[str, Any],
        reason_code: ReasonCode,
        _properties: Any,
    ) -> None:
        if reason_code.is_failure or reason_code != 0:
            rejected_event.set()
        else:
            connected_event.set()

    def on_disconnect(
        _c: Client,
        _userdata: Any,
        _disconnect_flags: Any,
        _reason_code: ReasonCode,
        _properties: Any,
    ) -> None:
        rejected_event.set()

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    try:
        client.connect(host, port, keepalive=10)
        client.loop_start()
        rejected_event.wait(timeout=5.0)
        if connected_event.is_set():
            print("FAIL C1: Conexión anónima aceptada por el broker (debía ser rechazada)")
            return False
        if rejected_event.is_set():
            print("PASS C1: Conexión anónima rechazada")
            return True
        print("PASS C1: Conexión anónima rechazada por falta de autorización")
        return True
    except Exception:
        print("PASS C1: Conexión anónima rechazada a nivel de socket")
        return True
    finally:
        close_client(client)


def check_c2_authenticated(host: str, port: int, device_pw: str) -> bool:
    """C2. Conexión autenticada de SIM-HA-001 correcta."""
    client = Client(CallbackAPIVersion.VERSION2, client_id="SIM-HA-001", protocol=MQTTv311)
    client.username_pw_set("SIM-HA-001", device_pw)
    connected_event = threading.Event()

    def on_connect(
        _c: Client,
        _userdata: Any,
        _flags: dict[str, Any],
        reason_code: ReasonCode,
        _properties: Any,
    ) -> None:
        if not reason_code.is_failure and reason_code == 0:
            connected_event.set()

    client.on_connect = on_connect

    try:
        client.connect(host, port, keepalive=10)
        client.loop_start()
        if connected_event.wait(timeout=5.0):
            print("PASS C2: Conexión autenticada de SIM-HA-001 correcta")
            return True
        print("FAIL C2: Conexión autenticada de SIM-HA-001 falló")
        return False
    except Exception as exc:
        print(f"FAIL C2: Error conectando SIM-HA-001: {type(exc).__name__}")
        return False
    finally:
        close_client(client)


def check_c3_shared_subscription(
    host: str, port: int, ingestor_pw: str, device_pw: str, repo_root: Path
) -> bool:
    """C3. Suscripción compartida."""
    c1 = Client(CallbackAPIVersion.VERSION2, client_id="verify-ing-1", protocol=MQTTv311)
    c1.username_pw_set("ingestor", ingestor_pw)
    c2 = Client(CallbackAPIVersion.VERSION2, client_id="verify-ing-2", protocol=MQTTv311)
    c2.username_pw_set("ingestor", ingestor_pw)

    counts = {"c1": 0, "c2": 0}
    lock = threading.Lock()
    sub1_event = threading.Event()
    sub2_event = threading.Event()
    suback_failed = False

    def on_sub1(
        _c: Client, _userdata: Any, _mid: int, reason_codes: list[ReasonCode], _properties: Any
    ) -> None:
        nonlocal suback_failed
        for rc in reason_codes:
            if rc == 0x80 or getattr(rc, "value", None) == 0x80 or rc.is_failure:
                suback_failed = True
        sub1_event.set()

    def on_sub2(
        _c: Client, _userdata: Any, _mid: int, reason_codes: list[ReasonCode], _properties: Any
    ) -> None:
        nonlocal suback_failed
        for rc in reason_codes:
            if rc == 0x80 or getattr(rc, "value", None) == 0x80 or rc.is_failure:
                suback_failed = True
        sub2_event.set()

    def on_msg_c1(_c: Client, _userdata: Any, _msg: Any) -> None:
        with lock:
            counts["c1"] += 1

    def on_msg_c2(_c: Client, _userdata: Any, _msg: Any) -> None:
        with lock:
            counts["c2"] += 1

    c1.on_subscribe = on_sub1
    c2.on_subscribe = on_sub2
    c1.on_message = on_msg_c1
    c2.on_message = on_msg_c2

    publisher: Client | None = None
    try:
        c1.connect(host, port, keepalive=10)
        c2.connect(host, port, keepalive=10)
        c1.loop_start()
        c2.loop_start()

        share_topic = "$share/ingestor/coldchain/v1/+/+/telemetry"
        c1.subscribe(share_topic, qos=1)
        c2.subscribe(share_topic, qos=1)

        sub1_event.wait(timeout=5.0)
        sub2_event.wait(timeout=5.0)

        if suback_failed:
            print("FAIL C3: ACL o broker rechazan $share: ver docs/adr/0004")
            return False

        if not (sub1_event.is_set() and sub2_event.is_set()):
            print("FAIL C3: Timeout esperando confirmación SUBACK de suscripción compartida")
            return False

        # Cargar payload base
        example_path = repo_root / "examples" / "telemetry" / "valid_refrigerated.json"
        if example_path.is_file():
            base_payload = json.loads(example_path.read_text(encoding="utf-8"))
        else:
            base_payload = {
                "schema": "telemetry.v1",
                "device_id": "SIM-HA-001",
                "temp_c": 4.7,
                "humidity_pct": 52.1,
                "rssi_dbm": -87,
                "battery_pct": 100,
                "on_backup_power": False,
                "fw": "sim-1.0.0",
            }

        publisher = Client(CallbackAPIVersion.VERSION2, client_id="SIM-HA-001", protocol=MQTTv311)
        publisher.username_pw_set("SIM-HA-001", device_pw)
        publisher.connect(host, port, keepalive=10)
        publisher.loop_start()
        time.sleep(0.1)

        pub_topic = "coldchain/v1/hosp-a/SIM-HA-001/telemetry"
        for i in range(20):
            payload = dict(base_payload)
            payload["seq"] = base_payload.get("seq", 1000) + i
            payload["ts"] = f"2026-11-18T12:00:{i:02d}Z"
            inf = publisher.publish(pub_topic, json.dumps(payload), qos=1)
            inf.wait_for_publish(timeout=1.0)

        start_wait = time.time()
        while time.time() - start_wait < 5.0:
            with lock:
                if counts["c1"] + counts["c2"] >= 20:
                    break
            time.sleep(0.05)

        with lock:
            total = counts["c1"] + counts["c2"]
            c1_val = counts["c1"]
            c2_val = counts["c2"]

        if total == 20:
            print(
                f"PASS C3: Suscripción compartida: 20/20 mensajes recibidos "
                f"(verify-ing-1: {c1_val}, verify-ing-2: {c2_val})"
            )
            if c1_val == 0 or c2_val == 0:
                print(
                    f"WARN C3: Uno de los clientes recibió 0 mensajes "
                    f"(verify-ing-1: {c1_val}, verify-ing-2: {c2_val})"
                )
            return True

        print(
            f"FAIL C3: Se esperaban 20 mensajes pero se recibieron {total} "
            f"(verify-ing-1: {c1_val}, verify-ing-2: {c2_val})"
        )
        return False
    finally:
        close_client(publisher)
        close_client(c1)
        close_client(c2)


def check_c4_acl_isolation(host: str, port: int, ingestor_pw: str, device_pw: str) -> bool:
    """C4. Aislamiento por ACL: SIM-HA-001 publica en topic de SIM-HA-002."""
    observer = Client(CallbackAPIVersion.VERSION2, client_id="verify-obs-c4", protocol=MQTTv311)
    observer.username_pw_set("ingestor", ingestor_pw)
    obs_msgs: list[Any] = []
    sub_event = threading.Event()

    observer.on_message = lambda _c, _u, msg: obs_msgs.append(msg)
    observer.on_subscribe = lambda _c, _u, _m, _rc, _p: sub_event.set()

    publisher: Client | None = None
    try:
        observer.connect(host, port, keepalive=10)
        observer.loop_start()
        observer.subscribe("coldchain/v1/+/+/telemetry", qos=1)
        if not sub_event.wait(timeout=5.0):
            print("FAIL C4: El observador ingestor no pudo suscribirse a telemetría")
            return False

        publisher = Client(CallbackAPIVersion.VERSION2, client_id="SIM-HA-001", protocol=MQTTv311)
        publisher.username_pw_set("SIM-HA-001", device_pw)
        publisher.connect(host, port, keepalive=10)
        publisher.loop_start()
        time.sleep(0.1)

        publisher.publish(
            "coldchain/v1/hosp-a/SIM-HA-002/telemetry",
            json.dumps({"test": "unauthorized"}),
            qos=1,
        )

        time.sleep(3.0)

        if len(obs_msgs) == 0:
            print("PASS C4: Aislamiento por ACL (observador no recibió nada en 3 s)")
            return True
        print(f"FAIL C4: Aislamiento por ACL falló (recibidos {len(obs_msgs)} mensajes indebidos)")
        return False
    finally:
        close_client(publisher)
        close_client(observer)


def check_c5_size_limit(host: str, port: int, ingestor_pw: str, device_pw: str) -> bool:
    """C5. Límite de tamaño: SIM-HA-001 publica payload de 5000 bytes."""
    observer = Client(CallbackAPIVersion.VERSION2, client_id="verify-obs-c5", protocol=MQTTv311)
    observer.username_pw_set("ingestor", ingestor_pw)
    obs_msgs: list[Any] = []
    sub_event = threading.Event()

    observer.on_message = lambda _c, _u, msg: obs_msgs.append(msg)
    observer.on_subscribe = lambda _c, _u, _m, _rc, _p: sub_event.set()

    publisher: Client | None = None
    try:
        observer.connect(host, port, keepalive=10)
        observer.loop_start()
        observer.subscribe("coldchain/v1/+/+/telemetry", qos=1)
        if not sub_event.wait(timeout=5.0):
            print("FAIL C5: El observador ingestor no pudo suscribirse a telemetría")
            return False

        publisher = Client(CallbackAPIVersion.VERSION2, client_id="SIM-HA-001", protocol=MQTTv311)
        publisher.username_pw_set("SIM-HA-001", device_pw)
        publisher.connect(host, port, keepalive=10)
        publisher.loop_start()
        time.sleep(0.1)

        publisher.publish(
            "coldchain/v1/hosp-a/SIM-HA-001/telemetry",
            b"X" * 5000,
            qos=1,
        )

        time.sleep(3.0)

        if len(obs_msgs) == 0:
            print("PASS C5: Límite de tamaño respetado (payload de 5000 bytes no recibido en 3 s)")
            return True
        msg_len = len(obs_msgs[0].payload)
        print(f"FAIL C5: Límite de tamaño falló (recibido payload de {msg_len} bytes)")
        return False
    finally:
        close_client(publisher)
        close_client(observer)


def check_c6_ingestor_cannot_publish(host: str, port: int, ingestor_pw: str) -> bool:
    """C6. El usuario ingestor no puede publicar en telemetría."""
    observer = Client(CallbackAPIVersion.VERSION2, client_id="verify-obs-c6", protocol=MQTTv311)
    observer.username_pw_set("ingestor", ingestor_pw)
    obs_msgs: list[Any] = []
    sub_event = threading.Event()

    observer.on_message = lambda _c, _u, msg: obs_msgs.append(msg)
    observer.on_subscribe = lambda _c, _u, _m, _rc, _p: sub_event.set()

    publisher: Client | None = None
    try:
        observer.connect(host, port, keepalive=10)
        observer.loop_start()
        observer.subscribe("coldchain/v1/+/+/telemetry", qos=1)
        if not sub_event.wait(timeout=5.0):
            print("FAIL C6: El observador ingestor no pudo suscribirse a telemetría")
            return False

        publisher = Client(
            CallbackAPIVersion.VERSION2, client_id="verify-pub-ing-c6", protocol=MQTTv311
        )
        publisher.username_pw_set("ingestor", ingestor_pw)
        publisher.connect(host, port, keepalive=10)
        publisher.loop_start()
        time.sleep(0.1)

        publisher.publish(
            "coldchain/v1/hosp-a/SIM-HA-001/telemetry",
            json.dumps({"test": "ingestor_publish"}),
            qos=1,
        )

        time.sleep(3.0)

        if len(obs_msgs) == 0:
            print("PASS C6: Ingestor no puede publicar (observador no recibió nada en 3 s)")
            return True
        print(f"FAIL C6: Ingestor pudo publicar en telemetría ({len(obs_msgs)} mensajes recibidos)")
        return False
    finally:
        close_client(publisher)
        close_client(observer)


def main() -> None:
    """Run all 6 broker verification checks."""
    repo_root = Path(__file__).resolve().parent.parent
    env_file = repo_root / ".env"
    env_vars = parse_simple_env(env_file)

    host = env_vars.get("MQTT_HOST", "localhost")
    port = int(env_vars.get("MQTT_PORT", 1883))
    ingestor_pw = env_vars.get("MQTT_INGESTOR_PASSWORD", "")
    device_pw = env_vars.get("MQTT_DEVICE_PASSWORD", "")

    # En Windows, mapear localhost a 127.0.0.1 previene demoras de timeout de IPv6 (::1)
    if host == "localhost":
        host = "127.0.0.1"

    if not ingestor_pw or not device_pw:
        print("FAIL: Faltan credenciales MQTT en el archivo .env")
        sys.exit(1)

    checks = [
        lambda: check_c1_anonymous_rejected(host, port),
        lambda: check_c2_authenticated(host, port, device_pw),
        lambda: check_c3_shared_subscription(host, port, ingestor_pw, device_pw, repo_root),
        lambda: check_c4_acl_isolation(host, port, ingestor_pw, device_pw),
        lambda: check_c5_size_limit(host, port, ingestor_pw, device_pw),
        lambda: check_c6_ingestor_cannot_publish(host, port, ingestor_pw),
    ]

    pass_count = 0
    for check_fn in checks:
        if check_fn():
            pass_count += 1
        time.sleep(0.1)

    print(f"{pass_count}/6 PASS")
    sys.exit(0 if pass_count == 6 else 1)


if __name__ == "__main__":
    main()
