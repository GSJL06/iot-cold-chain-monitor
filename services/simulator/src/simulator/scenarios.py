"""Gestión de escenarios, generación de eventos benignos y verdad de referencia."""

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from simulator.clock import format_ts
from simulator.config import CatalogEntry, FleetCfg, ScenarioPlan
from simulator.model import ActiveEffects, DoorOpen


@dataclass(frozen=True)
class Event:
    """Evento simulado que afecta a un dispositivo."""

    scenario: str
    device_id: str
    start_min: float
    end_min: float | None
    params: dict[str, Any] = field(default_factory=dict)


def expand_plan(
    plan: ScenarioPlan,
    fleet: FleetCfg,
    catalog: dict[str, CatalogEntry],
) -> list[Event]:
    """Combina fallos del plan con aperturas de puerta benignas según sección 6.1."""
    events: list[Event] = []

    # 1. Ventanas bloqueadas por dispositivo (sección 6.1)
    blocked_windows: dict[str, list[tuple[float, float]]] = {d.device_id: [] for d in fleet.devices}
    for fault in plan.faults:
        end_min = (
            float(fault.start_min + fault.duration_min) if fault.duration_min is not None else None
        )
        events.append(
            Event(
                scenario=fault.scenario,
                device_id=fault.device_id,
                start_min=float(fault.start_min),
                end_min=end_min,
                params={},
            )
        )

        fin = end_min if end_min is not None else float(plan.duration_min)
        margin = float(catalog[fault.scenario].margin_min)
        blocked_windows[fault.device_id].append((float(fault.start_min - 10), fin + margin))

    # 2. Generación determinista de eventos benignos (sección 6.1)
    devices = sorted([d.device_id for d in fleet.devices])
    for spec in plan.benign_specs:
        scenario = str(spec.get("scenario", "door_open"))
        count = int(spec["count"])
        seed_benigno = spec.get("seed", plan.seed)
        min_gap_min = float(spec.get("min_gap_min", 45.0))
        duration_s_options = list(spec["duration_s"])
        peak_c_range = spec["peak_c"]

        rng = random.Random(f"{seed_benigno}:door_open_schedule")
        benign_by_device: dict[str, list[int]] = {d: [] for d in devices}
        generated_benign: list[tuple[str, int, int, float]] = []

        for _ in range(10000):
            if len(generated_benign) >= count:
                break
            d = rng.choice(devices)
            inicio = rng.randint(30, plan.duration_min - 30)
            dur_s = rng.choice(duration_s_options)
            pico = round(rng.uniform(peak_c_range[0], peak_c_range[1]), 1)

            end_min = inicio + dur_s / 60.0
            if any(max(inicio, ws) <= min(end_min, we) for ws, we in blocked_windows[d]):
                continue
            if any(abs(inicio - other_start) < min_gap_min for other_start in benign_by_device[d]):
                continue

            benign_by_device[d].append(inicio)
            generated_benign.append((d, inicio, dur_s, pico))

        if len(generated_benign) < count:
            raise RuntimeError(
                f"No se pudieron generar los {count} eventos benignos tras 10 000 intentos "
                f"(se generaron {len(generated_benign)})"
            )

        # Ordenar eventos benignos por (inicio, dispositivo)
        generated_benign.sort(key=lambda item: (item[1], item[0]))

        for d, inicio, dur_s, pico in generated_benign:
            events.append(
                Event(
                    scenario=scenario,
                    device_id=d,
                    start_min=float(inicio),
                    end_min=float(inicio + dur_s / 60.0),
                    params={"peak_c": pico, "duration_s": dur_s},
                )
            )

    # Orden final cronológico y por dispositivo
    events.sort(key=lambda ev: (ev.start_min, ev.device_id))
    return events


def effects_at(events: list[Event], device_id: str, t_min: float) -> ActiveEffects:
    """Calcula los efectos activos en el dispositivo device_id en el instante t_min."""
    door_open: DoorOpen | None = None
    door_left_open = False
    compressor_failure = False
    power_outage = False
    low_battery_start_min: float | None = None
    weak_signal = False
    sensor_drift = False
    node_offline = False

    for ev in events:
        if ev.device_id != device_id:
            continue
        is_active = (t_min >= ev.start_min) and (ev.end_min is None or t_min < ev.end_min)
        if not is_active:
            continue

        if ev.scenario == "door_open":
            duration_min = (ev.end_min - ev.start_min) if ev.end_min is not None else 2.5
            peak_c = float(ev.params.get("peak_c", 4.0))
            door_open = DoorOpen(
                start_min=ev.start_min,
                duration_min=duration_min,
                peak_c=peak_c,
            )
        elif ev.scenario == "door_left_open":
            door_left_open = True
        elif ev.scenario == "compressor_failure":
            compressor_failure = True
        elif ev.scenario == "power_outage":
            power_outage = True
        elif ev.scenario == "low_battery":
            low_battery_start_min = float(ev.start_min)
        elif ev.scenario == "weak_signal":
            weak_signal = True
        elif ev.scenario == "sensor_drift":
            sensor_drift = True
        elif ev.scenario == "node_offline":
            node_offline = True

    return ActiveEffects(
        door_open=door_open,
        door_left_open=door_left_open,
        compressor_failure=compressor_failure,
        power_outage=power_outage,
        low_battery_start_min=low_battery_start_min,
        weak_signal=weak_signal,
        sensor_drift=sensor_drift,
        node_offline=node_offline,
    )


def build_ground_truth(
    events: list[Event],
    anchor_ts: datetime,
    sim_end_ts: datetime,
    catalog: dict[str, CatalogEntry],
) -> list[dict[str, Any]]:
    """Construye la lista de eventos de verdad de referencia según la sección 7."""
    truth: list[dict[str, Any]] = []

    for ev in events:
        cat_entry = catalog[ev.scenario]
        start_dt = anchor_ts + timedelta(minutes=ev.start_min)

        if ev.end_min is not None:
            end_dt = anchor_ts + timedelta(minutes=ev.end_min)
            window_end_dt = end_dt + timedelta(minutes=cat_entry.margin_min)
        else:
            end_dt = sim_end_ts
            window_end_dt = sim_end_ts

        truth.append(
            {
                "scenario": ev.scenario,
                "device_id": ev.device_id,
                "class": cat_entry.scenario_class,
                "start_ts": format_ts(start_dt),
                "end_ts": format_ts(end_dt),
                "window_end_ts": format_ts(window_end_dt),
                "expected_rules": list(cat_entry.expected_rules),
                "forbidden_rules": list(cat_entry.forbidden_rules),
                "allowed_rules": list(cat_entry.allowed_rules),
            }
        )

    truth.sort(key=lambda item: (item["start_ts"], item["device_id"]))
    return truth
