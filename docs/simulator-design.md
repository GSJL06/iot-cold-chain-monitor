# Diseño del simulador — ColdChain Monitor

| | |
|---|---|
| Estado | Fase 4B — diseño (v0.1) |
| Fecha | 2026-11-19 |
| Relacionados | `docs/mqtt-contract.md`, `docs/alert-rules.md` (secciones 4 y 5), `docs/mvp-plan.md`, `docs/adr/0005-simulador-determinista.md` |
| Configuración | `config/fleet.yaml`, `config/thermal_models.yaml`, `config/scenario_catalog.yaml`, `config/scenarios/*.yaml` |

## 1. Objetivo y principios

El simulador produce telemetría `telemetry.v1` de 12 dispositivos y la publica en MQTT. También genera la verdad de referencia (ground truth) con la que se mide el motor de alertas.

1. **Determinista.** Misma semilla y misma configuración producen exactamente los mismos datos.
2. **Generar no es publicar.** El modelo y los escenarios se ejecutan sin red ni reloj real. Publicar en MQTT es una capa fina encima. Así una prueba simula 24 h en segundos.
3. **Reloj de simulación.** El tiempo lo decide el simulador, no `datetime.now()`. Solo el modo `publish` consulta el reloj del sistema, para fijar el inicio.
4. **Una sola fuente de configuración.** Parámetros en `config/`, sin números mágicos en el código. Un test comprueba que `fleet.yaml` coincide con el seed de la base de datos.
5. **Sin dependencias extra.** Biblioteca estándar, PyYAML y paho-mqtt (`docs/stack.md`, capa 13).

## 2. Estructura

```
services/simulator/
├── Dockerfile
├── README.md
├── requirements.txt          (ya existe, versiones fijadas)
└── src/simulator/
    ├── __init__.py
    ├── __main__.py           # python -m simulator
    ├── cli.py                # subcomandos generate y publish
    ├── clock.py              # tiempo simulado y fases por dispositivo
    ├── config.py             # carga y validación de los YAML
    ├── model.py              # modelo térmico de un dispositivo
    ├── scenarios.py          # calendario de eventos, generador benigno, ground truth
    ├── fleet.py              # conjunto de dispositivos y generación de lecturas
    └── publisher.py          # clientes MQTT, status y LWT, backfill
```

| Módulo | Responsabilidad | Depende de red |
|---|---|---|
| `config`, `clock`, `model`, `scenarios`, `fleet` | Lógica pura y determinista | No |
| `publisher`, `cli` (subcomando `publish`) | Publicación MQTT | Sí |

## 3. Tiempo y determinismo

- **Tick:** cada dispositivo emite una lectura cada `period_s` (60 s). El modelo avanza con `dt = period_s / 60` minutos.
- **Fase:** el dispositivo número `i` (orden de `fleet.yaml`, desde 0) emite en `inicio + i × phase_step_s` más múltiplos del periodo. Reparte la carga y evita 12 mensajes simultáneos.
- **Ancla de escenarios:** `anchor_ts` es el instante en el que `t_min = 0`. Todos los `start_min` de los escenarios son relativos a ella. Sin backfill, `anchor_ts` = inicio; con backfill de N horas, `anchor_ts` = inicio + N horas (el histórico previo es de funcionamiento normal).
- **Aleatoriedad:** un `random.Random` por dispositivo, sembrado con la cadena `f"{seed}:{device_id}"`. Los eventos benignos usan otro generador, sembrado con `f"{seed_benigno}:door_open_schedule"`.
- **Orden fijo de sorteos.** Al crear el dispositivo, en este orden: temperatura inicial `uniform(banda)`, estado del compresor `random() < 0.5`, media de RSSI `uniform(rango)`. En cada tick, siempre y en este orden: ruido de temperatura `gauss`, paso de humedad `gauss` (si la clase tiene humedad), ruido de RSSI `gauss`. Se sortea aunque el nodo esté en silencio, para que un escenario no desplace la secuencia aleatoria.
- **Marca de tiempo:** UTC, formato `%Y-%m-%dT%H:%M:%SZ`, sin milisegundos.

