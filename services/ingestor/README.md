# Ingestor MQTT → TimescaleDB

Servicio encargado de consumir mensajes MQTT (`telemetry.v1` y `status.v1`), validarlos contra sus contratos JSON Schema, clasificar rechazos con precedencia estricta, deduplicar lecturas por `(device_id, ts)` y persistirlos por lotes en una única transacción antes de enviar el `PUBACK` manual al broker.

Para la arquitectura, flujo, garantías de entrega y decisiones de diseño, consulta:
- [docs/ingestor-design.md](../../docs/ingestor-design.md)
- [docs/adr/0006-ingestor-at-least-once.md](../../docs/adr/0006-ingestor-at-least-once.md)

---

## Variables de entorno

El listado completo de variables, valores por defecto y límites de validación se encuentra en la tabla de configuración de [docs/ingestor-design.md (sección 10)](../../docs/ingestor-design.md#10-configuración).

Las variables obligatorias son:
- `MQTT_INGESTOR_PASSWORD`
- `POSTGRES_DB`
- `INGESTOR_DB_PASSWORD`

---

## Ejecución, logs y salud

El servicio arranca por defecto al levantar el stack con Docker Compose (sin perfil adicional):

```bash
docker compose up -d
```

### Ver logs estructurados

Emite una línea JSON por evento a `stdout` (sin credenciales ni payloads):

```bash
docker compose logs -f ingestor
```

### Comprobar salud del contenedor

El worker actualiza el archivo de latido (`/tmp/ingestor.alive`, configurable con `HEARTBEAT_PATH`) como máximo cada segundo si la conexión MQTT y la base de datos están sanas. El `healthcheck` de Docker ejecuta:

```bash
docker compose ps ingestor
# O directamente dentro del contenedor:
docker compose exec ingestor python -m ingestor.health
```

---

## Pruebas y verificación

### Pruebas unitarias y de contrato (sin servicios externos)

```bash
pytest tests/unit/ingestor tests/contract -q
```

### Pruebas de integración contra TimescaleDB (con `ROLLBACK`)

```bash
pytest -m integration tests/integration/ingestor -q
```

### Verificación en vivo del pipeline

Con `timescaledb`, `mosquitto` e `ingestor` levantados:

```bash
python scripts/verify_ingestor.py
python scripts/pipeline_health.py --minutes 5
python scripts/load_test.py --rate 20 --seconds 60
```
