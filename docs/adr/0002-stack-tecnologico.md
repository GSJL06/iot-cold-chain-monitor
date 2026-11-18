# ADR 0002 — Stack tecnológico

| | |
|---|---|
| Estado | Aceptada |
| Fecha | 2026-11-18 |
| Relacionada | ADR 0001 (arquitectura base) |

## Contexto

El proyecto debe ser reproducible con coste cero, completarse en 4–6 semanas a 8–10 h/semana y demostrar SQL, Python, mensajería y datos. La arquitectura (ADR 0001) ya fija MQTT, base de series temporales y alertas sobre la base de datos.

## Decisión

1. **Python 3.12** en todos los servicios, un `Dockerfile` por servicio y un único `docker-compose.yml`.
2. **Mosquitto 2.x** (broker) y **paho-mqtt 2.1** (cliente).
3. **PostgreSQL 17 + TimescaleDB 2.x**, con **psycopg 3** y SQL explícito. Esquema en scripts SQL de inicialización.
4. **jsonschema** para validar el contrato en el ingestor y en los tests.
5. **FastAPI + Pydantic v2** para la API; **Grafana** para dashboards; **Telegram Bot API** para notificar.
6. **GitHub Actions** (CI), **pytest** y **ruff** (calidad, Fase 3).
7. Todas las imágenes y dependencias con versión exacta; no se usa `latest`.

## Consecuencias

**A favor**

- Un lenguaje y un conjunto pequeño de herramientas: menos tiempo en integración, más en reglas y datos.
- SQL visible en el código: refuerza el perfil de datos.
- Reproducibilidad: cualquier clon levanta el mismo entorno.

**En contra**

- Sin migraciones versionadas: cambiar el esquema obliga a recrear el volumen en desarrollo.
- Sin Kubernetes ni observabilidad avanzada en v1: se declara como brecha conocida.
- Fijar versiones exige actualizarlas a mano; se hará con una decisión explícita (nuevo ADR si cambia una versión mayor).

## Alternativas descartadas

Detalle capa por capa en `docs/stack.md`, sección 1.
