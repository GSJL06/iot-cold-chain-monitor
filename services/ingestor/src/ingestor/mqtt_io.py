"""Adaptador paho-mqtt 2.x con sesión persistente, suscripciones y ACK manual."""

import queue
from typing import Any

import paho.mqtt.client as mqtt

from ingestor.config import Settings
from ingestor.health import HealthState
from ingestor.logs import JsonLogger
from ingestor.pipeline import Message


class MqttSource:
    """Fuente de mensajes MQTT que encola entregas QoS 1 y confirma tras el COMMIT."""

    def __init__(
        self,
        settings: Settings,
        source_queue: queue.Queue[Message],
        health_state: HealthState,
        logger: JsonLogger,
    ) -> None:
        """Configura el cliente paho VERSION2 con clean_session=False y manual_ack=True."""
        self._settings = settings
        self._queue = source_queue
        self._health_state = health_state
        self._logger = logger

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.ingestor_id,
            protocol=mqtt.MQTTv311,
            clean_session=False,
            manual_ack=True,
        )
        self._client.username_pw_set(username="ingestor", password=settings.mqtt_password)
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        connect_flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: Any,
    ) -> None:
        """Actualiza salud y suscribe solo cuando el broker no conserva sesión previa."""
        if reason_code.is_failure:
            self._health_state.mqtt_connected = False
            self._logger.error("mqtt_disconnected", reason=str(reason_code))
            return

        session_present = bool(connect_flags.session_present)
        self._health_state.mqtt_connected = True
        self._logger.info(
            "mqtt_connected",
            session_present=session_present,
            reason=str(reason_code),
        )

        if not session_present:
            telemetry_topic = (
                f"$share/{self._settings.shared_group}/coldchain/v1/+/+/telemetry"
                if self._settings.shared_group
                else "coldchain/v1/+/+/telemetry"
            )
            status_topic = "coldchain/v1/+/+/status"
            client.subscribe([(telemetry_topic, 1), (status_topic, 1)])
            self._logger.info(
                "subscribed",
                session_present=session_present,
                reason=str(reason_code),
            )

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: mqtt.ReasonCode,
        properties: Any,
    ) -> None:
        """Marca MQTT desconectado al cerrarse o caer la conexión."""
        self._health_state.mqtt_connected = False
        self._logger.warning("mqtt_disconnected", reason=str(reason_code))

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        """Encola cada mensaje recibido de forma bloqueante sin tocar la base de datos."""
        self._queue.put(
            Message(
                topic=msg.topic,
                payload=bytes(msg.payload),
                mid=msg.mid,
                qos=msg.qos,
            )
        )

    def start(self) -> None:
        """Inicia la conexión asíncrona y el hilo de red de paho."""
        self._client.connect_async(
            self._settings.mqtt_host,
            self._settings.mqtt_port,
            keepalive=60,
        )
        self._client.loop_start()

    def ack(self, mid: int, qos: int) -> None:
        """Envía PUBACK al broker tras confirmar la transacción en PostgreSQL."""
        self._client.ack(mid, qos)

    def stop(self) -> None:
        """Desconecta limpiamente del broker y detiene el hilo de red."""
        self._client.disconnect()
        self._client.loop_stop()
        self._health_state.mqtt_connected = False
