"""Pruebas unitarias para el publicador MQTT del simulador."""

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import paho.mqtt.client as mqtt
from simulator.config import load_catalog, load_fleet, load_plan, load_thermal
from simulator.fleet import Fleet
from simulator.publisher import SimPublisher

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"
FLEET_PATH = CONFIG_DIR / "fleet.yaml"
THERMAL_PATH = CONFIG_DIR / "thermal_models.yaml"
CATALOG_PATH = CONFIG_DIR / "scenario_catalog.yaml"
PLAN_DEMO_PATH = CONFIG_DIR / "scenarios" / "demo.yaml"
SCHEMA_PATH = REPO_ROOT / "schemas" / "telemetry.v1.schema.json"

FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class PublishedMessage:
    """Registro de mensaje publicado."""

    def __init__(
        self,
        topic: str,
        payload: str | bytes | None,
        qos: int,
        retain: bool,
    ) -> None:
        self.topic = topic
        self.payload = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
        self.qos = qos
        self.retain = retain
        self.data: dict[str, Any] = json.loads(self.payload)


class FakeClient:
    """Cliente MQTT simulado para registrar llamadas sin abrir sockets."""

    def __init__(
        self,
        device_id: str,
        callback_api_version: Any = mqtt.CallbackAPIVersion.VERSION2,
        protocol: Any = mqtt.MQTTv311,
    ) -> None:
        self.device_id = device_id
        self.client_id = device_id
        self.callback_api_version = callback_api_version
        self.protocol = protocol
        self.username: str | None = None
        self.password: str | None = None
        self.max_inflight: int | None = None
        self.will: dict[str, Any] | None = None
        self.connected = False
        self.connect_host: str | None = None
        self.connect_port: int | None = None
        self.loop_started = False
        self.loop_stopped = False
        self.disconnected = False
        self.publishes: list[PublishedMessage] = []

    def username_pw_set(self, username: str, password: str | None = None) -> None:
        self.username = username
        self.password = password

    def max_inflight_messages_set(self, inflight: int) -> None:
        self.max_inflight = inflight

    def will_set(
        self,
        topic: str,
        payload: str | bytes | None = None,
        qos: int = 0,
        retain: bool = False,
    ) -> None:
        payload_str = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
        self.will = {
            "topic": topic,
            "payload": payload_str,
            "qos": qos,
            "retain": retain,
            "data": json.loads(payload_str),
        }

    def connect(
        self,
        host: str,
        port: int = 1883,
        keepalive: int = 60,
        bind_address: str = "",
    ) -> int:
        self.connected = True
        self.connect_host = host
        self.connect_port = port
        return 0

    def loop_start(self) -> int:
        self.loop_started = True
        return 0

    def loop_stop(self, force: bool = False) -> int:
        self.loop_stopped = True
        return 0

    def disconnect(self, reasoncode: Any = None, properties: Any = None) -> int:
        self.disconnected = True
        return 0

    def publish(
        self,
        topic: str,
        payload: str | bytes | None = None,
        qos: int = 0,
        retain: bool = False,
    ) -> Any:
        msg = PublishedMessage(topic, payload, qos, retain)
        self.publishes.append(msg)
        return msg


def test_default_client_factory_version2() -> None:
    """Verifica que el factory por defecto configure CallbackAPIVersion.VERSION2 y MQTTv311."""
    client = SimPublisher._default_client_factory("SIM-TEST-001")
    assert isinstance(client, mqtt.Client)
    assert client._callback_api_version == mqtt.CallbackAPIVersion.VERSION2
    assert client._client_id == b"SIM-TEST-001"
    assert client._protocol == mqtt.MQTTv311