## 4. Modelo térmico

Estado por dispositivo: temperatura del producto `T`, compresor encendido `comp`, bulto de aire `bump` (°C), deriva del sensor `drift` (°C), batería `battery` (%), humedad `h` (%), media de RSSI.

### 4.1 Ecuaciones por tick

```
hora = hora UTC del tick (0 a 24, con fracción)
amb  = ambient.mean_c + ambient.daily_amplitude_c × sin(2π × (hora − (peak_hour_utc − 6)) / 24)

compresor_habilitado = no hay power_outage activo (si cooling_lost) y no hay compressor_failure activo
si compresor_habilitado:
    si comp y T ≤ banda_baja:  comp = falso
    si no comp y T ≥ banda_alta: comp = verdadero
si no: comp = falso

dT_por_min = leak_per_min × (amb − T)
           − (cool_c_per_min si comp, si no 0)
           + (door_left_open_gain_c_per_min si door_left_open activo, si no 0)
T += dT_por_min × dt

door_open activo:  bump = pico × min(1, (t_min − inicio + dt) / duración_min)
si no:             bump = bump × exp(−dt / decay_tau_min)

si sensor_drift activo: drift += rate_c_per_min × dt        (la deriva se conserva al terminar)

temp_c = redondeo_1_decimal(T + bump + drift + gauss(0, noise_sigma_c))
```

`compressor_failure` queda activo desde `start_min`; si el evento trae `duration_min`, termina ahí y el compresor vuelve a habilitarse.

### 4.2 Resto de variables

- **Humedad** (clases con `humidity`): `h += 0.1 × (mean_pct − h) + gauss(0, step_sigma)`, acotada a `[min_pct, max_pct]`, un decimal. Valor inicial `mean_pct`. En ultracongelación no se emite el campo.
- **RSSI:** `round(gauss(media, sigma_dbm))` acotado a `[−140, 0]`. Con `weak_signal` activo, `media = mean_dbm` y `sigma = sigma_dbm` del escenario.
- **Batería:**
  - Sin corte ni escenario de batería: sube `recharge_pct_per_min × dt` hasta 100.
  - `power_outage` activo: baja `battery_drain_pct_per_min × dt`, mínimo 0, y `on_backup_power = true`.
  - `low_battery`: en el primer tick vale `start_pct`; después baja `battery_drain_pct_per_min × dt`, mínimo 0.
  - Un decimal. Estado inicial: 100.
- **`seq`:** empieza en 0 y sube 1 por cada lectura emitida. En silencio (`node_offline`) no sube: al volver no hay hueco, porque el nodo no "perdió" mensajes, no los generó.
- **`fw`:** `firmware` de `fleet.yaml`.

### 4.3 Parámetros por clase

Valores en `config/thermal_models.yaml`.

| Clase | Banda de control | Enfriamiento | Fuga | Ruido σ | Ganancia puerta abierta |
|---|---|---|---|---|---|
| `REFRIGERATED` | 3,5 a 5,5 °C | 0,20 °C/min | 0,004 /min | 0,05 °C | 0,9 °C/min |
| `FROZEN` | −22,0 a −19,0 °C | 0,25 °C/min | 0,0025 /min | 0,05 °C | 1,0 °C/min |
| `ULTRA_FROZEN` | −84,0 a −78,0 °C | 0,35 °C/min | 0,0008 /min | 0,15 °C | 1,5 °C/min |

Entorno: ambiente 22 °C ± 1,5 °C con máximo a las 15:00 UTC.

## 5. Escenarios

Cada evento tiene `scenario`, `device_id`, `start_min` y, según el caso, `duration_min`. El catálogo con clase, reglas esperadas y prohibidas está en `config/scenario_catalog.yaml`.

