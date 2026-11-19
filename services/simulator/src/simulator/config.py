"""Carga y validación de la configuración del simulador y catálogo de escenarios."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Excepción para errores de sintaxis, esquema o coherencia en la configuración."""


@dataclass(frozen=True)
class DeviceCfg:
    """Configuración de un dispositivo de la flota."""

    device_id: str
    site_id: str
    storage_class: str


@dataclass(frozen=True)
class FleetCfg:
    """Configuración global de la flota de dispositivos."""

    period_s: int
    firmware: str
    phase_step_s: int
    devices: tuple[DeviceCfg, ...]


@dataclass(frozen=True)
class HumidityParams:
    """Parámetros del modelo autorregresivo de humedad."""

    mean_pct: float
    step_sigma: float
    min_pct: float
    max_pct: float


@dataclass(frozen=True)
class ClassParams:
    """Parámetros térmicos específicos de una clase de almacenamiento."""

    band_c: tuple[float, float]
    cool_c_per_min: float
    leak_per_min: float
    noise_sigma_c: float
    door_left_open_gain_c_per_min: float
    humidity: HumidityParams | None = None


@dataclass(frozen=True)
class AmbientCfg:
    """Parámetros de la temperatura ambiente exterior."""

    mean_c: float
    daily_amplitude_c: float
    peak_hour_utc: int


@dataclass(frozen=True)
class DoorOpenCfg:
    """Parámetros de bulto de aire por apertura de puerta corta."""

    decay_tau_min: float


@dataclass(frozen=True)
class BatteryCfg:
    """Parámetros de recarga de batería en operación normal."""

    recharge_pct_per_min: float


@dataclass(frozen=True)
class RssiCfg:
    """Parámetros de señal de radio en línea base."""

    baseline_range_dbm: tuple[float, float]
    sigma_dbm: float


@dataclass(frozen=True)
class PowerOutageCfg:
    """Parámetros del escenario de corte de energía."""

    battery_drain_pct_per_min: float
    cooling_lost: bool


@dataclass(frozen=True)
class LowBatteryCfg:
    """Parámetros del escenario de batería baja."""

    start_pct: float
    battery_drain_pct_per_min: float


@dataclass(frozen=True)
class WeakSignalCfg:
    """Parámetros del escenario de señal degradada."""

    mean_dbm: float
    sigma_dbm: float


@dataclass(frozen=True)
class SensorDriftCfg:
    """Parámetros del escenario de deriva gradual del sensor."""

    rate_c_per_min: float


@dataclass(frozen=True)
class ThermalScenariosCfg:
    """Parámetros agrupados para escenarios de fallo térmicos y de hardware."""

    power_outage: PowerOutageCfg
    low_battery: LowBatteryCfg
    weak_signal: WeakSignalCfg
    sensor_drift: SensorDriftCfg

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


@dataclass(frozen=True)
class ThermalCfg:
    """Configuración completa de modelos térmicos y escenarios."""

    ambient: AmbientCfg
    door_open: DoorOpenCfg
    battery: BatteryCfg
    rssi: RssiCfg
    classes: dict[str, ClassParams]
    scenarios: ThermalScenariosCfg


@dataclass(frozen=True)
class CatalogEntry:
    """Entrada del catálogo de escenarios y reglas esperadas/prohibidas."""

    scenario_class: str
    expected_rules: tuple[str, ...] = ()
    forbidden_rules: tuple[str, ...] = ()
    allowed_rules: tuple[str, ...] = ()
    margin_min: int = 0
    applicable_classes: tuple[str, ...] = ()

    @property
    def class_(self) -> str:
        """Alias para el atributo 'class' del YAML."""
        return self.scenario_class

    @property
    def category(self) -> str:
        """Alias de clasificación de escenario."""
        return self.scenario_class


@dataclass(frozen=True)
class ScenarioEvent:
    """Evento programado dentro de un plan de simulación."""

    scenario: str
    device_id: str
    start_min: int
    duration_min: int | None = None


@dataclass(frozen=True)
class ScenarioPlan:
    """Plan de simulación con semilla, duración y lista de eventos."""

    name: str
    seed: int
    duration_min: int
    faults: tuple[ScenarioEvent, ...]
    benign_specs: tuple[dict[str, Any], ...] = ()


