"""Pruebas unitarias para el modelo térmico determinista y validación de tolerancias."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path
from typing import Any

import pytest
from simulator.clock import format_ts, parse_ts, tick_times
from simulator.config import load_thermal
from simulator.model import ActiveEffects, DoorOpen, Sample, ThermalDevice

REPO_ROOT = Path(__file__).resolve().parents[3]
THERMAL_PATH = REPO_ROOT / "config" / "thermal_models.yaml"

# Fecha de inicio fija y determinista (nunca datetime.now())
FIXED_START_TS = datetime(2026, 11, 18, 0, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class SimTestEvent:
    """Evento simplificado para construir ActiveEffects en pruebas del modelo."""

    scenario: str
    start_min: float
    duration_min: float | None = None
    peak_c: float | None = None


def run_simulation(
    device: ThermalDevice,
    duration_min: int,
    events: list[SimTestEvent | dict[str, Any]] | None = None,
    start_ts: datetime = FIXED_START_TS,
) -> list[tuple[datetime, Sample]]:
    """Función auxiliar que recorre los minutos de la simulación y construye ActiveEffects.

    Interpreta una lista de eventos y genera las muestras correspondientes llamando a step().
    """
    normalized_events: list[SimTestEvent] = []
    if events:
        for ev in events:
            if isinstance(ev, SimTestEvent):
                normalized_events.append(ev)
            elif isinstance(ev, dict):
                normalized_events.append(
                    SimTestEvent(
                        scenario=ev["scenario"],
                        start_min=float(ev["start_min"]),
                        duration_min=float(ev["duration_min"])
                        if ev.get("duration_min") is not None
                        else None,
                        peak_c=float(ev["peak_c"]) if ev.get("peak_c") is not None else None,
                    )
                )

    results: list[tuple[datetime, Sample]] = []
    for m in range(duration_min):
        ts = start_ts + timedelta(minutes=m)

        door_open: DoorOpen | None = None
        door_left_open = False
        compressor_failure = False
        power_outage = False
        low_battery_start_min: float | None = None
        weak_signal = False
        sensor_drift = False
        node_offline = False

        for ev in normalized_events:
            dur = ev.duration_min
            is_active = (m >= ev.start_min) and (dur is None or m < ev.start_min + dur)

            if ev.scenario == "door_open":
                d_min = dur if dur is not None else 2.5
                if ev.start_min <= m < ev.start_min + d_min:
                    peak = ev.peak_c if ev.peak_c is not None else 4.0
                    door_open = DoorOpen(
                        start_min=ev.start_min,
                        duration_min=d_min,
                        peak_c=peak,
                    )
            elif ev.scenario == "door_left_open" and is_active:
                door_left_open = True
            elif ev.scenario == "compressor_failure" and is_active:
                compressor_failure = True
            elif ev.scenario == "power_outage" and is_active:
                power_outage = True
            elif ev.scenario == "low_battery" and is_active:
                low_battery_start_min = float(ev.start_min)
            elif ev.scenario == "weak_signal" and is_active:
                weak_signal = True
            elif ev.scenario == "sensor_drift" and is_active:
                sensor_drift = True
            elif ev.scenario == "node_offline" and is_active:
                node_offline = True

        effects = ActiveEffects(
            door_open=door_open,
            door_left_open=door_left_open,
            compressor_failure=compressor_failure,
            power_outage=power_outage,
            low_battery_start_min=low_battery_start_min,
            weak_signal=weak_signal,
            sensor_drift=sensor_drift,
            node_offline=node_offline,
        )

        sample = device.step(ts=ts, t_min=float(m), effects=effects)
        results.append((ts, sample))

    return results


def test_clock_parse_format_and_ticks():
    """Verifica parse_ts, format_ts y tick_times del módulo clock."""
    ts = parse_ts("2026-11-18T15:30:00Z")
    assert ts.tzinfo is not None
    assert format_ts(ts) == "2026-11-18T15:30:00Z"

    # Acepta sufijo Z y formatea en UTC
    ts_local = datetime(2026, 11, 18, 15, 30, 0)
    assert format_ts(ts_local) == "2026-11-18T15:30:00Z"

    # tick_times con count finito
    ticks = list(tick_times(ts, period_s=60, phase_s=5, count=3))
    assert len(ticks) == 3
    assert format_ts(ticks[0]) == "2026-11-18T15:30:05Z"
    assert format_ts(ticks[1]) == "2026-11-18T15:31:05Z"
    assert format_ts(ticks[2]) == "2026-11-18T15:32:05Z"

    # tick_times con count=None (generador infinito)
    inf_ticks = list(islice(tick_times(ts, period_s=10, phase_s=0, count=None), 5))
    assert len(inf_ticks) == 5
    assert format_ts(inf_ticks[4]) == "2026-11-18T15:30:40Z"


def test_thermal_device_reproducibility():
    """Verifica reproducibilidad estricta: misma semilla = mismas muestras; distinta = distintas."""
    thermal = load_thermal(THERMAL_PATH)

    dev_a = ThermalDevice("SIM-HA-001", "REFRIGERATED", seed=42, thermal=thermal)
    dev_b = ThermalDevice("SIM-HA-001", "REFRIGERATED", seed=42, thermal=thermal)
    dev_c = ThermalDevice("SIM-HA-001", "REFRIGERATED", seed=999, thermal=thermal)

    results_a = run_simulation(dev_a, duration_min=60)
    results_b = run_simulation(dev_b, duration_min=60)
    results_c = run_simulation(dev_c, duration_min=60)

    samples_a = [s for _, s in results_a]
    samples_b = [s for _, s in results_b]
    samples_c = [s for _, s in results_c]

    assert samples_a == samples_b, "Misma semilla debe producir exactamente las mismas muestras"
    assert samples_a != samples_c, "Semillas distintas deben producir muestras diferentes"


@pytest.mark.parametrize(
    ("device_id", "storage_class", "min_c", "max_c", "max_rate_c_per_min"),
    [
        ("SIM-HA-001", "REFRIGERATED", 2.0, 8.0, 0.5),
        ("SIM-HA-004", "FROZEN", -25.0, -15.0, 0.5),
        ("SIM-LC-003", "ULTRA_FROZEN", -90.0, -60.0, 1.0),
    ],
)
def test_design_test_2_baseline(
    device_id: str,
    storage_class: str,
    min_c: float,
    max_c: float,
    max_rate_c_per_min: float,
):
    """Prueba 2: Línea base de 24 h sin eventos dentro de rango y |ΔT| < 0.9 * max_rate."""
    thermal = load_thermal(THERMAL_PATH)
    dev = ThermalDevice(
        device_id=device_id,
        storage_class=storage_class,
        seed=42,
        thermal=thermal,
    )

    results = run_simulation(dev, duration_min=1440)
    temps = [s.temp_c for _, s in results]

    in_range = all(min_c <= t <= max_c for t in temps)
    msg_range = f"Clase {storage_class} salió de rango: min={min(temps)}, max={max(temps)}"
    assert in_range, msg_range

    diffs = [round(abs(temps[i] - temps[i - 1]), 1) for i in range(1, len(temps))]
    max_diff = max(diffs)
    threshold = 0.9 * max_rate_c_per_min
    msg_rate = f"Clase {storage_class} excedió tasa: {max_diff:.3f} >= {threshold:.3f}"
    assert max_diff < threshold, msg_rate


def test_design_test_3_door_open():
    """Prueba 3: door_open (pico 4.0 °C, 150 s = 2.5 min) con tiempo fuera <= 3 min."""
    thermal = load_thermal(THERMAL_PATH)

    for seed in range(50):
        dev = ThermalDevice(
            device_id="SIM-HA-001",
            storage_class="REFRIGERATED",
            seed=seed,
            thermal=thermal,
        )
        events = [
            SimTestEvent(
                scenario="door_open",
                start_min=60.0,
                duration_min=2.5,
                peak_c=4.0,
            )
        ]
        results = run_simulation(dev, duration_min=120, events=events)

        consecutive_out = 0
        max_consecutive_out = 0
        for _, sample in results:
            if sample.temp_c > 8.0 or sample.temp_c < 2.0:
                consecutive_out += 1
                if consecutive_out > max_consecutive_out:
                    max_consecutive_out = consecutive_out
            else:
                consecutive_out = 0

        msg = f"Semilla {seed}: fuera de rango {max_consecutive_out} min (> 3 min)"
        assert max_consecutive_out <= 3, msg


def test_design_test_4_door_left_open():
    """Prueba 4: door_left_open con 12 min en nevera produce tiempo continuo fuera >= 15 min."""
    thermal = load_thermal(THERMAL_PATH)

    for seed in range(50):
        dev = ThermalDevice(
            device_id="SIM-HA-001",
            storage_class="REFRIGERATED",
            seed=seed,
            thermal=thermal,
        )
        events = [
            SimTestEvent(
                scenario="door_left_open",
                start_min=60.0,
                duration_min=12.0,
            )
        ]
        results = run_simulation(dev, duration_min=180, events=events)

        consecutive_out = 0
        max_consecutive_out = 0
        for _, sample in results:
            if sample.temp_c > 8.0 or sample.temp_c < 2.0:
                consecutive_out += 1
                if consecutive_out > max_consecutive_out:
                    max_consecutive_out = consecutive_out
            else:
                consecutive_out = 0

        msg = f"Semilla {seed}: fuera de rango {max_consecutive_out} min (< 15 min)"
        assert max_consecutive_out >= 15, msg


def test_design_test_5_compressor_failure():
    """Prueba 5: compressor_failure sale de rango entre 35 y 80 min tras inicio; |ΔT| < 0.5 °C."""
    thermal = load_thermal(THERMAL_PATH)
    failure_start = 60.0

    for seed in range(50):
        dev = ThermalDevice(
            device_id="SIM-HA-001",
            storage_class="REFRIGERATED",
            seed=seed,
            thermal=thermal,
        )
        events = [
            SimTestEvent(
                scenario="compressor_failure",
                start_min=failure_start,
                duration_min=None,
            )
        ]
        results = run_simulation(dev, duration_min=200, events=events)

        exit_delay: int | None = None
        prev_temp: float | None = None
        max_diff = 0.0

        for m, (_, sample) in enumerate(results):
            if prev_temp is not None:
                diff = abs(sample.temp_c - prev_temp)
                if diff > max_diff:
                    max_diff = diff
            prev_temp = sample.temp_c

            if m >= failure_start and exit_delay is None:
                if sample.temp_c > 8.0 or sample.temp_c < 2.0:
                    exit_delay = int(m - failure_start)

        msg_delay = f"Semilla {seed}: retardo {exit_delay} min (esperado entre 35 y 80 min)"
        assert exit_delay is not None and 35 <= exit_delay <= 80, msg_delay
        msg_diff = f"Semilla {seed}: |ΔT| máx fue {max_diff:.3f} °C (esperado < 0.5 °C)"
        assert max_diff < 0.5, msg_diff


def test_design_test_7_power_outage():
    """Prueba 7: power_outage mantiene on_backup_power=True y batería < 20 % entre min 90 y 110."""
    thermal = load_thermal(THERMAL_PATH)
    outage_start = 60.0
    outage_duration = 120.0

    for seed in range(50):
        dev = ThermalDevice(
            device_id="SIM-FB-001",
            storage_class="REFRIGERATED",
            seed=seed,
            thermal=thermal,
        )
        events = [
            SimTestEvent(
                scenario="power_outage",
                start_min=outage_start,
                duration_min=outage_duration,
            )
        ]
        results = run_simulation(dev, duration_min=200, events=events)

        below_20_minute: int | None = None
        for m, (_, sample) in enumerate(results):
            is_during_outage = outage_start <= m < outage_start + outage_duration
            if is_during_outage:
                msg_backup = f"Semilla {seed}: en minuto {m} on_backup_power debió ser True"
                assert sample.on_backup_power is True, msg_backup
                if sample.battery_pct < 20.0 and below_20_minute is None:
                    below_20_minute = int(m - outage_start)
            else:
                msg_normal = f"Semilla {seed}: en minuto {m} on_backup_power debió ser False"
                assert sample.on_backup_power is False, msg_normal

        msg_drain = f"Semilla {seed}: batería < 20 % en min {below_20_minute} (esperado 90-110)"
        assert below_20_minute is not None and 90 <= below_20_minute <= 110, msg_drain


def test_design_test_8_low_battery():
    """Prueba 8: low_battery tiene batería < 20 % entre min 50 y 70 con on_backup_power=False."""
    thermal = load_thermal(THERMAL_PATH)
    low_start = 60.0
    low_duration = 100.0

    for seed in range(50):
        dev = ThermalDevice(
            device_id="SIM-FB-003",
            storage_class="REFRIGERATED",
            seed=seed,
            thermal=thermal,
        )
        events = [
            SimTestEvent(
                scenario="low_battery",
                start_min=low_start,
                duration_min=low_duration,
            )
        ]
        results = run_simulation(dev, duration_min=200, events=events)

        below_20_minute: int | None = None
        for m, (_, sample) in enumerate(results):
            msg_bk = f"Semilla {seed}: on_backup_power debe ser False en minuto {m}"
            assert sample.on_backup_power is False, msg_bk
            is_during_low = low_start <= m < low_start + low_duration
            if is_during_low and sample.battery_pct < 20.0 and below_20_minute is None:
                below_20_minute = int(m - low_start)

        msg_drain = f"Semilla {seed}: batería < 20 % en min {below_20_minute} (esperado 50-70)"
        assert below_20_minute is not None and 50 <= below_20_minute <= 70, msg_drain


def test_design_test_9_weak_signal():
    """Prueba 9: weak_signal tiene media móvil de 5 lecturas de RSSI < -100 dBm."""
    thermal = load_thermal(THERMAL_PATH)
    weak_start = 60.0
    weak_duration = 30.0

    for seed in range(50):
        dev = ThermalDevice(
            device_id="SIM-FB-001",
            storage_class="REFRIGERATED",
            seed=seed,
            thermal=thermal,
        )
        events = [
            SimTestEvent(
                scenario="weak_signal",
                start_min=weak_start,
                duration_min=weak_duration,
            )
        ]
        results = run_simulation(dev, duration_min=120, events=events)

        rssi_history: list[int] = []
        for m, (_, sample) in enumerate(results):
            if weak_start <= m < weak_start + weak_duration:
                rssi_history.append(sample.rssi_dbm)
                if len(rssi_history) >= 5:
                    window_mean = sum(rssi_history[-5:]) / 5.0
                    msg = f"Semilla {seed}: media móvil {window_mean:.1f} no es < -100 dBm"
                    assert window_mean < -100.0, msg