| Escenario | Efecto en el modelo | Parámetros | Firma esperada (medida en el prototipo) |
|---|---|---|---|
| `door_open` | `bump` sube lineal hasta `peak_c` en `duration_s` y cae con τ = 2 min | `peak_c` 3–4 °C, `duration_s` 90–150 s | Tiempo continuo fuera de rango ≤ 3 min (máx. medido: 2 min en nevera, 1 en congelador, 0 en ultracongelador); variación > 0,5 °C/min en la subida |
| `door_left_open` | `dT_por_min` suma la ganancia de la clase mientras dura | `duration_min` ≥ 12 | Tiempo continuo fuera de rango ≥ 23 min (12 min de puerta) y ≥ 35 min en nevera; variación 0,9–1,0 °C/min |
| `compressor_failure` | Compresor deshabilitado desde `start_min` | `duration_min` opcional | Sale de rango entre 40 y 75 min después en nevera y congelador; variación máxima 0,3 °C/min (sin disparar R02) |
| `node_offline` | No se emiten lecturas; el modelo sigue avanzando | `duration_min` ≥ 15 | Silencio de `duration_min` minutos, `seq` sin huecos |
| `power_outage` | `on_backup_power = true`, batería baja 0,8 %/min y el compresor se deshabilita | `duration_min` ≥ 70 | Batería < 20 % a los ~100 min y < 10 % a los ~112 min; fuera de rango ~58 min con corte de 120 min |
| `low_battery` | Batería salta a 35 % y baja 0,25 %/min, sin corte | `duration_min` ≥ 100 | < 20 % a los 60 min y < 10 % a los 100 min |
| `weak_signal` | RSSI medio −108 dBm, σ 3 | `duration_min` ≥ 10 | Media móvil de 5 lecturas < −100 dBm |
| `sensor_drift` | `drift` crece 0,01 °C/min | `duration_min` | Sesgo gradual; ninguna regla v1 lo detecta |

Restricciones de aplicabilidad (`applicable_classes`): `door_left_open`, `compressor_failure` y `power_outage` no se asignan a ultracongelación, porque con su inercia térmica no salen de rango dentro de las ventanas del plan (`compressor_failure` tarda ~291 min; `door_left_open` de 12 min no sale de rango).

## 6. Plan de validación de 24 h

Archivo: `config/scenarios/validation_24h.yaml` (semilla 42). Cumple el objetivo 5 de `architecture.md`: 10 fallos reales en 10 dispositivos distintos y 30 aperturas de puerta benignas.

| Fallo | Dispositivo (clase) | Inicio | Duración |
|---|---|---|---|
| `power_outage` | SIM-FB-001 (R) | 240 | 120 |
| `door_left_open` | SIM-HA-002 (R) | 300 | 18 |
| `compressor_failure` | SIM-HA-001 (R) | 420 | hasta el final |
| `node_offline` | SIM-HA-003 (R) | 600 | 25 |
| `low_battery` | SIM-FB-003 (R) | 660 | 110 |
| `compressor_failure` | SIM-FB-004 (F) | 780 | hasta el final |
| `door_left_open` | SIM-FB-002 (R) | 900 | 14 |
| `power_outage` | SIM-HA-004 (F) | 1000 | 130 |
| `compressor_failure` | SIM-LC-001 (R) | 1080 | hasta el final |
| `node_offline` | SIM-LC-002 (F) | 1200 | 30 |

Los dos ultracongeladores (SIM-LC-003 y SIM-LC-004) solo reciben eventos benignos.

### 6.1 Algoritmo de los eventos benignos

