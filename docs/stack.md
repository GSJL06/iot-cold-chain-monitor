# Stack tecnológico — ColdChain Monitor

| | |
|---|---|
| Estado | Fase 2 — decisiones de stack (v0.1) |
| Fecha | 2026-11-18 |
| Versiones exactas | `docs/versions.md` (imágenes) y `services/*/requirements.txt` (Python) |

Una recomendación por capa. Coste total: **0 €**.

## 1. Decisión por capa

| # | Capa | Elección | Por qué | Alternativa descartada |
|---|---|---|---|---|
| 1 | Lenguaje de servicios | **Python 3.12** | Ya lo usas (scripts, SENA, Nexara) y es el lenguaje de datos; un solo lenguaje reduce el coste de cambio de contexto | Java/Kotlin: más ceremonia para un pipeline de datos y no refuerzan el ángulo datos/IA |
| 2 | Broker MQTT | **Eclipse Mosquitto 2.x** | Ligero, soporta MQTT 5 y 3.1.1, suscripciones compartidas, ACL y autenticación por archivo | EMQX: pensado para clústeres; excesivo para 20 msg/s |
| 3 | Cliente MQTT | **paho-mqtt 2.1** | Estándar de facto, lo usan simulador e ingestor | aiomqtt: capa asíncrona sobre paho sin beneficio a esta carga |
| 4 | Validación del contrato | **jsonschema 4.x** | Mismo schema y misma librería que los tests de contrato: una sola fuente de verdad | Pydantic en el ingestor: duplicaría el contrato en código |
| 5 | Base de datos | **PostgreSQL 17 + TimescaleDB 2.x** | SQL estándar con hypertables, compresión y agregados continuos; aprovecha tu SQL | InfluxDB: otro lenguaje de consulta, menos transferible |
| 6 | Driver de BD | **psycopg 3 (con pool)** | SQL explícito; el proyecto quiere mostrar SQL, no ocultarlo | SQLAlchemy ORM: oculta las consultas que son parte de la demostración |
| 7 | Esquema | **Scripts SQL en `infra/timescaledb/init/`** | Cero herramientas extra; se ejecutan al crear el volumen | Alembic: migraciones innecesarias para un esquema único de demo |
| 8 | API | **FastAPI + Pydantic v2 + Uvicorn** | OpenAPI automática y validación de entrada | Flask: sin validación ni documentación automáticas |
| 9 | Motor de alertas | **Python + SQL, bucle de 15 s** | Reglas por duración en SQL; estado en la BD (ADR 0001) | Celery o APScheduler: infraestructura de colas sin necesidad |
| 10 | Notificación | **Telegram Bot API vía httpx** | Gratis, inmediata, un POST HTTPS | SMS (Twilio): de pago |
| 11 | Analítica (opcional) | **pandas + scikit-learn (IsolationForest)** | MKT, tiempo en rango y anomalías con pocas líneas | Spark, Airflow o MLflow: desproporcionados |
| 12 | Visualización | **Grafana (imagen `grafana/grafana`)** | Estándar para series temporales; datasource PostgreSQL y provisioning como código | Streamlit: menos habitual en IoT y sin paneles temporales nativos |
| 13 | Simulador | **Biblioteca estándar + PyYAML** | Modelo térmico simple con semilla fija; imagen mínima | NumPy o SimPy: dependencias sin ganancia real |
| 14 | Contenedores | **Docker Desktop + Compose v2** | `docker compose up` reproduce todo | Kubernetes: no cabe en 4–6 semanas a 8–10 h/semana |
| 15 | CI | **GitHub Actions** | Gratis en repositorios públicos, integrado en GitHub | Jenkins autohospedado: operar el CI se come el tiempo del proyecto |
| 16 | Calidad (Fase 3) | **pytest + pytest-cov + ruff** | Ruff reemplaza lint, formato e imports en una herramienta | flake8 + black + isort: tres herramientas para lo mismo |

## 2. Estructura de despliegue local

- Un `Dockerfile` por servicio Python, base `python:3.12-slim`.
- Un `docker-compose.yml` en la raíz con: `mosquitto`, `timescaledb`, `ingestor`, `alert-engine`, `simulator`, `api`, `grafana` (y `analytics` si llega).
- Imágenes con tag exacto (`docs/versions.md`). No se usa `latest`.
- Configuración por variables de entorno desde `.env`.

## 3. Trampas conocidas (se aplican en la Fase 4)

1. **paho-mqtt 2.x rompió la API de 1.x.** `mqtt.Client("id")` falla; hay que declarar `CallbackAPIVersion.VERSION2` y `client_id=` por nombre. Los ejemplos antiguos de internet ya no sirven.
2. **TimescaleDB no publica tag `latest`.** Solo `latest-pgXX` y tags por versión. Se fija `<versión>-pg17`.
3. **Grafana:** desde la 12.4 la imagen de referencia es `grafana/grafana`; el repositorio `grafana/grafana-oss` ya no se actualiza.
4. **Mosquitto 2.x no acepta conexiones remotas sin configuración.** Hay que declarar `listener 1883`, `allow_anonymous false` y un `password_file`.
5. **Los scripts de `docker-entrypoint-initdb.d` solo corren con el volumen vacío.** Cambiar el esquema exige borrar el volumen (`docker compose down -v`) en desarrollo.

## 4. Coste

| Pieza | Coste | Nota |
|---|---|---|
| Docker Desktop | 0 € | Gratuito para uso personal. Verificar la licencia vigente si se usa en una empresa |
| GitHub + Actions | 0 € | Repositorio público |
| Mosquitto, TimescaleDB, Grafana, Python | 0 € | Software libre |
| Telegram Bot API | 0 € | Sin límite práctico a esta carga |
| Demo pública | opcional | Fuera de v1; se evidencia con GIF y capturas |

## 5. Qué queda fuera y cuándo se revisa

- **Kubernetes, Jenkins, Argo, Prometheus:** aparecen en ofertas de DevOps, pero no caben en el alcance. Se citan como siguiente paso en el README, no como experiencia.
- **TLS en MQTT y alta disponibilidad de la BD:** documentados como mejoras tras la v1.
- **PostgreSQL 18:** existe en las imágenes de TimescaleDB. Se elige la 17 por madurez de imagen y drivers; migrar sería una decisión con ADR nuevo.
- **Observabilidad (métricas, logs estructurados):** Fase 3.
