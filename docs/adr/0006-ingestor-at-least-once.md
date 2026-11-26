# ADR 0006 — Ingestor con entrega al menos una vez, confirmación manual y deduplicación en base de datos

| | |
|---|---|
| Estado | Aceptada, con una condición abierta (ver "Verificación pendiente") |
| Fecha | 2026-11-26 |
| Relacionada | ADR 0001, ADR 0004, `docs/ingestor-design.md`, `docs/mqtt-contract.md` |

## Contexto

El criterio de la Fase 4C exige que reiniciar el ingestor durante la simulación no pierda mensajes y que un duplicado no genere filas nuevas. El ingestor debe además insertar por lotes para sostener 20 mensajes/s con latencia p95 < 2 s.

Con la confirmación automática de paho, el `PUBACK` sale al volver de `on_message`. Si el mensaje espera en un lote y el proceso cae, el broker lo da por entregado y se pierde.

## Decisión

1. **Confirmación manual** (`manual_ack=True`): el `PUBACK` se envía solo después del `COMMIT` de la transacción que contiene el mensaje.
2. **Sesión persistente** (`clean_session=False`, `client_id` fijo) y QoS 1: el broker conserva lo no confirmado mientras el ingestor está caído.
3. **Deduplicación en la base de datos**: `(device_id, ts)` con `ON CONFLICT DO NOTHING`. La reentrega es inocua.
4. **Una transacción por lote** con `readings`, `rejected_messages` y `device_status`. Lectura de lotes con `unnest` en una sola sentencia.
5. **Aislamiento de mensajes venenosos**: un error de datos o de restricción reprocesa el lote mensaje a mensaje y aparta el culpable a `rejected_messages` como `schema_invalid`. Un error de conexión reintenta sin confirmar.
6. **Clasificación pura con orden de precedencia fijo** (`validation.classify`), con `NaN`, `Infinity` y `seq` fuera de `integer` tratados como `schema_invalid`. Un topic mal formado es `topic_mismatch`: no se amplía la tabla de motivos del contrato.
7. **Estado de dispositivo por orden de llegada**, no por `ts`: el `ts` del LWT es el de la conexión y una comparación lo descartaría.
8. **Suscripciones solo con sesión nueva** (`session_present` falso) para no reentregar mensajes `status` retenidos fuera de orden.
9. **Lote ≤ 20** (`max_inflight_messages` del broker) forzado en la configuración.
10. **Límite de rechazos almacenados** (120 por minuto) para que un dispositivo autenticado no llene `rejected_messages`.
11. **Esquema formal de `status.v1`** (`schemas/status.v1.schema.json`) con sus ejemplos y pruebas de contrato.
12. **Latido en fichero** (`/tmp/ingestor.alive`) para el `healthcheck` de Docker, porque el servicio no expone HTTP.

## Verificación pendiente

Mosquitto 2.0.23 podría no encolar mensajes de una suscripción compartida para un miembro desconectado con sesión persistente. La prueba de reinicio de la Fase 4C lo mide (0 huecos de `seq`).

- Si pasa: se mantiene el contrato MQTT v1 tal como está.
- Si falla: plan B, `MQTT_SHARED_GROUP=` vacío para usar suscripción normal con una sola instancia. Es un cambio de configuración; se actualizan `docs/mqtt-contract.md` (sección 2) y esta ADR.

## Consecuencias

**A favor**

- Ningún mensaje confirmado está sin guardar: el `PUBACK` es la prueba de que el `COMMIT` ocurrió.
- Reiniciar el ingestor o la base de datos no pierde datos y no los duplica.
- Un mensaje malformado nunca bloquea el flujo.
- El diseño se prueba casi entero sin servicios (clasificación y lotes con falsos).

**En contra**

- El tamaño de lote queda acotado por `max_inflight_messages` del broker.
- Los mensajes sin confirmar se reentregan tras un fallo; la deduplicación los absorbe, pero el recuento de duplicados sube.
- Un fallo prolongado de la base de datos frena al broker (los mensajes se acumulan sin confirmar). Es el comportamiento buscado.
- El `ack` desde el hilo del worker, y no desde el de red de paho, se valida con las pruebas de reinicio y carga, no con documentación.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| Confirmación automática con cola interna | Pierde los mensajes en cola si el proceso cae |
| Un `INSERT` y un `COMMIT` por mensaje | Más sencillo, pero no demuestra inserción por lotes ni sostiene la carga con margen |
| `COPY` para insertar | No admite `ON CONFLICT`; exigiría una tabla intermedia |
| Pydantic para validar | Duplicaría el contrato en código (`docs/stack.md`) |
| `asyncio` (aiomqtt, asyncpg) | Sin beneficio a 20 mensajes/s y rompe la decisión de usar paho y psycopg |
| Cola de mensajes muertos en un topic MQTT | Los rechazos ya quedan consultables en SQL y en el dashboard |
| Ordenar `status` por `ts` | Descarta el LWT, que lleva el `ts` de la conexión |
