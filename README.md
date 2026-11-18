# ColdChain Monitor

Plataforma IoT de monitoreo con alertas y analítica, portafolio.

## Estado

Fase 1 completada: diseño y contratos. Código en desarrollo.

## Problema

Vacunas, biológicos y reactivos deben mantenerse dentro de un rango de temperatura (por ejemplo, 2–8 °C en refrigeración). Una excursión no detectada a tiempo implica pérdida de producto e incumplimiento de las buenas prácticas de distribución (GDP).

1. **Falsas alarmas.** Una puerta abierta 90 segundos dispara el mismo aviso que un compresor averiado. El equipo aprende a ignorar las alertas.
2. **Fallos silenciosos.** Un nodo que deja de transmitir no genera ninguna alarma de temperatura: "sin alertas" parece "todo bien".

## Arquitectura

Consulta la documentación técnica detallada en [docs/architecture.md](docs/architecture.md).

## Cómo ejecutar

Próximamente (Fase 4).

## Estructura del repositorio

```text
.
├── config/
├── docs/
│   ├── adr/
│   │   └── 0001-arquitectura-base.md
│   ├── alert-rules.md
│   ├── architecture.md
│   └── mqtt-contract.md
├── examples/
│   └── telemetry/
│       ├── invalid_bad_device_id.json
│       ├── invalid_extra_field.json
│       ├── invalid_missing_ts.json
│       ├── invalid_temp_out_of_physical_range.json
│       ├── valid_frozen.json
│       ├── valid_refrigerated.json
│       └── valid_ultra_frozen.json
├── infra/
│   ├── grafana/
│   ├── mosquitto/
│   └── timescaledb/
├── schemas/
│   └── telemetry.v1.schema.json
├── services/
│   ├── alert-engine/
│   ├── analytics/
│   ├── api/
│   ├── ingestor/
│   └── simulator/
├── .env.example
├── .gitignore
├── LICENSE
└── README.md
```

## Documentación

- [Arquitectura](docs/architecture.md)
- [Contrato MQTT](docs/mqtt-contract.md)
- [Reglas de alertas](docs/alert-rules.md)
- [ADR 0001 — Arquitectura base](docs/adr/0001-arquitectura-base.md)

## Aviso

Proyecto independiente. Datos 100% sintéticos. No contiene código, datos ni credenciales de empleadores o clientes.

## Licencia

Ver archivo LICENSE.
