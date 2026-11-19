"""Pruebas unitarias para la flota simulada, reproducibilidad, telemetría y contrato."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema
from simulator.cli import main
from simulator.config import load_catalog, load_fleet, load_plan, load_thermal
from simulator.fleet import Fleet

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"
FLEET_PATH = CONFIG_DIR / "fleet.yaml"
THERMAL_PATH = CONFIG_DIR / "thermal_models.yaml"
CATALOG_PATH = CONFIG_DIR / "scenario_catalog.yaml"
PLAN_24H_PATH = CONFIG_DIR / "scenarios" / "validation_24h.yaml"
SCHEMA_PATH = REPO_ROOT / "schemas" / "telemetry.v1.schema.json"

FIXED_START_TS = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def test_design_test_1_reproducibility(tmp_path: Path):
    """Prueba 1: Misma semilla = mismo SHA-256; distinta semilla = distinto SHA-256."""
    out1 = tmp_path / "out1.jsonl"
    out2 = tmp_path / "out2.jsonl"
    out3 = tmp_path / "out3.jsonl"

    ret1 = main(
        [
            "--config-dir",
            str(CONFIG_DIR),
            "generate",
            "--scenario",
            str(PLAN_24H_PATH),
            "--seed",
            "42",
            "--out",
            str(out1),
        ]
    )
    assert ret1 == 0

    ret2 = main(
        [
            "--config-dir",
            str(CONFIG_DIR),
            "generate",
            "--scenario",
            str(PLAN_24H_PATH),
            "--seed",
            "42",
            "--out",
            str(out2),
        ]
    )
    assert ret2 == 0

    ret3 = main(
        [
            "--config-dir",
            str(CONFIG_DIR),
            "generate",
            "--scenario",
            str(PLAN_24H_PATH),
            "--seed",
            "999",
            "--out",
            str(out3),
        ]
    )
    assert ret3 == 0

    sha1 = hashlib.sha256(out1.read_bytes()).hexdigest()
    sha2 = hashlib.sha256(out2.read_bytes()).hexdigest()
    sha3 = hashlib.sha256(out3.read_bytes()).hexdigest()

    assert sha1 == sha2, "Misma semilla debe producir exactamente el mismo SHA-256"
    assert sha1 != sha3, "Semillas diferentes deben producir SHA-256 distintos"


def test_design_test_6_node_offline():
    """Prueba 6: node_offline sin lecturas durante el evento y seq continuo al reanudar."""
    fleet_cfg = load_fleet(FLEET_PATH)
    thermal_cfg = load_thermal(THERMAL_PATH)
    catalog = load_catalog(CATALOG_PATH)
    plan = load_plan(PLAN_24H_PATH)

    fleet = Fleet(
        fleet_cfg=fleet_cfg,
        thermal_cfg=thermal_cfg,
        catalog=catalog,
        plan=plan,
        start_ts=FIXED_START_TS,
    )

    # SIM-HA-003 tiene node_offline entre min 600 y 625 (duración 25 min)
    ha003_readings = [
        r for r in fleet.generate(duration_min=plan.duration_min) if r.device_id == "SIM-HA-003"
    ]

    offline_start_ts = FIXED_START_TS + timedelta(minutes=600)
    offline_end_ts = FIXED_START_TS + timedelta(minutes=625)

    during_offline = [r for r in ha003_readings if offline_start_ts <= r.ts < offline_end_ts]
    msg_offline = f"Se emitieron {len(during_offline)} lecturas durante node_offline"
    assert len(during_offline) == 0, msg_offline

    before_offline = [r for r in ha003_readings if r.ts < offline_start_ts]
    after_offline = [r for r in ha003_readings if r.ts >= offline_end_ts]

    assert len(before_offline) > 0
    assert len(after_offline) > 0

    last_before_seq = before_offline[-1].seq
    first_after_seq = after_offline[0].seq

    msg_seq = f"seq no es contiguo: antes={last_before_seq}, después={first_after_seq}"
    assert first_after_seq == last_before_seq + 1, msg_seq


def test_design_test_11_contract_schema_validation():
    """Prueba 11: El 100% de las lecturas de validation_24h validan contra telemetry.v1."""
    fleet_cfg = load_fleet(FLEET_PATH)
    thermal_cfg = load_thermal(THERMAL_PATH)
    catalog = load_catalog(CATALOG_PATH)
    plan = load_plan(PLAN_24H_PATH)

    schema_data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema_data)

    fleet = Fleet(
        fleet_cfg=fleet_cfg,
        thermal_cfg=thermal_cfg,
        catalog=catalog,
        plan=plan,
        start_ts=FIXED_START_TS,
    )

    validated_count = 0
    for reading in fleet.generate(duration_min=plan.duration_min):
        payload = reading.to_payload()
        validator.validate(payload)
        validated_count += 1

    assert validated_count == 17225, f"Se validaron {validated_count} lecturas (esperadas 17225)"


def test_reading_count_validation_24h():
    """Verifica que validation_24h produzca exactamente 12 * 1440 - 55 = 17225 lecturas."""
    fleet_cfg = load_fleet(FLEET_PATH)
    thermal_cfg = load_thermal(THERMAL_PATH)
    catalog = load_catalog(CATALOG_PATH)
    plan = load_plan(PLAN_24H_PATH)

    fleet = Fleet(
        fleet_cfg=fleet_cfg,
        thermal_cfg=thermal_cfg,
        catalog=catalog,
        plan=plan,
        start_ts=FIXED_START_TS,
    )

    all_readings = list(fleet.generate(duration_min=plan.duration_min))
    expected_readings = 12 * 1440 - 55  # 55 ticks silenciados por node_offline

    assert len(all_readings) == expected_readings == 17225