```
ventanas_bloqueadas[dispositivo] = para cada fallo del dispositivo:
    [inicio − 10, fin + margin_min]   # fin = duration_min; para compressor_failure: fin de la simulación
    # margin_min sale de scenario_catalog.yaml
rng = Random(f"{seed_benigno}:door_open_schedule")
dispositivos = ids ordenados alfabéticamente
repetir hasta tener `count` eventos o 10 000 intentos:
    d      = rng.choice(dispositivos)
    inicio = rng.randint(30, duration_min − 30)           # minuto entero
    dur_s  = rng.choice(duration_s)
    pico   = round(rng.uniform(peak_c[0], peak_c[1]), 1)
    descartar si [inicio, inicio + dur_s/60] se solapa con una ventana bloqueada de d
    descartar si |inicio − inicio_otro| < min_gap_min para otro benigno de d
    guardar (d, inicio, dur_s, pico)
ordenar por (inicio, dispositivo)
```

Si tras 10 000 intentos faltan eventos, el simulador lanza un error explícito.

## 7. Verdad de referencia (ground truth)

El subcomando `generate` escribe `ground_truth.json`: una lista ordenada de eventos.

```json
{
  "scenario": "door_left_open",
  "device_id": "SIM-HA-002",
  "class": "real_fault",
  "start_ts": "2026-11-18T05:00:00Z",
  "end_ts": "2026-11-18T05:18:00Z",
  "window_end_ts": "2026-11-18T06:48:00Z",
  "expected_rules": ["R01"],
  "forbidden_rules": [],
  "allowed_rules": ["R02"]
}
```

- `window_end_ts` = `end_ts` + `margin_min` del catálogo. Con `margin_min: 0` y sin `duration_min` (fallo abierto), `end_ts` y `window_end_ts` son el final de la simulación.
- El evaluador de la Fase 4D usa estas ventanas. Una alerta cuenta como acierto si su dispositivo tiene un evento `real_fault` cuya ventana `[start_ts, window_end_ts]` contiene su `opened_at`.

## 8. Mensajes MQTT

- **Telemetría:** `coldchain/v1/{site_id}/{device_id}/telemetry`, QoS 1, sin retain, payload `telemetry.v1` con `humidity_pct` solo en las clases que la miden. Debe validar contra `schemas/telemetry.v1.schema.json`.
- **Estado:** `coldchain/v1/{site_id}/{device_id}/status`, QoS 1, retain, payload `{"schema":"status.v1","device_id":"...","state":"online","ts":"..."}`. Se publica `online` al conectar y `offline` al cerrar con Ctrl+C o `SIGTERM`.
- **LWT:** cada cliente declara el `status` `offline` como último deseo. `node_offline` simula un nodo colgado: sigue conectado y no publica, así que no dispara el LWT (R03 se basa en el silencio, no en el LWT).
- **Clientes:** uno por dispositivo (`client_id` = `device_id`; usuario = `device_id`; contraseña `MQTT_DEVICE_PASSWORD`), `keepalive` 60 s, paho-mqtt 2.x con `CallbackAPIVersion.VERSION2`.

## 9. Interfaz de línea de comandos

```
python -m simulator generate --scenario config/scenarios/validation_24h.yaml \
    --start 2026-11-18T00:00:00Z --out out/validation_24h.jsonl --truth out/validation_24h_truth.json

python -m simulator publish --scenario config/scenarios/demo.yaml \
    [--backfill-hours 6] [--period-s 60] [--seed 11]
```

| Subcomando | Qué hace |
|---|---|
| `generate` | Genera todas las lecturas de `duration_min` sin red y las escribe como JSON Lines (una lectura por línea, orden por `ts`). Escribe también `ground_truth.json` |
| `publish` | Publica en el broker en tiempo real. Con `--backfill-hours N` publica primero N horas de histórico normal a máxima velocidad (con `ts` pasados) y sigue en directo. `--period-s` acepta valores ≥ 1 |

Variables de entorno de `publish`: `MQTT_HOST` (por defecto `localhost`), `MQTT_PORT` (1883), `MQTT_DEVICE_PASSWORD` (obligatoria). Cierre limpio con Ctrl+C o `SIGTERM`.

