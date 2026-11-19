"""Pruebas unitarias para la carga y validación de la configuración del simulador."""

import re
from pathlib import Path

import pytest
from simulator.config import (
    ConfigError,
    load_catalog,
    load_fleet,
    load_plan,
    load_thermal,
    validate_plan,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"
FLEET_PATH = CONFIG_DIR / "fleet.yaml"
THERMAL_PATH = CONFIG_DIR / "thermal_models.yaml"
CATALOG_PATH = CONFIG_DIR / "scenario_catalog.yaml"
SCENARIOS_DIR = CONFIG_DIR / "scenarios"
SEED_SQL_PATH = REPO_ROOT / "infra" / "timescaledb" / "init" / "003_seed.sql"


def test_load_all_repo_yaml_types():
    """Verifica que los 4 tipos de archivos YAML del repositorio carguen correctamente."""
    fleet = load_fleet(FLEET_PATH)
    assert fleet.period_s == 60
    assert fleet.firmware == "sim-1.0.0"
    assert fleet.phase_step_s == 5
    assert len(fleet.devices) == 12

    thermal = load_thermal(THERMAL_PATH)
    assert thermal.ambient.mean_c == 22.0
    assert thermal.ambient.daily_amplitude_c == 1.5
    assert thermal.ambient.peak_hour_utc == 15
    assert "REFRIGERATED" in thermal.classes
    assert "FROZEN" in thermal.classes
    assert "ULTRA_FROZEN" in thermal.classes
    assert thermal.classes["ULTRA_FROZEN"].humidity is None
    assert thermal.classes["REFRIGERATED"].humidity is not None

    catalog = load_catalog(CATALOG_PATH)
    assert "door_open" in catalog
    assert "door_left_open" in catalog
    assert "compressor_failure" in catalog
    assert "power_outage" in catalog
    assert catalog["door_open"].scenario_class == "benign"
    assert "REFRIGERATED" in catalog["door_left_open"].applicable_classes

    baseline = load_plan(SCENARIOS_DIR / "baseline.yaml")
    assert baseline.name == "baseline"
    assert baseline.duration_min == 1440
    assert len(baseline.faults) == 0

    demo = load_plan(SCENARIOS_DIR / "demo.yaml")
    assert demo.name == "demo"
    assert len(demo.faults) == 4
    assert len(demo.benign_specs) == 1

    val_24h = load_plan(SCENARIOS_DIR / "validation_24h.yaml")
    assert val_24h.name == "validation_24h"
    assert len(val_24h.faults) == 10
    assert len(val_24h.benign_specs) == 1


def test_validate_plan_unknown_scenario(tmp_path: Path):
    """Verifica que validate_plan lance ConfigError ante un escenario no presente en el catálogo."""
    fleet = load_fleet(FLEET_PATH)
    catalog = load_catalog(CATALOG_PATH)

    bad_plan_file = tmp_path / "bad_scenario.yaml"
    bad_plan_file.write_text(
        """version: 1
name: bad_scenario
seed: 1
duration_min: 100
faults:
  - {scenario: escenario_fantasma, device_id: SIM-HA-001, start_min: 10, duration_min: 20}
benign: []
""",
        encoding="utf-8",
    )
    plan = load_plan(bad_plan_file)
    with pytest.raises(ConfigError, match="Escenario desconocido"):
        validate_plan(plan, fleet, catalog)


def test_validate_plan_nonexistent_device(tmp_path: Path):
    """Verifica que validate_plan lance ConfigError ante un dispositivo no existente en la flota."""
    fleet = load_fleet(FLEET_PATH)
    catalog = load_catalog(CATALOG_PATH)

    bad_plan_file = tmp_path / "bad_device.yaml"
    bad_plan_file.write_text(
        """version: 1
name: bad_device
seed: 1
duration_min: 100
faults:
  - {scenario: door_left_open, device_id: SIM-INEXISTENTE, start_min: 10, duration_min: 20}
benign: []
""",
        encoding="utf-8",
    )
    plan = load_plan(bad_plan_file)
    with pytest.raises(ConfigError, match="Dispositivo inexistente"):
        validate_plan(plan, fleet, catalog)


def test_validate_plan_non_applicable_class(tmp_path: Path):
    """Verifica que validate_plan lance ConfigError ante una clase incompatible con el escenario."""
    fleet = load_fleet(FLEET_PATH)
    catalog = load_catalog(CATALOG_PATH)

    # SIM-LC-003 es ULTRA_FROZEN, y door_left_open solo aplica a [REFRIGERATED, FROZEN]
    bad_plan_file = tmp_path / "bad_class.yaml"
    bad_plan_file.write_text(
        """version: 1
name: bad_class
seed: 1
duration_min: 100
faults:
  - {scenario: door_left_open, device_id: SIM-LC-003, start_min: 10, duration_min: 20}
benign: []
""",
        encoding="utf-8",
    )
    plan = load_plan(bad_plan_file)
    with pytest.raises(ConfigError, match="no aplicable al escenario"):
        validate_plan(plan, fleet, catalog)


def test_validate_plan_start_min_out_of_range(tmp_path: Path):
    """Verifica que validate_plan lance ConfigError si start_min no está en [0, duration_min)."""
    fleet = load_fleet(FLEET_PATH)
    catalog = load_catalog(CATALOG_PATH)

    bad_plan_file = tmp_path / "bad_start.yaml"
    bad_plan_file.write_text(
        """version: 1
name: bad_start
seed: 1
duration_min: 100
faults:
  - {scenario: door_left_open, device_id: SIM-HA-001, start_min: 100, duration_min: 10}
benign: []
""",
        encoding="utf-8",
    )
    plan = load_plan(bad_plan_file)
    with pytest.raises(ConfigError, match="fuera del rango"):
        validate_plan(plan, fleet, catalog)


def test_malformed_yaml_files(tmp_path: Path):
    """Verifica que la carga de archivos YAML mal formados o corruptos lance ConfigError."""
    bad_syntax = tmp_path / "bad_syntax.yaml"
    bad_syntax.write_text("::invalid yaml::", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_fleet(bad_syntax)

    missing_fields = tmp_path / "missing_fields.yaml"
    missing_fields.write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_fleet(missing_fields)


def test_design_test_12_fleet_matches_seed_sql():
    """Prueba 12 del diseño: los 12 dispositivos de fleet.yaml coinciden con 003_seed.sql."""
    fleet = load_fleet(FLEET_PATH)
    sql_text = SEED_SQL_PATH.read_text(encoding="utf-8")

    pattern = re.compile(r"\('(SIM-[A-Z0-9-]+)',\s*'([a-z-]+)',\s*'[^']*',\s*'([A-Z_]+)'")
    sql_devices = pattern.findall(sql_text)

    assert len(sql_devices) == 12
    assert len(fleet.devices) == 12

    for (sql_id, sql_site, sql_class), dev in zip(sql_devices, fleet.devices, strict=True):
        assert dev.device_id == sql_id
        assert dev.site_id == sql_site
        assert dev.storage_class == sql_class


@pytest.mark.parametrize("scenario_file", ["baseline.yaml", "demo.yaml", "validation_24h.yaml"])
def test_repo_scenarios_pass_validation(scenario_file: str):
    """Verifica que todos los escenarios oficiales del repositorio superen validate_plan."""
    fleet = load_fleet(FLEET_PATH)
    catalog = load_catalog(CATALOG_PATH)
    plan = load_plan(SCENARIOS_DIR / scenario_file)

    # Debe ejecutarse sin lanzar excepciones
    validate_plan(plan, fleet, catalog)
