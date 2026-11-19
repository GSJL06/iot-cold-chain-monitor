# Changelog

Todos los cambios notables en este proyecto serán documentados en este archivo.

El formato está basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/),
y este proyecto se adhiere a [Semantic Versioning](https://semver.org/lang/es/).

## [Unreleased]

- Fase 4A: infraestructura base (Mosquitto con ACL, TimescaleDB con esquema, roles y flota sintética), plan MVP y verificador del broker.
- Fase 4B: simulador determinista (modelo térmico, 8 escenarios, plan de validación de 24 h con ground truth), publicación MQTT con backfill y servicio en Compose.
- Fase 4A: verificada la suscripción compartida `$share` en Mosquitto 2.0.23 (6/6 PASS, `docs/evidence/4a-verify-broker.txt`); ADR 0004 actualizada con el resultado.

### Added

- **Fase 3 — Prácticas de ingeniería, CI y seguridad**:
  - Configuración de Ruff (`ruff.toml`) para linting y formateo con reglas `E`, `F`, `I`, `B`, `UP` y `S`.
  - Configuración de pre-commit (`.pre-commit-config.yaml`) con hooks de calidad, formato y validación de mensajes de commit mediante `pygrep`.
  - Workflow de CI en GitHub Actions (`.github/workflows/ci.yml`) con jobs `lint`, `test` y `audit`.
  - Configuración de Dependabot (`.github/dependabot.yml`) para actualizaciones automáticas agrupadas semanales de dependencias pip y GitHub Actions.
  - Plantilla de Pull Request (`.github/pull_request_template.md`) con checklist de Definition of Done.
  - Política de seguridad (`SECURITY.md`) con procedimiento de reporte privado de vulnerabilidades.
  - Documentación de prácticas de ingeniería (`docs/engineering-practices.md`) y ADR 0003 (`docs/adr/0003-practicas-ingenieria.md`).
- **Fase 2 — Stack y versiones fijadas**:
  - Definición del stack tecnológico (ADR 0002, `docs/stack.md`) con TimescaleDB, Mosquitto, Grafana y servicios en Python 3.12.
  - Fijación estricta de versiones de infraestructura y servicios (`docs/versions.md`) para reproducibilidad.
  - Modelo de amenazas STRIDE (`docs/threat-model.md`) y checklist de seguridad.
  - Estructura base y dependencias iniciales de servicios (`simulator`, `ingestor`, `alert-engine`, `api`, `analytics`).
- **Fase 1 — Documentación de arquitectura y contrato MQTT**:
  - Definición de arquitectura base del sistema IoT (ADR 0001, `docs/architecture.md`).
  - Especificación de contrato de telemetría MQTT (`docs/mqtt-contract.md`).
  - Esquema JSON Schema para telemetría v1 (`schemas/telemetry.v1.schema.json`) y catálogo de ejemplos válidos e inválidos (`examples/telemetry/`).
  - Especificación de reglas de alerta R01–R06 (`docs/alert-rules.md`).
  - Pruebas iniciales de validación de contrato con pytest y jsonschema (`tests/contract/test_telemetry_schema.py`).
