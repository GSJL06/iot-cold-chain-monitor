# Plan del MVP por fases — ColdChain Monitor

| | |
|---|---|
| Estado | Fase 4 — plan aprobado (v0.1) |
| Fecha | 2026-11-18 |
| Relacionados | `docs/architecture.md`, `docs/engineering-practices.md`, `docs/adr/0004-plan-mvp-por-fases.md` |

## 1. Resumen

El MVP se entrega en **6 sub-fases (4A–4F)**, unas **44 h** en total: 5 semanas a 8–10 h/semana [SUPUESTO]. Cada sub-fase termina con un criterio medible, un entregable visible y un PR integrado en `main` con CI en verde.

| Fase | Nombre | Horas | Semana | Release |
|---|---|---|---|---|
| 4A | Infraestructura base: broker, base de datos, roles | 4 | 1 | — |
| 4B | Simulador de dispositivos | 8 | 1–2 | — |
| 4C | Ingestor | 8 | 2–3 | — |
| 4D | Motor de alertas | 12 | 3–4 | — |
| 4E | API y dashboards Grafana | 8 | 4–5 | — |
| 4F | Cierre, métricas y release | 4 | 5 | `v0.1.0` |
| 4G (opcional) | Analítica: tiempo en rango y MKT | 6 | 6 | `v0.2.0` |

Regla de avance: no se empieza una sub-fase hasta cumplir el criterio de la anterior.

## 2. Criterios de "terminado" por fase

### 4A — Infraestructura base (4 h)

- **Alcance:** `docker-compose.yml` con Mosquitto y TimescaleDB; esquema SQL, roles de mínimo privilegio y flota de 12 dispositivos sintéticos; autenticación y ACL del broker; script de verificación del broker.
- **Terminado cuando:**
  - `docker compose up -d` deja ambos servicios en `healthy` en ≤ 90 s con las imágenes ya descargadas.
  - `\dt` en la base muestra 8 tablas y `SELECT count(*) FROM devices` devuelve 12.
  - `scripts/verify_broker.py` termina con 6 de 6 comprobaciones en PASS.
  - La verificación de suscripción compartida (`$share`) queda registrada en el ADR 0004.
- **Entregable visible:** salida del verificador guardada en `docs/evidence/4a-verify-broker.txt`.
- **Cubre:** seguridad de `threat-model.md` (amenazas 1, 7, 10).

### 4B — Simulador (8 h)

- **Alcance:** servicio `simulator` con modelo térmico (ciclos de compresor y ruido), 12 dispositivos, escenarios de `docs/alert-rules.md` (sección 4), semilla fija, reloj inyectable y modo rápido (tiempo simulado).
- **Terminado cuando:**
  - Con la misma semilla, dos ejecuciones generan exactamente los mismos 12 × 1440 puntos (comparación por hash en un test).
  - Cada escenario produce su firma esperada (por ejemplo `door_open` sube 3–4 °C y vuelve a rango en menos de 5 min).
  - 100 % de los payloads generados validan contra `telemetry.v1.schema.json`.
  - 5 min en tiempo real contra el broker: 12 dispositivos × 5 mensajes = 60 mensajes recibidos.
- **Entregable visible:** `docker compose up simulator` con mensajes en el broker y `services/simulator/README.md`.

### 4C — Ingestor (8 h)

- **Alcance:** suscripción compartida, validación con los 5 motivos de rechazo, deduplicación `(device_id, ts)`, inserción por lotes, manejo de `status.v1`, logs JSON.
- **Terminado cuando:**
  - Cada uno de los 5 motivos de rechazo tiene un test y llega a `rejected_messages`.
  - Un mensaje duplicado no genera fila nueva.
  - Reiniciar el ingestor durante la simulación no pierde mensajes (sesión persistente con QoS 1).
  - Latencia publicación → fila p95 < 2 s a 20 msg/s (medida con `scripts/load_test.py`).
- **Entregable visible:** consulta de salud del pipeline con latencia p95 y huecos de `seq`.

### 4D — Motor de alertas (12 h)

- **Alcance:** reglas R01–R06 en SQL, histéresis, ciclo de vida (una alerta abierta por dispositivo y regla), notificador con interfaz (`LogNotifier` y `TelegramNotifier`), `config/storage_classes.yaml`, reloj inyectable.
- **Terminado cuando:**
  - Cobertura ≥ 70 % en `alert-engine`.
  - Cada regla tiene test positivo, negativo y de histéresis.
  - Escenario de 24 h simuladas con semilla fija: recall 100 % y precisión ≥ 90 % (definiciones en `alert-rules.md`, sección 5).
  - Alerta abierta ≤ 30 s después de cumplirse la condición (test con reloj simulado).
  - Reiniciar el servicio no pierde ni duplica alertas abiertas.