No existe aceleración del tiempo al publicar: un `ts` adelantado más de 5 minutos al reloj del servidor se rechaza con `clock_skew` (`mqtt-contract.md`, sección 5). El histórico usa `ts` pasados, que son válidos.

## 10. Pruebas de aceptación (tolerancias)

Calibradas con 200 semillas del prototipo. Cada una debe pasar para cualquier semilla de prueba.

| # | Prueba | Criterio |
|---|---|---|
| 1 | Reproducibilidad | Dos ejecuciones de `generate` con la misma semilla producen archivos con el mismo SHA-256; con otra semilla, distinto |
| 2 | Línea base | 24 h sin eventos: toda temperatura dentro de `[min_c, max_c]` de su clase y `|ΔT|` entre lecturas consecutivas < 0,9 × `max_rate_c_per_min` |
| 3 | `door_open` | Con pico 4,0 °C y 150 s, en 50 semillas, el tiempo continuo fuera de rango es ≤ 3 min |
| 4 | `door_left_open` | Con 12 min en nevera: tiempo continuo fuera de rango ≥ 15 min |
| 5 | `compressor_failure` | Sale de rango entre 35 y 80 min tras el inicio; `|ΔT|` máx. entre lecturas < 0,5 °C |
| 6 | `node_offline` | Sin lecturas durante todo el evento y `seq` sin huecos al reanudar |
| 7 | `power_outage` | `on_backup_power` verdadero durante todo el evento; batería < 20 % entre los minutos 90 y 110 |
| 8 | `low_battery` | Batería < 20 % entre los minutos 50 y 70 con `on_backup_power` falso |
| 9 | `weak_signal` | Media de 5 lecturas consecutivas de RSSI < −100 dBm durante el evento |
| 10 | Plan de 24 h | 12 dispositivos × 1440 lecturas menos las silenciadas; 10 eventos `real_fault` y 30 `door_open` en `ground_truth.json`; ningún benigno dentro de la ventana de un fallo del mismo dispositivo ni a menos de 45 min de otro benigno |
| 11 | Contrato | 100 % de las lecturas de `validation_24h` validan contra `telemetry.v1.schema.json` |
| 12 | Configuración | `fleet.yaml` coincide con `003_seed.sql` (id, sitio y clase de los 12 dispositivos); todo escenario del plan es aplicable a la clase de su dispositivo |

## 11. Validación del diseño

Antes de pasar el diseño a código se comprobó con un prototipo de referencia (no incluido en el repositorio), con las mismas ecuaciones y parámetros:

- Línea base: todas las clases dentro de rango; variación máxima por lectura 0,40 °C/min (nevera y congelador, límite 0,5) y 0,80 (ultracongelador, límite 1,0).
- `door_open` en el peor caso (pico 4,0 °C, 150 s, 300 semillas con picos y duraciones aleatorios): ≤ 2 min continuos fuera de rango, por debajo de los 5 min de R01.
- Plan de 24 h con un detector simplificado de R01, R03, R04 y R05: 10 de 10 fallos detectados y 0 alertas fuera de fallo reales (ninguna de las 30 aperturas de puerta dispara R01).
- Rangos por semilla: `compressor_failure` sale de rango entre 40 y 74 min; `door_left_open` de 12 min mantiene 23–49 min fuera de rango.

Esto valida el diseño, no el motor de alertas real, que se evalúa en la Fase 4D.

## 12. Limitaciones

- Modelo de primer orden: sin descongelación periódica, sin carga térmica de producto ni estratificación del aire.
- Un solo perfil de ambiente por día; sin estaciones.
- Los 12 dispositivos comparten contraseña MQTT (datos sintéticos; riesgo aceptado, `threat-model.md`).
- El prototipo valida las firmas; la calibración frente a datos reales queda fuera del alcance.
