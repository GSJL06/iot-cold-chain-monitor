# Versiones fijadas

Fecha de verificación: 2026-11-18

| Componente | Imagen:tag | Comando de verificación | Resultado |
|---|---|---|---|
| TimescaleDB | `timescale/timescaledb:2.32.0-pg17` | `docker manifest inspect timescale/timescaledb:2.32.0-pg17` | OK |
| Eclipse Mosquitto | `eclipse-mosquitto:2.0.23` | `docker manifest inspect eclipse-mosquitto:2.0.23` | OK |
| Grafana | `grafana/grafana:13.3.1` | `docker manifest inspect grafana/grafana:13.3.1` | OK |
| Python (base) | `python:3.12.16-slim` | `docker manifest inspect python:3.12.16-slim` | OK |

## Dependencias Python

- [services/simulator/requirements.txt](../services/simulator/requirements.txt)
- [services/ingestor/requirements.txt](../services/ingestor/requirements.txt)
- [services/alert-engine/requirements.txt](../services/alert-engine/requirements.txt)
- [services/api/requirements.txt](../services/api/requirements.txt)
- [services/analytics/requirements.txt](../services/analytics/requirements.txt)

Los tags se fijan por reproducibilidad. Actualizarlos es una decisión explícita (ADR si cambia una versión mayor).
