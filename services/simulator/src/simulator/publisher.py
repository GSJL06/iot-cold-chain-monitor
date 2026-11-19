"""Publicador MQTT multicliente para la flota de dispositivos del simulador."""

import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import paho.mqtt.client as mqtt

from simulator.clock import format_ts
from simulator.fleet import Fleet

logger = logging.getLogger("simulator")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


class SimPublisher:
    """Publicador MQTT con un cliente paho independiente por dispositivo."""

    def __init__(
        self,
        fleet: Fleet,
        host: str,
        port: int,
        password: str,
        client_factory: Callable[[str], Any] | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        """Inicializa el publicador inyectando dependencias opcionales para pruebas."""
        self._fleet = fleet
        self._host = host
        self._port = port
        self._password = password
        self._client_factory = client_factory or self._default_client_factory
        self._now_fn = now or (lambda: datetime.now(UTC))
        self._sleep_fn = sleep or time.sleep

    @staticmethod
    def _default_client_factory(device_id: str) -> mqtt.Client:
        """Crea un cliente paho 2.x configurado con CallbackAPIVersion.VERSION2."""
        return mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=device_id,
            protocol=mqtt.MQTTv311,
        )

    def _log(self, level: str, event: str, **kwargs: Any) -> None:
        """Emite una entrada de log estructurado en JSON de una sola línea."""
        entry: dict[str, Any] = {
            "ts": format_ts(self._now_fn()),
            "level": level.upper(),
            "service": "simulator",
            "event": event,
            **kwargs,
        }
        line = json.dumps(entry, separators=(",", ":"), ensure_ascii=False)
        if level.lower() == "error":
            logger.error(line)
        elif level.lower() == "warning":
            logger.warning(line)
        else:
            logger.info(line)

    def run(self, backfill_hours: int, stop_event: threading.Event) -> None:
        """Ejecuta el ciclo de vida de publicación: conexión, backfill, directo y cierre."""
        now_dt = self._now_fn().replace(microsecond=0)
        start_ts = now_dt - timedelta(hours=backfill_hours)
        backfill_min = backfill_hours * 60
        anchor_ts = start_ts + timedelta(minutes=backfill_min)

        # Reconstruir la flota con start_ts y backfill_min si tiene configuración
        if hasattr(self._fleet, "_fleet_cfg"):
            fleet = Fleet(
                fleet_cfg=self._fleet._fleet_cfg,
                thermal_cfg=self._fleet._thermal_cfg,
                catalog=self._fleet._catalog,
                plan=self._fleet._plan,
                start_ts=start_ts,
                backfill_min=backfill_min,
                seed=self._fleet._seed,
            )
            fleet_cfg = self._fleet._fleet_cfg
        else:
            fleet = self._fleet
            fleet_cfg = getattr(fleet, "_fleet_cfg", None)

        clients: dict[str, Any] = {}
        devices = fleet_cfg.devices if fleet_cfg is not None else ()

        for dev in devices:
            client = self._client_factory(dev.device_id)
            client.username_pw_set(username=dev.device_id, password=self._password)
            client.max_inflight_messages_set(200)

            status_topic = f"coldchain/v1/{dev.site_id}/{dev.device_id}/status"
            lwt_payload = json.dumps(
                {
                    "schema": "status.v1",
                    "device_id": dev.device_id,
                    "state": "offline",
                    "ts": format_ts(now_dt),
                },
                separators=(",", ":"),
                ensure_ascii=False,
            )
            client.will_set(status_topic, payload=lwt_payload, qos=1, retain=True)
            client.connect(self._host, self._port, keepalive=60)
            client.loop_start()

            online_payload = json.dumps(
                {
                    "schema": "status.v1",
                    "device_id": dev.device_id,
                    "state": "online",
                    "ts": format_ts(now_dt),
                },
                separators=(",", ":"),
                ensure_ascii=False,
            )
            client.publish(status_topic, payload=online_payload, qos=1, retain=True)
            clients[dev.device_id] = client

        self._log("info", "connected", count=len(clients))

        published_total = 0
        backfill_done = False

        try:
            for reading in fleet.generate(duration_min=None):
                if stop_event.is_set():
                    break

                client = clients.get(reading.device_id)
                if client is None:
                    continue

                topic = f"coldchain/v1/{reading.site_id}/{reading.device_id}/telemetry"
                payload = json.dumps(
                    reading.to_payload(),
                    separators=(",", ":"),
                    ensure_ascii=False,
                )

                if reading.ts < anchor_ts:
                    # Publicación acelerada de backfill
                    client.publish(topic, payload=payload, qos=1, retain=False)
                    published_total += 1
                    if published_total % 100 == 0:
                        self._log("info", "published_total", count=published_total)
                    self._sleep_fn(0.002)
                else:
                    # Transición a publicación en directo
                    if not backfill_done:
                        backfill_done = True
                        self._log("info", "backfill_done", published=published_total)

                    current_now = self._now_fn()
                    wait_s = (reading.ts - current_now).total_seconds()
                    if wait_s > 0:
                        if self._sleep_fn is not time.sleep:
                            self._sleep_fn(wait_s)
                            if stop_event.is_set():
                                break
                        else:
                            if stop_event.wait(timeout=wait_s):
                                break

                    if stop_event.is_set():
                        break

                    client.publish(topic, payload=payload, qos=1, retain=False)
                    published_total += 1
                    if published_total % 100 == 0:
                        self._log("info", "published_total", count=published_total)
        finally:
            if not backfill_done:
                self._log("info", "backfill_done", published=published_total)

            # Cierre limpio: publicar estado offline retenido y desconectar
            off_ts = format_ts(self._now_fn())
            for dev in devices:
                client = clients.get(dev.device_id)
                if client is not None:
                    status_topic = f"coldchain/v1/{dev.site_id}/{dev.device_id}/status"
                    offline_payload = json.dumps(
                        {
                            "schema": "status.v1",
                            "device_id": dev.device_id,
                            "state": "offline",
                            "ts": off_ts,
                        },
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                    client.publish(status_topic, payload=offline_payload, qos=1, retain=True)
                    client.loop_stop()
                    client.disconnect()

            self._log("info", "stopped", published=published_total)
