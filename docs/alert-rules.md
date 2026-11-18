# Reglas de alerta v1

El `alert-engine` evalúa estas reglas cada 15 s sobre `readings` y `device_status`. Periodo de telemetría de referencia: 60 s.

## 1. Parámetros por clase de almacenamiento

Valores iniciales [SUPUESTO]; se cargan desde `config/storage_classes.yaml` (Fase 4).

| Clase | `min_c` | `max_c` | `max_rate_c_per_min` |
|---|---|---|---|
| `REFRIGERATED` | 2,0 | 8,0 | 0,5 |
| `FROZEN` | −25,0 | −15,0 | 0,5 |
| `ULTRA_FROZEN` | −90,0 | −60,0 | 1,0 |

## 2. Reglas

| ID | Nombre | Condición | Severidad | Notifica | Se resuelve cuando |
|---|---|---|---|---|---|
| R01 | `TEMP_EXCURSION` | Todas las lecturas de la ventana fuera de `[min_c, max_c]` | WARNING a ≥ 5 min; CRITICAL a ≥ 15 min | Sí | 3 lecturas consecutivas dentro de rango |
| R02 | `RAPID_CHANGE` | Variación > `max_rate_c_per_min` durante 3 lecturas | INFO | No | La variación vuelve a ser ≤ el límite |
| R03 | `DEVICE_SILENT` | Sin lecturas durante > 3 × periodo (180 s); CRITICAL a > 10 min | WARNING / CRITICAL | Sí | 2 lecturas nuevas |
| R04 | `LOW_BATTERY` | `battery_pct` < 20; CRITICAL si < 10 | WARNING / CRITICAL | Sí | `battery_pct` ≥ 30 (histéresis) |
| R05 | `POWER_ON_BACKUP` | `on_backup_power = true` ≥ 10 min; CRITICAL a ≥ 60 min | WARNING / CRITICAL | Sí | `on_backup_power = false` en 2 lecturas |
| R06 | `WEAK_SIGNAL` | Media móvil de 5 lecturas de `rssi_dbm` < −100 | INFO | No | Media móvil > −95 (histéresis) |

Decisiones de diseño:

- **R01 exige duración.** Una apertura de puerta de 90–180 s eleva la temperatura y vuelve a rango antes de 5 min: no abre WARNING. Es la medida contra falsas alarmas.
- **R02 no notifica.** Sirve de contexto: si R01 se abre en los 10 min siguientes, el `context` de la alerta incluye `probable_cause = "door_open_or_load"`.
- **R03 se basa en silencio**, no en el LWT. El LWT queda como dato de diagnóstico: un nodo colgado con la conexión TCP viva no dispara el LWT durante un tiempo.
- **R04 y R06 usan histéresis** (abrir y cerrar con umbrales distintos) para no parpadear.

## 3. Ciclo de vida y notificaciones

- Una alerta abierta por par `(device_id, rule_id)`.
- Notificación al abrir, al escalar de severidad y al resolver.
- Recordatorio cada 30 min para alertas CRITICAL sin ACK [SUPUESTO].
- El ACK (`POST /alerts/{id}/ack`) detiene los recordatorios; no cierra la alerta.
- El contexto de cada notificación incluye: sitio, dispositivo, temperatura actual, rango, minutos fuera de rango, `rssi_dbm`, `battery_pct`, `on_backup_power` y `last_seen`.

## 4. Escenarios del simulador y trazabilidad

| Escenario | Qué simula | Regla esperada | Regla que NO debe abrirse | Clase |
|---|---|---|---|---|
| `door_open` | Puerta abierta 90–180 s; la temperatura sube 3–4 °C y vuelve | R02 | R01 | Benigno |
| `door_left_open` | Puerta abierta ≥ 10 min | R02, R01 (WARNING → CRITICAL) | — | Fallo real |
| `compressor_failure` | Subida lenta hasta salir de rango y quedarse | R01 (WARNING → CRITICAL) | — | Fallo real |
| `node_offline` | El nodo deja de publicar | R03 | R01 | Fallo real |
| `power_outage` | `on_backup_power = true` y la batería baja | R05; luego R04 | — | Fallo real |
| `low_battery` | Batería de respaldo baja sin corte de red | R04 | R05 | Fallo real |
| `weak_signal` | `rssi_dbm` degradado | R06 | — | Diagnóstico |
| `sensor_drift` | Deriva lenta de la sonda | Ninguna regla v1 | — | Limitación conocida; la cubre `analytics` en parte |

## 5. Escenario de validación (objetivo 5 de `architecture.md`)

24 h simuladas, semilla fija, 12 dispositivos:

- **10 fallos reales:** 2 × `door_left_open`, 3 × `compressor_failure`, 2 × `node_offline`, 2 × `power_outage`, 1 × `low_battery`.
- **30 eventos benignos:** `door_open`.

Definiciones:

- **Recall** = fallos reales con al menos una alerta notificable abierta ÷ 10.
- **Precisión** = alertas notificables (R01, R03, R04, R05) que corresponden a un fallo real inyectado ÷ total de alertas notificables abiertas.
- Objetivo: recall 100 %, precisión ≥ 90 %.
