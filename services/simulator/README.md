# Simulador IoT de Cadena de Frío

Servicio de generación y publicación de telemetría sintética físicamente realista para la plataforma de monitoreo de cadena de frío. Simula una flota heterogénea de 12 dispositivos distribuidos en 3 sitios y 3 clases térmicas de almacenamiento (refrigerado, congelado y ultracongelado).

Para los fundamentos matemáticos, constantes físicas, catálogo de fallos y detalles de diseño, consulta el documento principal:
- [docs/simulator-design.md](../../docs/simulator-design.md)

---

## Modos de Uso

El simulador se ejecuta a través del módulo de Python `simulator`:

```bash
python -m simulator --help
```

### 1. Modo Generación Offline (`generate`)

Genera telemetría determinista a un archivo JSON Lines (NDJSON) y la verdad de referencia a un archivo JSON sin abrir conexiones de red:

```bash
python -m simulator generate \
  --scenario config/scenarios/demo.yaml \
  --start 2026-01-01T00:00:00Z \
  --out out/demo_telemetry.ndjson \
  --truth out/demo_truth.json
```

Opciones principales:
- `--scenario`: Ruta al archivo YAML del escenario (obligatorio).
- `--start`: Marca temporal ISO 8601 UTC de inicio (por defecto: `2026-01-01T00:00:00Z`).
- `--seed`: Semilla PRNG opcional (sustituye la semilla definida en el plan).
- `--out`: Ruta de salida para las lecturas de telemetría (JSON Lines).
- `--truth`: Ruta de salida para los eventos de verdad de referencia (JSON).
- `--config-dir`: Directorio con los modelos térmicos, flota y catálogo (por defecto: variable `SIM_CONFIG_DIR` o `config`).

### 2. Modo Publicación MQTT (`publish`)

Publica la telemetría en un broker MQTT en tiempo real, con soporte para precarga de histórico (*backfill*):

```bash
python -m simulator publish \
  --scenario config/scenarios/demo.yaml \
  --backfill-hours 1 \
  --period-s 60
```

Comportamiento:
- **Multicliente:** Crea una conexión MQTT 3.1.1 independiente por dispositivo (`client_id` y usuario igual a `device_id`).
- **Estado y LWT:** Cada cliente registra un *Last Will and Testament* (LWT) con payload `{"schema": "status.v1", "state": "offline", ...}` retenido (QoS 1). Al conectarse publica estado `online` retenido y al recibir SIGINT/SIGTERM publica estado `offline` y desconecta limpiamente.
- **Backfill acelerado:** Si `backfill-hours > 0`, las lecturas previas a la hora actual se transmiten de forma acelerada con una pausa de 2 ms entre mensajes.
- **Transmisión en directo:** Las lecturas a partir de la hora actual se sincronizan contra el reloj del sistema.
- **Logging estructurado:** Emite eventos en formato JSON de una sola línea (`connected`, `backfill_done`, `published_total`, `stopped`).

Opciones principales:
- `--scenario`: Ruta al archivo YAML del escenario (por defecto: `$SIM_CONFIG_DIR/scenarios/demo.yaml`).
- `--backfill-hours`: Horas de datos históricos a generar y publicar antes de continuar en directo (entero $\ge 0$, por defecto: `0`).
- `--period-s`: Periodo de muestreo en segundos (sustituye el valor de `fleet.yaml`).
- `--seed`: Semilla PRNG opcional.

---

## Variables de Entorno

| Variable | Descripción | Valor por Defecto |
| :--- | :--- | :--- |
| `MQTT_HOST` | Host o nombre de servicio del broker MQTT. | `localhost` |
| `MQTT_PORT` | Puerto TCP del broker MQTT. | `1883` |
| `MQTT_DEVICE_PASSWORD` | Contraseña compartida de los dispositivos (obligatoria). | *Ninguno (falla si falta)* |
| `SIM_CONFIG_DIR` | Ruta al directorio con archivos de configuración YAML. | `config` (`/app/config` en Docker) |
| `SIM_SCENARIO` | Nombre del escenario para Docker Compose. | `demo` |
| `SIM_BACKFILL_HOURS` | Horas de backfill en Docker Compose. | `0` |

---

## Ejecución con Docker Compose

El servicio `simulator` está definido en `docker-compose.yml` bajo el perfil opcional `sim`. De esta forma no se inicia automáticamente con los servicios centrales de infraestructura (`mosquitto`, `timescaledb`, `grafana`):

```bash
# Iniciar infraestructura y simulador simultáneamente
docker compose --profile sim up simulator

# O iniciar el simulador en segundo plano una vez la infraestructura esté lista
docker compose --profile sim up -d simulator

# Detener el simulador
docker compose --profile sim stop simulator
```

---

## Verificación de Telemetría

Para monitorear el flujo de mensajes MQTT en vivo y validar que cumplan estrictamente el esquema `schemas/telemetry.v1.schema.json`:

```bash
python scripts/watch_telemetry.py --seconds 60
```
