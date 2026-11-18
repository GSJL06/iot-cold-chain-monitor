# Prácticas de ingeniería — ColdChain Monitor

| | |
|---|---|
| Estado | Fase 3 — prácticas definidas (v0.1) |
| Fecha | 2026-11-18 |
| Relacionados | `docs/adr/0003-practicas-ingenieria.md`, `docs/threat-model.md` |

## 1. Flujo de trabajo

| Tema | Regla |
|---|---|
| Ramas | `main` siempre en verde. Todo cambio entra por pull request (PR) desde una rama `feat/…`, `fix/…`, `docs/…`, `chore/…`, `test/…` o `ci/…` |
| PR | Pequeños (una idea por PR). Squash merge: un commit limpio en `main`. El título del PR sigue Conventional Commits |
| Commits | Conventional Commits: `tipo: descripción`. Tipos válidos: `feat`, `fix`, `docs`, `chore`, `test`, `build`, `ci`, `refactor`, `perf`, `style`, `revert` |
| Protección de `main` | PR obligatorio, checks `lint` y `test` en verde, sin force-push, sin borrado. Sin revisores obligatorios (proyecto individual) |
| Versionado | SemVer. `v0.1.0` = pipeline y alertas funcionando (fin de Fase 4); `v0.2.0` = analítica y dashboards completos; `v1.0.0` = documentación final y demo (Fase 5) |
| Contrato de datos | `telemetry.v1` se versiona aparte del software. Un campo nuevo implica `telemetry.v2` |
| Changelog | `CHANGELOG.md` en formato Keep a Changelog. Se actualiza en cada PR que cambie comportamiento |
| Releases | Manuales: `gh release create vX.Y.Z --generate-notes` |

## 2. CI/CD

Plataforma: GitHub Actions (gratis en repositorios públicos). Workflow `.github/workflows/ci.yml`.

| Job | Qué hace | Cuándo | ¿Bloquea el merge? |
|---|---|---|---|
| `lint` | `ruff check` y `ruff format --check` | push a `main` y PR | Sí |
| `test` | `pytest -m "not integration"` | push a `main` y PR | Sí |
| `audit` | `pip-audit` sobre cada `services/*/requirements.txt` | push, PR y lunes 06:00 UTC | No (informativo) |

Reglas del workflow:

- Permisos mínimos: `permissions: contents: read`.
- `concurrency` cancela ejecuciones obsoletas del mismo PR.
- Cada job con `timeout-minutes`.
- Acciones de terceros fijadas a versión mayor; Dependabot las actualiza.

**CD en v1:** no hay despliegue. En la Fase 4 el CI añade un job que construye las imágenes Docker para verificar que compilan, sin publicarlas. El entregable de cada hito es un GitHub Release con notas.

**Dependabot** (`.github/dependabot.yml`): revisión semanal agrupada para `pip` (raíz y cada servicio) y `github-actions`. Es el mecanismo para actualizar las versiones fijadas de `docs/versions.md`.

## 3. Estrategia de pruebas

| Nivel | Qué prueba | Herramienta | Dónde corre | Objetivo |
|---|---|---|---|---|
| Contrato | Payloads contra `telemetry.v1.schema.json` | pytest + jsonschema | CI en cada PR | 8 casos en verde (ya existe) |
| Unitario | Funciones puras: modelo térmico del simulador, parámetros por clase, cálculo de MKT, formato de notificaciones | pytest | CI en cada PR | Cobertura ≥ 70 % en `alert-engine` |
| Integración | Reglas R01–R06 en SQL contra TimescaleDB con datos sembrados | pytest `-m integration` + contenedor de servicio en Actions | CI en cada PR (Fase 4) | Cada regla con caso positivo, negativo y de histéresis |
| Escenario | 24 h simuladas con semilla fija y fallos etiquetados (`alert-rules.md`, sección 5) | pytest `-m integration` | CI en `main` y manual | Recall 100 %, precisión ≥ 90 % |
| Abuso | Pruebas negativas de seguridad (sección 5.2) | pytest `-m integration` | CI en `main` | Todas rechazadas |

Reglas para que las pruebas sean fiables:

1. **Deterministas:** semilla fija en el simulador; sin `sleep` reales.
2. **Reloj inyectable:** el motor de alertas recibe `now` como parámetro. Así una prueba simula 24 h en segundos y mide la latencia de alerta (objetivo 4 de `architecture.md`).
3. **Datos sintéticos** generados por el propio simulador o por fixtures SQL en `tests/fixtures/`.
4. **Marcador `integration`** para todo lo que necesita base de datos; en local se omite sin ella.

## 4. Calidad de código

- **Ruff** (`ruff.toml`): reglas `E`, `F`, `I`, `B`, `UP` y `S` (seguridad, incluye detección de SQL construido con cadenas). Línea máxima de 100.
- **pre-commit** (`.pre-commit-config.yaml`): ruff, ruff-format, `check-yaml`, `check-json`, `check-toml`, `detect-private-key`, `check-added-large-files` y validación del mensaje de commit.
- Anotaciones de tipos en funciones públicas. `mypy` queda fuera de v1.
- Código, identificadores y mensajes de log en inglés. Documentación en español.