def _read_yaml(path: str | Path) -> dict[str, Any]:
    """Lee y parsea un archivo YAML verificando su existencia y formato."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"Archivo no encontrado: {path}")
    try:
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML inválido en {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Error de E/S al leer {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Estructura inválida en {path}: se esperaba un diccionario raíz")
    return data


def load_fleet(path: str | Path) -> FleetCfg:
    """Carga la configuración de la flota desde fleet.yaml."""
    raw = _read_yaml(path)
    try:
        period_s = int(raw["period_s"])
        firmware = str(raw["firmware"])
        phase_step_s = int(raw["phase_step_s"])
        devices_raw = raw["devices"]
        if not isinstance(devices_raw, list):
            raise ConfigError(f"Campo 'devices' no es una lista en {path}")
        devices = tuple(
            DeviceCfg(
                device_id=str(d["device_id"]),
                site_id=str(d["site_id"]),
                storage_class=str(d["storage_class"]),
            )
            for d in devices_raw
        )
        return FleetCfg(
            period_s=period_s,
            firmware=firmware,
            phase_step_s=phase_step_s,
            devices=devices,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"Archivo de flota mal formado {path}: {exc}") from exc


def load_thermal(path: str | Path) -> ThermalCfg:
    """Carga los parámetros térmicos y ambientales desde thermal_models.yaml."""
    raw = _read_yaml(path)
    try:
        amb_raw = raw["ambient"]
        ambient = AmbientCfg(
            mean_c=float(amb_raw["mean_c"]),
            daily_amplitude_c=float(amb_raw["daily_amplitude_c"]),
            peak_hour_utc=int(amb_raw["peak_hour_utc"]),
        )
        door_open = DoorOpenCfg(
            decay_tau_min=float(raw["door_open"]["decay_tau_min"]),
        )
        battery = BatteryCfg(
            recharge_pct_per_min=float(raw["battery"]["recharge_pct_per_min"]),
        )
        rssi_raw = raw["rssi"]
        rssi = RssiCfg(
            baseline_range_dbm=(
                float(rssi_raw["baseline_range_dbm"][0]),
                float(rssi_raw["baseline_range_dbm"][1]),
            ),
            sigma_dbm=float(rssi_raw["sigma_dbm"]),
        )
        classes: dict[str, ClassParams] = {}
        for class_name, cdata in raw["classes"].items():
            hum = None
            if cdata.get("humidity") is not None:
                hdata = cdata["humidity"]
                hum = HumidityParams(
                    mean_pct=float(hdata["mean_pct"]),
                    step_sigma=float(hdata["step_sigma"]),
                    min_pct=float(hdata["min_pct"]),
                    max_pct=float(hdata["max_pct"]),
                )
            classes[str(class_name)] = ClassParams(
                band_c=(float(cdata["band_c"][0]), float(cdata["band_c"][1])),
                cool_c_per_min=float(cdata["cool_c_per_min"]),
                leak_per_min=float(cdata["leak_per_min"]),
                noise_sigma_c=float(cdata["noise_sigma_c"]),
                door_left_open_gain_c_per_min=float(cdata["door_left_open_gain_c_per_min"]),
                humidity=hum,
            )
        sc_raw = raw["scenarios"]
        scenarios = ThermalScenariosCfg(
            power_outage=PowerOutageCfg(
                battery_drain_pct_per_min=float(
                    sc_raw["power_outage"]["battery_drain_pct_per_min"]
                ),
                cooling_lost=bool(sc_raw["power_outage"]["cooling_lost"]),
            ),
            low_battery=LowBatteryCfg(
                start_pct=float(sc_raw["low_battery"]["start_pct"]),
                battery_drain_pct_per_min=float(sc_raw["low_battery"]["battery_drain_pct_per_min"]),
            ),
            weak_signal=WeakSignalCfg(
                mean_dbm=float(sc_raw["weak_signal"]["mean_dbm"]),
                sigma_dbm=float(sc_raw["weak_signal"]["sigma_dbm"]),
            ),
            sensor_drift=SensorDriftCfg(
                rate_c_per_min=float(sc_raw["sensor_drift"]["rate_c_per_min"]),
            ),
        )
        return ThermalCfg(
            ambient=ambient,
            door_open=door_open,
            battery=battery,
            rssi=rssi,
            classes=classes,
            scenarios=scenarios,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"Archivo de modelos térmicos mal formado {path}: {exc}") from exc


def load_catalog(path: str | Path) -> dict[str, CatalogEntry]:
    """Carga el catálogo de escenarios desde scenario_catalog.yaml."""
    raw = _read_yaml(path)
    try:
        scenarios_raw = raw["scenarios"]
        if not isinstance(scenarios_raw, dict):
            raise ConfigError(f"Campo 'scenarios' no es un diccionario en {path}")
        catalog: dict[str, CatalogEntry] = {}
        for sc_name, sc_data in scenarios_raw.items():
            catalog[str(sc_name)] = CatalogEntry(
                scenario_class=str(sc_data["class"]),
                expected_rules=tuple(str(r) for r in sc_data.get("expected_rules", ())),
                forbidden_rules=tuple(str(r) for r in sc_data.get("forbidden_rules", ())),
                allowed_rules=tuple(str(r) for r in sc_data.get("allowed_rules", ())),
                margin_min=int(sc_data.get("margin_min", 0)),
                applicable_classes=tuple(str(c) for c in sc_data.get("applicable_classes", ())),
            )
        return catalog
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"Archivo de catálogo mal formado {path}: {exc}") from exc


def load_plan(path: str | Path) -> ScenarioPlan:
    """Carga un plan de simulación desde un archivo YAML de escenarios."""
    raw = _read_yaml(path)
    try:
        name = str(raw["name"])
        seed = int(raw["seed"])
        duration_min = int(raw["duration_min"])
        faults_raw = raw.get("faults", [])
        if not isinstance(faults_raw, list):
            raise ConfigError(f"Campo 'faults' no es una lista en {path}")
        faults = tuple(
            ScenarioEvent(
                scenario=str(f["scenario"]),
                device_id=str(f["device_id"]),
                start_min=int(f["start_min"]),
                duration_min=int(f["duration_min"])
                if "duration_min" in f and f["duration_min"] is not None
                else None,
            )
            for f in faults_raw
        )
        benign_raw = raw.get("benign", [])
        if not isinstance(benign_raw, list):
            raise ConfigError(f"Campo 'benign' no es una lista en {path}")
        benign_specs = tuple(dict(b) for b in benign_raw)
        return ScenarioPlan(
            name=name,
            seed=seed,
            duration_min=duration_min,
            faults=faults,
            benign_specs=benign_specs,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"Archivo de plan mal formado {path}: {exc}") from exc


def validate_plan(
    plan: ScenarioPlan,
    fleet: FleetCfg,
    catalog: dict[str, CatalogEntry],
) -> None:
    """Valida un plan de simulación contra la flota y el catálogo de escenarios.

    Lanza ConfigError si hay:
    - un escenario desconocido,
    - un device_id inexistente,
    - una clase de almacenamiento no aplicable según applicable_classes,
    - un start_min fuera del rango [0, duration_min),
    - o datos mal formados.
    """
    if plan.duration_min <= 0:
        raise ConfigError(f"duration_min debe ser > 0, recibido: {plan.duration_min}")

    fleet_devices = {d.device_id: d for d in fleet.devices}

    for fault in plan.faults:
        if fault.scenario not in catalog:
            raise ConfigError(f"Escenario desconocido en plan: '{fault.scenario}'")

        if fault.device_id not in fleet_devices:
            raise ConfigError(f"Dispositivo inexistente en la flota: '{fault.device_id}'")

        if not (0 <= fault.start_min < plan.duration_min):
            raise ConfigError(
                f"start_min={fault.start_min} fuera del rango [0, {plan.duration_min})"
            )

        if fault.duration_min is not None and fault.duration_min <= 0:
            raise ConfigError(
                f"duration_min debe ser > 0 si se especifica, recibido: {fault.duration_min}"
            )

        device = fleet_devices[fault.device_id]
        cat_entry = catalog[fault.scenario]
        if device.storage_class not in cat_entry.applicable_classes:
            raise ConfigError(
                f"Clase '{device.storage_class}' del dispositivo '{fault.device_id}' "
                f"no aplicable al escenario '{fault.scenario}' "
                f"(permitidas: {cat_entry.applicable_classes})"
            )

    for spec in plan.benign_specs:
        scenario = spec.get("scenario")
        if scenario is not None and scenario not in catalog:
            raise ConfigError(f"Escenario benigno desconocido: '{scenario}'")