- **Entregable visible:** captura de una notificación real de Telegram y primera versión de `docs/metrics.md`.

### 4E — API y dashboards (8 h)

- **Alcance:** FastAPI (`/healthz`, `/devices`, `/devices/{id}/readings`, `/alerts`, `/alerts/{id}/ack`, `/kpis`), `X-API-Key` para el ACK, paginación; Grafana con datasource y 4 dashboards provisionados como código (Flota, Dispositivo, Alertas, Salud del pipeline).
- **Terminado cuando:**
  - OpenAPI disponible en `/docs`.
  - Las 4 pruebas de abuso de `engineering-practices.md` (sección 5.2) pasan.
  - Tras `docker compose up`, los 4 dashboards aparecen sin clics manuales.
  - `GET /alerts?limit=5000` devuelve como máximo 1000 filas.
- **Entregable visible:** capturas de los dashboards con datos de la simulación.

### 4F — Cierre y release (4 h)

- **Alcance:** compose completo con todos los servicios, Dockerfiles con usuario no root, job de CI que construye las imágenes, README con quickstart, medición de los objetivos 1–5 de `architecture.md`, GIF de demostración, etiqueta y release.
- **Terminado cuando:**
  - Desde un clon limpio, el stack arranca y muestra datos en ≤ 3 min (cronometrado).
  - `docs/metrics.md` contiene los valores medidos de los objetivos 1–5, cada uno con su comando de reproducción.
  - CI en verde y `v0.1.0` publicada con notas de versión.
- **Entregable visible:** GitHub Release `v0.1.0`, GIF en el README.

### 4G — Analítica (opcional, 6 h)

Job diario que calcula tiempo en rango, MKT y puntuación de anomalías, y un dashboard de cumplimiento. Entrega `v0.2.0`. Se hace solo si 4A–4F cierran dentro de plazo.

## 3. Regla de recorte de alcance

Si una fase se pasa más de un 30 % de su estimación, se recorta en este orden:

1. Se omite 4G (analítica).
2. El `TelegramNotifier` queda como interfaz con `LogNotifier` activo; la captura real de Telegram se documenta como pendiente.
3. Los dashboards pasan de 4 a 2 (Flota y Alertas), con Salud del pipeline como consulta SQL documentada.
4. Nunca se recortan: contrato de datos, deduplicación, reglas R01 y R03, tests del motor de alertas y CI.

## 4. Medición de los objetivos de `architecture.md`

| # | Objetivo | Comando o prueba | Dónde queda la evidencia |
|---|---|---|---|
| 1 | Stack arriba en ≤ 3 min | Cronómetro desde `git clone` hasta dashboard con datos | `docs/metrics.md` (4F) |
| 2 | p95 publicación → fila < 2 s a 20 msg/s | `scripts/load_test.py` y consulta de latencia | `docs/metrics.md` (4C, 4F) |
| 3 | 0 huecos de `seq` en 1 h | Consulta de huecos (`engineering-practices.md`, sección 6) | `docs/metrics.md` (4F) |
| 4 | Alerta ≤ 30 s tras la condición | Test con reloj simulado | Salida de pytest (4D) |
| 5 | Recall 100 %, precisión ≥ 90 % | Test de integración del escenario de 24 h | Salida de pytest (4D) |
| 6 | Cobertura ≥ 70 % y CI verde | `pytest --cov` en CI | Badge y logs de Actions |

## 5. Decisiones técnicas fijadas para la Fase 4

- **Roles de base de datos:** `ingestor`, `alert_engine`, `api_svc`, `analytics` y `grafana_ro`. `architecture.md` (sección 10) preveía `api_ro`; el ACK exige escribir, por lo que `api_svc` solo recibe `UPDATE (state, acked_at)` sobre `alerts`.
- **Compresión y retención** de `readings`: API de compresión de TimescaleDB, aislada en un archivo de inicialización propio. La documentación oficial la mantiene soportada tras la llegada de la API de columnstore.
- **Finales de línea:** `.gitattributes` fuerza LF en `infra/`, `*.sh` y `*.sql`. Los archivos con CRLF montados en contenedores Linux fallan al leerse.
- **Suscripción compartida:** se verifica en 4A. Plan B: un solo ingestor sin `$share` y actualización de `mqtt-contract.md`.