## 5. Seguridad básica

| Control | Herramienta o práctica | Fase |
|---|---|---|
| Secretos fuera del repo | `.env` ignorado, `.env.example` con placeholders | Hecha |
| Detección de secretos | Secret scanning y push protection de GitHub; hook `detect-private-key` | Fase 3 |
| Dependencias vulnerables | Dependabot alerts, `pip-audit` semanal | Fase 3 |
| Reporte de vulnerabilidades | `SECURITY.md` y reporte privado de GitHub | Fase 3 |
| Análisis estático de seguridad | Reglas `S` de ruff | Fase 3 |
| Mínimo privilegio en datos | Roles de BD `ingestor`, `alert_engine`, `api_ro`, `grafana_ro` | Fase 4 |
| Autenticación del broker | `password_file`, un usuario por servicio, ACL por dispositivo | Fase 4 |
| Contenedores endurecidos | Ver checklist 5.1 | Fase 4 |

### 5.1 Checklist de seguridad para la Fase 4

- [ ] Cada `Dockerfile` crea y usa un usuario no root.
- [ ] Imágenes base con el tag exacto de `docs/versions.md`.
- [ ] `.dockerignore` excluye `.env`, `.git`, `.venv` y tests.
- [ ] En `docker-compose.yml`: puertos publicados solo en `127.0.0.1`, `cap_drop: [ALL]` en servicios Python, sin `privileged`.
- [ ] `mosquitto.conf`: `allow_anonymous false`, `password_file`, ACL, `message_size_limit 4096`, `max_connections` acotado.
- [ ] Consultas SQL siempre parametrizadas (nunca f-strings con datos).
- [ ] Rol `api_ro` con `statement_timeout = 5s`; endpoints con paginación y `LIMIT` máximo de 1000.
- [ ] Grafana con contraseña de administrador desde `.env` y acceso anónimo desactivado.
- [ ] Ningún log imprime credenciales ni tokens.

### 5.2 Pruebas de abuso (Fase 4)

1. Publicar sin credenciales: el broker lo rechaza.
2. Publicar con un dispositivo en el topic de otro: la ACL lo deniega.
3. Publicar un payload de más de 4 KB: el broker lo descarta.
4. `POST /alerts/{id}/ack` sin `X-API-Key`: responde 401.

## 6. Observabilidad (sin Prometheus en v1)

El pipeline se mide a sí mismo con SQL y se muestra en un dashboard de Grafana "Pipeline health".

**Logs estructurados** (JSON, una línea por evento, a stdout):

```json
{"ts":"2026-11-18T12:00:01Z","level":"INFO","service":"ingestor","event":"reading_stored","device_id":"SIM-HA-001","latency_ms":42}
```

Campos fijos: `ts`, `level`, `service`, `event`. Opcionales: `device_id`, `rule_id`, `latency_ms`, `error`. Nunca se registran credenciales.

**Healthchecks:** `GET /healthz` en la API; `healthcheck` de Docker en broker, base de datos y workers.

**Métricas de salud del pipeline (SQL):**

```sql
-- Latencia publicación → fila (p95, últimos 5 min)
SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM received_at - ts)) AS p95_s
FROM readings
WHERE received_at > now() - interval '5 minutes';

-- Mensajes perdidos por dispositivo (huecos de seq, última hora)
SELECT device_id, sum(gap) AS missing
FROM (
  SELECT device_id, seq - lag(seq) OVER (PARTITION BY device_id ORDER BY ts) - 1 AS gap
  FROM readings
  WHERE ts > now() - interval '1 hour'
) g
WHERE gap > 0
GROUP BY device_id;

-- Rechazos por motivo (última hora)
SELECT reason, count(*) AS n
FROM rejected_messages
WHERE received_at > now() - interval '1 hour'
GROUP BY reason;
```

Un `seq` que vuelve a 0 por reinicio del dispositivo da un hueco negativo y se descarta con `gap > 0`.

**Paneles previstos:** latencia p95, huecos de `seq`, rechazos por motivo, retraso de ingestión (`now() - max(received_at)`), alertas abiertas por severidad y dispositivos silenciosos.

## 7. Documentación

- Documentación como código: todo en `docs/`, versionada con el software.
- Una decisión que cambia la arquitectura o el stack se registra como ADR numerado.
- Diagramas en Mermaid dentro del Markdown (GitHub los dibuja).
- Cada servicio tendrá un `README.md` corto: qué hace, variables de entorno, cómo probarlo.
- El README raíz se rehace en la Fase 5 con métricas medidas, GIF de demo y enlace a cada documento.

## 8. Definition of Done de un PR

- [ ] CI en verde (`lint` y `test`).
- [ ] Pruebas nuevas o actualizadas para el cambio.
- [ ] Documentación y `CHANGELOG.md` actualizados si cambia el comportamiento.
- [ ] Sin secretos, IPs reales ni datos de clientes.
- [ ] Título del PR en formato Conventional Commits.
