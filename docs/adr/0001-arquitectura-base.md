# ADR 0001 — Arquitectura base: MQTT + base de series temporales + alertas sobre la base de datos

| | |
|---|---|
| Estado | Aceptada |
| Fecha | 2026-11-18 |

## Contexto

Plataforma de monitoreo de cadena de frío con 12 dispositivos simulados por defecto y un objetivo de carga de 20 msg/s en un portátil. Debe ser reproducible con coste cero y demostrar fiabilidad de alertas, no escala masiva.

## Decisión

1. Los dispositivos publican por **MQTT** (QoS 1) a un broker Mosquitto.
2. Un **ingestor** valida contra un JSON Schema y escribe en **TimescaleDB** (PostgreSQL con extensión de series temporales).
3. El **alert-engine** evalúa reglas con SQL cada 15 s sobre la base de datos. No consume el stream directamente.
4. **Grafana** y la **API** leen de la base de datos con roles de solo lectura.

## Consecuencias

**A favor**

- Reiniciar cualquier servicio no pierde estado: las ventanas de las reglas viven en la base de datos.
- Las reglas por duración se escriben y se prueban en SQL con datos sembrados.
- Un solo almacén para dashboards, API y analítica.
- MQTT refleja la realidad de dispositivos con poca capacidad y conexiones inestables.

**En contra**

- Latencia de alerta de hasta 15 s adicionales. Aceptable para reglas que miden minutos.
- La base de datos es punto único de fallo en v1. Se documenta; la alta disponibilidad queda fuera de alcance.

## Alternativas descartadas

| Alternativa | Motivo del descarte |
|---|---|
| Kafka + procesador de streams | Complejidad operativa desproporcionada para 20 msg/s y un portátil |
| Ingesta HTTP directa desde los dispositivos | Pierde el desacople del broker y no refleja el patrón real de dispositivos limitados |
| Reglas evaluadas en memoria sobre el stream | Reiniciar el servicio perdería las ventanas; más difícil de probar con datos sembrados |

La justificación capa por capa del stack (con alternativas descartadas) va en la Fase 2.