def test_publisher_lifecycle_and_invariants() -> None:
    """Prueba el ciclo de vida completo del publicador con FakeClient.

    Verifica:
    a) 12 clientes con client_id, username correctos y VERSION2.
    b) will_set con status offline, QoS 1, retain True.
    c) status online al conectar (QoS 1, retain True) para los 12.
    d) telemetría al topic correcto con QoS 1 y retain False.
    e) con backfill_hours=1, lecturas ts < anchor primero y luego ts >= anchor.
    f) al activar stop_event, status offline (QoS 1, retain True) para los 12 y desconexión.
    g) validación estricta de payloads con schemas/telemetry.v1.schema.json.
    """
    fleet_cfg = load_fleet(FLEET_PATH)
    thermal_cfg = load_thermal(THERMAL_PATH)
    catalog = load_catalog(CATALOG_PATH)
    plan = load_plan(PLAN_DEMO_PATH)

    dev_by_id = {d.device_id: d for d in fleet_cfg.devices}
    assert len(dev_by_id) == 12

    created_clients: dict[str, FakeClient] = {}
    all_publishes: list[tuple[str, PublishedMessage]] = []
    stop_event = threading.Event()
    live_telemetry_count = [0]

    def fake_factory(device_id: str) -> FakeClient:
        fc = FakeClient(device_id)
        original_publish = fc.publish

        def hooked_publish(
            topic: str,
            payload: str | bytes | None = None,
            qos: int = 0,
            retain: bool = False,
        ) -> PublishedMessage:
            msg = original_publish(topic, payload, qos, retain)
            all_publishes.append((device_id, msg))
            if (
                msg.data.get("schema") == "telemetry.v1"
                and msg.data["ts"] >= "2026-01-01T12:00:00Z"
            ):
                live_telemetry_count[0] += 1
                if live_telemetry_count[0] >= 12:
                    stop_event.set()
            return msg

        fc.publish = hooked_publish  # type: ignore[assignment]
        created_clients[device_id] = fc
        return fc

    fleet = Fleet(
        fleet_cfg=fleet_cfg,
        thermal_cfg=thermal_cfg,
        catalog=catalog,
        plan=plan,
        start_ts=FIXED_NOW,
        backfill_min=0,
    )

    dummy_secret = "".join(["dummy", "-", "credential", "-", "123"])
    publisher = SimPublisher(
        fleet=fleet,
        host="mock-broker",
        port=1883,
        password=dummy_secret,
        client_factory=fake_factory,
        now=lambda: FIXED_NOW,
        sleep=lambda _s: None,
    )

    publisher.run(backfill_hours=1, stop_event=stop_event)

    # a) 12 clientes con client_id, username correctos y VERSION2
    assert len(created_clients) == 12, f"Esperados 12 clientes, creados: {len(created_clients)}"
    for dev_id in dev_by_id:
        c = created_clients[dev_id]
        assert c.client_id == dev_id
        assert c.username == dev_id
        assert c.password == dummy_secret
        assert c.max_inflight == 200
        assert c.callback_api_version == mqtt.CallbackAPIVersion.VERSION2
        assert c.connected is True
        assert c.connect_host == "mock-broker"
        assert c.connect_port == 1883
        assert c.loop_started is True

    # b) will_set registra topic de status con offline, QoS 1, retain True
    for dev_id, dev in dev_by_id.items():
        c = created_clients[dev_id]
        assert c.will is not None, f"will_set no llamado para {dev_id}"
        assert c.will["topic"] == f"coldchain/v1/{dev.site_id}/{dev_id}/status"
        assert c.will["qos"] == 1
        assert c.will["retain"] is True
        assert c.will["data"] == {
            "schema": "status.v1",
            "device_id": dev_id,
            "state": "offline",
            "ts": "2026-01-01T12:00:00Z",
        }

    # c) al conectar publica status online (QoS 1, retain True)
    for dev_id, dev in dev_by_id.items():
        c = created_clients[dev_id]
        assert len(c.publishes) > 0, f"Sin publicaciones para {dev_id}"
        first_msg = c.publishes[0]
        assert first_msg.topic == f"coldchain/v1/{dev.site_id}/{dev_id}/status"
        assert first_msg.qos == 1
        assert first_msg.retain is True
        assert first_msg.data == {
            "schema": "status.v1",
            "device_id": dev_id,
            "state": "online",
            "ts": "2026-01-01T12:00:00Z",
        }

    # d) telemetría al topic correcto con QoS 1 y retain False
    telemetry_publishes = [
        (dev_id, msg) for dev_id, msg in all_publishes if msg.data.get("schema") == "telemetry.v1"
    ]
    assert len(telemetry_publishes) >= 732, (
        f"Se esperaban al menos 732 telemetrías (720 backfill + 12 live), "
        f"obtenidas: {len(telemetry_publishes)}"
    )

    for dev_id, msg in telemetry_publishes:
        dev = dev_by_id[dev_id]
        expected_topic = f"coldchain/v1/{dev.site_id}/{dev_id}/telemetry"
        assert msg.topic == expected_topic
        assert msg.qos == 1
        assert msg.retain is False
        assert msg.data["device_id"] == dev_id

    # e) con backfill_hours=1 se publican primero ts < anchor y luego ts >= anchor
    anchor_iso = "2026-01-01T12:00:00Z"
    backfill_telemetry = [m for _, m in telemetry_publishes if m.data["ts"] < anchor_iso]
    live_telemetry = [m for _, m in telemetry_publishes if m.data["ts"] >= anchor_iso]

    # 12 dispositivos * 60 minutos de backfill = 720 lecturas de backfill
    assert len(backfill_telemetry) == 720, (
        f"Esperadas 720 lecturas en backfill, obtenidas: {len(backfill_telemetry)}"
    )
    assert len(live_telemetry) >= 12, (
        f"Esperadas al menos 12 lecturas en directo, obtenidas: {len(live_telemetry)}"
    )

    # Las primeras 720 telemetrías deben ser estrictamente < anchor_iso
    for i in range(720):
        _, m = telemetry_publishes[i]
        assert m.data["ts"] < anchor_iso, f"Mensaje {i} con ts {m.data['ts']} debería ser backfill"

    # A partir del índice 720 deben ser estrictamente >= anchor_iso
    for i in range(720, len(telemetry_publishes)):
        _, m = telemetry_publishes[i]
        assert m.data["ts"] >= anchor_iso, f"Mensaje {i} con ts {m.data['ts']} debería ser live"

    # f) al activar stop_event se publica status offline (QoS 1, retain True) para los 12
    for dev_id, dev in dev_by_id.items():
        c = created_clients[dev_id]
        last_msg = c.publishes[-1]
        assert last_msg.topic == f"coldchain/v1/{dev.site_id}/{dev_id}/status"
        assert last_msg.qos == 1
        assert last_msg.retain is True
        assert last_msg.data["schema"] == "status.v1"
        assert last_msg.data["device_id"] == dev_id
        assert last_msg.data["state"] == "offline"
        assert c.loop_stopped is True
        assert c.disconnected is True

    # g) validación con schemas/telemetry.v1.schema.json (al menos 5 lecturas reales)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    # Validamos una muestra representativa de 30 lecturas (backfill y en directo)
    sample_to_validate = telemetry_publishes[:15] + telemetry_publishes[-15:]
    assert len(sample_to_validate) == 30
    for _dev_id, msg in sample_to_validate:
        jsonschema.validate(instance=msg.data, schema=schema)
