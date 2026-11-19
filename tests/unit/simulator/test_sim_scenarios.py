"""Pruebas unitarias para la expansión de planes, efectos activos y verdad de referencia."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from simulator.config import load_catalog, load_fleet, load_plan
from simulator.scenarios import Event, build_ground_truth, effects_at, expand_plan

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"
FLEET_PATH = CONFIG_DIR / "fleet.yaml"
CATALOG_PATH = CONFIG_DIR / "scenario_catalog.yaml"
PLAN_24H_PATH = CONFIG_DIR / "scenarios" / "validation_24h.yaml"

FIXED_START_TS = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def test_design_test_10_plan_24h_invariants():
    """Prueba 10: Invariantes del plan de validación de 24 h (sección 10).

    - 10 eventos real_fault y 30 door_open en ground_truth.json.
    - Ningún benigno dentro de la ventana bloqueada de un fallo del mismo dispositivo.
    - Ningún benigno a menos de 45 min de otro benigno del mismo dispositivo.
    """
    fleet = load_fleet(FLEET_PATH)
    catalog = load_catalog(CATALOG_PATH)
    plan = load_plan(PLAN_24H_PATH)

    events = expand_plan(plan, fleet, catalog)
    sim_end_ts = FIXED_START_TS + timedelta(minutes=plan.duration_min)
    truth = build_ground_truth(events, FIXED_START_TS, sim_end_ts, catalog)

    # 1. Conteo de eventos
    real_faults = [e for e in truth if e["class"] == "real_fault"]
    door_opens = [e for e in truth if e["scenario"] == "door_open"]

    assert len(real_faults) == 10, f"Se esperaban 10 fallos reales, obtenidos: {len(real_faults)}"
    assert (
        len(door_opens) == 30
    ), f"Se esperaban 30 aperturas benignas, obtenidas: {len(door_opens)}"

    # 2. Ventanas bloqueadas por dispositivo: [inicio - 10, fin + margin_min]
    fault_events = [e for e in events if catalog[e.scenario].scenario_class == "real_fault"]
    benign_events = [e for e in events if e.scenario == "door_open"]

    blocked_by_dev: dict[str, list[tuple[float, float]]] = {d.device_id: [] for d in fleet.devices}
    for fe in fault_events:
        margin = catalog[fe.scenario].margin_min
        fin = fe.end_min if fe.end_min is not None else float(plan.duration_min)
        blocked_by_dev[fe.device_id].append((fe.start_min - 10.0, fin + margin))

    for be in benign_events:
        d = be.device_id
        b_start = be.start_min
        b_end = be.end_min if be.end_min is not None else b_start + 2.0
        # No debe solaparse con ninguna ventana bloqueada del mismo dispositivo
        for w_start, w_end in blocked_by_dev[d]:
            overlap = max(b_start, w_start) <= min(b_end, w_end)
            assert not overlap, (
                f"Dispositivo {d}: benigno [{b_start}, {b_end}] se solapa "
                f"con ventana de fallo [{w_start}, {w_end}]"
            )

    # 3. Separación mínima de 45 min entre benignos del mismo dispositivo
    benigns_by_dev: dict[str, list[float]] = {d.device_id: [] for d in fleet.devices}
    for be in benign_events:
        benigns_by_dev[be.device_id].append(be.start_min)

    for dev_id, starts in benigns_by_dev.items():
        sorted_starts = sorted(starts)
        for i in range(1, len(sorted_starts)):
            gap = sorted_starts[i] - sorted_starts[i - 1]
            assert gap >= 45.0, (
                f"Dispositivo {dev_id}: intervalo entre benignos {gap} min "
                f"es menor al mínimo de 45 min"
            )


def test_effects_at_activation_windows():
    """Verifica que effects_at active y desactive efectos según los tiempos del evento."""
    events = [
        Event(
            scenario="door_open",
            device_id="SIM-HA-001",
            start_min=60.0,
            end_min=62.5,
            params={"peak_c": 3.8},
        ),
        Event(
            scenario="compressor_failure",
            device_id="SIM-HA-001",
            start_min=120.0,
            end_min=None,
            params={},
        ),
        Event(
            scenario="power_outage",
            device_id="SIM-FB-001",
            start_min=200.0,
            end_min=260.0,
            params={},
        ),
    ]

    # Antes del inicio
    eff_pre = effects_at(events, "SIM-HA-001", t_min=59.9)
    assert eff_pre.door_open is None
    assert eff_pre.compressor_failure is False

    # Durante door_open
    eff_during = effects_at(events, "SIM-HA-001", t_min=60.5)
    assert eff_during.door_open is not None
    assert eff_during.door_open.start_min == 60.0
    assert eff_during.door_open.duration_min == 2.5
    assert eff_during.door_open.peak_c == 3.8
    assert eff_during.compressor_failure is False

    # Después de door_open y antes de compressor_failure
    eff_post = effects_at(events, "SIM-HA-001", t_min=62.5)
    assert eff_post.door_open is None
    assert eff_post.compressor_failure is False

    # Durante compressor_failure (abierto)
    eff_cf = effects_at(events, "SIM-HA-001", t_min=150.0)
    assert eff_cf.compressor_failure is True
    eff_cf_far = effects_at(events, "SIM-HA-001", t_min=1400.0)
    assert eff_cf_far.compressor_failure is True

    # Otro dispositivo no se ve afectado
    eff_other = effects_at(events, "SIM-FB-001", t_min=61.0)
    assert eff_other.door_open is None
    eff_power = effects_at(events, "SIM-FB-001", t_min=205.0)
    assert eff_power.power_outage is True


def test_build_ground_truth_fields_and_order():
    """Verifica estructura, cálculo de ventanas y orden en build_ground_truth."""
    catalog = load_catalog(CATALOG_PATH)
    events = [
        Event(
            scenario="door_left_open",
            device_id="SIM-HA-002",
            start_min=300.0,
            end_min=318.0,
            params={},
        ),
        Event(
            scenario="compressor_failure",
            device_id="SIM-HA-001",
            start_min=420.0,
            end_min=None,
            params={},
        ),
    ]

    anchor = datetime(2026, 11, 18, 0, 0, 0, tzinfo=UTC)
    sim_end = datetime(2026, 11, 19, 0, 0, 0, tzinfo=UTC)
    truth = build_ground_truth(events, anchor, sim_end, catalog)

    assert len(truth) == 2
    ev0 = truth[0]
    assert ev0["scenario"] == "door_left_open"
    assert ev0["device_id"] == "SIM-HA-002"
    assert ev0["class"] == "real_fault"
    assert ev0["start_ts"] == "2026-11-18T05:00:00Z"
    assert ev0["end_ts"] == "2026-11-18T05:18:00Z"
    # door_left_open tiene margin_min = 90 (05:18 + 01:30 = 06:48)
    assert ev0["window_end_ts"] == "2026-11-18T06:48:00Z"
    assert ev0["expected_rules"] == ["R01"]
    assert ev0["forbidden_rules"] == []
    assert ev0["allowed_rules"] == ["R02"]

    ev1 = truth[1]
    assert ev1["scenario"] == "compressor_failure"
    assert ev1["start_ts"] == "2026-11-18T07:00:00Z"
    assert ev1["end_ts"] == "2026-11-19T00:00:00Z"
    assert ev1["window_end_ts"] == "2026-11-19T00:00:00Z"
    assert ev1["expected_rules"] == ["R01"]
    assert ev1["forbidden_rules"] == ["R03"]
