# Contrato MQTT v1

Fuente de verdad del payload: `schemas/telemetry.v1.schema.json`. Si este documento y el schema difieren, manda el schema.

## 1. Topics

| Topic | Dirección | QoS | Retain | Payload |
|---|---|---|---|---|
| `coldchain/v1/{site_id}/{device_id}/telemetry` | dispositivo → broker | 1 | no | `telemetry.v1` |
| `coldchain/v1/{site_id}/{device_id}/status` | dispositivo → broker | 1 | sí | `status.v1` |

Identificadores:

| Campo | Patrón | Ejemplo |
|---|---|---|
| `site_id` | `^[a-z0-9][a-z0-9-]{1,23}$` | `hosp-a`, `farm-b`, `lab-c` |
| `device_id` | `^[A-Z0-9][A-Z0-9-]{3,31}$` | `SIM-HA-001` |

## 2. Conexión

- `client_id` = `device_id`. Un cliente por dispositivo.
- Keepalive: 60 s.
- Periodo de telemetría por defecto: 60 s (configurable en el simulador).
- **LWT (Last Will and Testament):** al conectar, el dispositivo declara un mensaje `status.v1` con `state = "offline"` en su topic `status` (retain). Si el broker detecta una desconexión sin aviso, lo publica.
- Al conectar, el dispositivo publica `status.v1` con `state = "online"` (retain).
- Autenticación: usuario y contraseña por cliente. ACL: un dispositivo solo escribe en `coldchain/v1/+/<su device_id>/#`.
- El `ingestor` usa suscripción compartida `$share/ingestor/coldchain/v1/+/+/telemetry` con sesión persistente. Permite añadir réplicas sin duplicar el procesamiento.

## 3. Payload `telemetry.v1`

```json
{
  "schema": "telemetry.v1",
  "device_id": "SIM-HA-001",
  "ts": "2026-11-18T12:00:00Z",
  "seq": 1042,
  "temp_c": 4.7,
  "humidity_pct": 52.1,
  "rssi_dbm": -87,
  "battery_pct": 100,
  "on_backup_power": false,
  "fw": "sim-1.0.0"
}
```

| Campo | Tipo | Obligatorio | Rango | Nota |
|---|---|---|---|---|
| `schema` | string | sí | constante `telemetry.v1` | Versión del contrato |
| `device_id` | string | sí | patrón de la sección 1 | Debe coincidir con el topic |
| `ts` | string | sí | ISO 8601 UTC con `Z` | Reloj del dispositivo |
| `seq` | integer | sí | ≥ 0 | Contador creciente; los huecos miden pérdida de mensajes |
| `temp_c` | number | sí | −100 a 60 | Rango físico del sensor |
| `humidity_pct` | number | no | 0 a 100 | Ausente en sondas que no la miden (ultracongelación) |
| `rssi_dbm` | integer | sí | −140 a 0 | Potencia de señal |
| `battery_pct` | number | sí | 0 a 100 | Batería de respaldo |
| `on_backup_power` | boolean | sí | — | `true` si el nodo funciona con batería por corte de red eléctrica |
| `fw` | string | no | máx. 32 caracteres | Versión de firmware |

El schema es estricto (`additionalProperties: false`). Cualquier campo nuevo implica una versión nueva (`telemetry.v2`) y un topic `v2`.

## 4. Payload `status.v1`

```json
{
  "schema": "status.v1",
  "device_id": "SIM-HA-001",
  "state": "online",
  "ts": "2026-11-18T11:59:30Z"
}
```

`state` es `online` u `offline`. Su schema formal llega con el `ingestor` (Fase 4).

## 5. Validaciones del ingestor

El `ingestor` rechaza el mensaje y lo guarda en `rejected_messages` con el motivo:

| Motivo | Condición |
|---|---|
| `schema_invalid` | No cumple `telemetry.v1.schema.json` |
| `unsupported_schema` | El campo `schema` no es `telemetry.v1` |
| `topic_mismatch` | `device_id` del payload distinto del topic |
| `unknown_device` | `device_id` no existe en `devices` |
| `clock_skew` | `ts` más de 5 min en el futuro respecto a la hora del servidor |

Duplicados: la clave `(device_id, ts)` absorbe reenvíos de QoS 1 con `ON CONFLICT DO NOTHING`.

## 6. Prueba manual (Fase 4)

```bash
mosquitto_pub -h localhost -p 1883 -u "$MQTT_USER" -P "$MQTT_PASSWORD" \
  -t "coldchain/v1/hosp-a/SIM-HA-001/telemetry" -q 1 \
  -f examples/telemetry/valid_refrigerated.json
```
