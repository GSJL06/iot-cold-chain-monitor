# Modelo de amenazas — ColdChain Monitor

| | |
|---|---|
| Estado | Fase 3 — diseño (v0.1). Los controles se implementan y prueban en la Fase 4 |
| Fecha | 2026-11-18 |
| Método | STRIDE sobre el diagrama de flujo de `docs/architecture.md` |

## 1. Alcance y supuestos

- Entorno de demostración local con `docker compose`. Datos sintéticos, sin datos personales.
- Atacante considerado: un dispositivo o cliente en la red local que puede hablar con el broker o con la API, y un conjunto de dependencias externas.
- Fuera de alcance: seguridad física de los dispositivos, ataques al sistema operativo del anfitrión, ataques a GitHub o a Telegram.

## 2. Activos

| Activo | Por qué importa |
|---|---|
| Lecturas de temperatura | Base de las decisiones de calidad; su manipulación oculta excursiones |
| Alertas y su estado | Disparan la respuesta del técnico |
| Credenciales (`.env`, token de Telegram, `API_KEY`) | Dan acceso al broker, a la base de datos y al canal de avisos |
| Disponibilidad del pipeline | Un pipeline caído equivale a "sin alertas" |

## 3. Límites de confianza

1. Dispositivo → broker (cliente no confiable).
2. Broker → ingestor (mensajes no confiables hasta validarlos).
3. Cliente HTTP → API.
4. Servicios → base de datos.
5. `alert-engine` → Telegram (salida a Internet).

## 4. Amenazas y mitigaciones

| # | STRIDE | Escenario | Mitigación en el diseño | Estado | Riesgo residual |
|---|---|---|---|---|---|
| 1 | Spoofing | Un cliente publica como otro dispositivo | Usuario y contraseña por dispositivo; ACL `coldchain/v1/+/%u/#`; el ingestor rechaza `topic_mismatch` y `unknown_device` | Fase 4 | Credencial de un dispositivo robada permite suplantarlo |
| 2 | Tampering | Payload con temperatura falsa o fuera de rango | Schema estricto con rangos físicos; `clock_skew`; rechazos a `rejected_messages` | Diseñado | Un dispositivo comprometido puede mentir con valores plausibles. La firma de payloads queda fuera de v1 |
| 3 | Repudiation | Alguien reconoce (ACK) una alerta sin trazabilidad | ACK con `X-API-Key`; registro del ACK con `acked_at` y log estructurado | Fase 4 | Una sola clave compartida: no hay identidad por usuario |
| 4 | Info. disclosure | Secretos subidos al repo o a logs | `.env` ignorado; secret scanning con push protection; hook `detect-private-key`; los logs no incluyen credenciales | Fase 3 | Un secreto pegado en un issue o PR sigue siendo posible |
| 5 | Info. disclosure | Grafana o la API accesibles sin control | Grafana con contraseña por `.env` y acceso anónimo desactivado; puertos publicados solo en `127.0.0.1` | Fase 4 | Quien tenga acceso al anfitrión accede a todo |
| 6 | Info. disclosure | Token del bot de Telegram filtrado | Solo en `.env`; nunca en logs ni en el contexto de las alertas; revocable desde BotFather | Fase 4 | Hasta revocarlo, permite enviar mensajes en nombre del bot |
| 7 | Denial of service | Inundación de mensajes MQTT | `message_size_limit 4096` y `max_connections` en Mosquitto; inserción por lotes en el ingestor | Fase 4 | Sin límites de tasa por cliente en v1 |
| 8 | Denial of service | Pipeline o ingestor caído sin que nadie lo note | R03 detecta dispositivos mudos; healthchecks de Docker; panel "retraso de ingestión" | Fase 4 | Si Grafana también cae, no hay aviso |
| 9 | Denial of service | Consulta pesada a la API | Paginación obligatoria, `LIMIT` máximo de 1000, `statement_timeout = 5s` en el rol `api_ro` | Fase 4 | Sin límite de peticiones por IP |
| 10 | Elevation of privilege | Inyección SQL o rol con demasiados permisos | Consultas parametrizadas; regla `S608` de ruff; roles `ingestor`, `alert_engine`, `api_ro`, `grafana_ro` con permisos mínimos | Fase 4 | Un error de configuración de roles |
| 11 | Elevation of privilege | Contenedor comprometido | Usuario no root; `cap_drop: [ALL]`; sin `privileged`; sin montajes del anfitrión salvo configuración de solo lectura | Fase 4 | Vulnerabilidades del kernel o de Docker |
| 12 | Tampering | Dependencia o imagen vulnerable o comprometida | Versiones y tags exactos; Dependabot; `pip-audit` semanal; imágenes oficiales | Fase 3 | Sin fijar por hash ni digest en v1 |

## 5. Riesgos aceptados en v1

- MQTT sin TLS: el tráfico viaja en claro dentro de la red local.
- Sin cifrado en reposo de la base de datos.
- Sin identidad por usuario (una `API_KEY` compartida) y sin multi-tenant.
- Sin firma criptográfica de las lecturas.

Cada punto se documenta en el README como mejora futura.

## 6. Verificación

Las pruebas de abuso de `docs/engineering-practices.md` (sección 5.2) cubren las amenazas 1, 3, 7 y 9. Cada amenaza con estado "Fase 4" pasa a "Verificada" cuando su control tiene una prueba automática en CI.
