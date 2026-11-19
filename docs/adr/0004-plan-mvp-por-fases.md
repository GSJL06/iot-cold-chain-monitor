# ADR 0004 — Plan del MVP por fases y decisiones técnicas de la Fase 4

|             |                                                  |
| ----------- | ------------------------------------------------ |
| Estado      | Aceptada.                                        |
| Fecha       | 2026-11-18                                       |
| Relacionada | ADR 0001, ADR 0002, ADR 0003, `docs/mvp-plan.md` |

## Contexto

Quedan por construir cinco servicios y su integración en 4–6 semanas a 8–10 h/semana. El riesgo principal es el alcance: construir todo y medir al final esconde los problemas hasta que son caros.

## Decisión

1. **Seis sub-fases (4A–4F) con criterio de terminado medible.** Cada una cierra con un PR integrado y CI en verde. La analítica (4G) es opcional.
2. **Infraestructura primero (4A).** Broker, base de datos, roles y ACL se validan antes de escribir servicios, con un verificador automático (`scripts/verify_broker.py`).
3. **Roles de mínimo privilegio** desde el primer día. La API usa `api_svc` con `UPDATE` solo sobre `state` y `acked_at` de `alerts`, en lugar del `api_ro` previsto en `architecture.md`.
4. **API de compresión de TimescaleDB** (`timescaledb.compress` y `add_compression_policy`) en un archivo de inicialización aislado, para poder sustituirla por la API de columnstore sin tocar el esquema.
5. **LF forzado** en `infra/`, `*.sh` y `*.sql` mediante `.gitattributes`.

## Verificación de `$share` (resuelta en 4A)

La suscripción compartida `$share/ingestor/...` con Mosquitto 2.0.23 y ACL se comprueba en 4A. Resultado: verificado el 2026-11-18. Mosquitto 2.0.23 acepta $share con la ACL del repositorio (20 de 20 mensajes repartidos 10/10; ver docs/evidence/4a-verify-broker.txt). Se mantiene el contrato MQTT v1.

- Resultado: se mantiene el contrato MQTT v1 tal como está.
- Plan B (un único ingestor con suscripción normal): no fue necesario.

## Consecuencias

**A favor**

- Cada fase deja evidencia reproducible (salidas de comandos y tests) para `docs/metrics.md`.
- Un retraso se detecta en la fase donde ocurre, no al final.
- La regla de recorte de alcance está decidida de antemano, no bajo presión.

**En contra**

- Más PR pequeños: más fricción por fase.
- El reparto con `$share` se verificó con tráfico mínimo; la 4C lo confirma con el ingestor real.
- Con un solo desarrollador, la estimación de 44 h tiene incertidumbre; se reevalúa al cerrar 4B.

## Alternativas descartadas

| Alternativa                                             | Motivo                                                                      |
| ------------------------------------------------------- | --------------------------------------------------------------------------- |
| Construir todos los servicios y medir al final          | Los problemas de integración aparecen tarde y sin evidencia intermedia      |
| Un único servicio monolítico                            | No demuestra arquitectura de microservicios ni la separación de privilegios |
| Kafka en lugar de MQTT                                  | Excesivo para 12 dispositivos; el dominio IoT usa MQTT                      |
| Rol de API de solo lectura y ACK mediante otro servicio | Un servicio más sin beneficio de seguridad real en esta escala              |
