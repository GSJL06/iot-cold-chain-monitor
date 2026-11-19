"""Modelo térmico determinista y simulación de sensores para un dispositivo."""

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime

from simulator.config import ClassParams, ThermalCfg


@dataclass(frozen=True)
class DoorOpen:
    """Parámetros de una apertura de puerta activa."""

    start_min: float
    duration_min: float
    peak_c: float


@dataclass(frozen=True)
class ActiveEffects:
    """Efectos de escenarios activos en un instante de muestreo."""

    door_open: DoorOpen | None = None
    door_left_open: bool = False
    compressor_failure: bool = False
    power_outage: bool = False
    low_battery_start_min: float | None = None
    weak_signal: bool = False
    sensor_drift: bool = False
    node_offline: bool = False


@dataclass(frozen=True)
class Sample:
    """Muestra de telemetría calculada en un tick del dispositivo."""

    temp_c: float
    humidity_pct: float | None
    rssi_dbm: int
    battery_pct: float
    on_backup_power: bool
    silent: bool


class ThermalDevice:
    """Simulador térmico determinista para un dispositivo físico de frío."""

    def __init__(
        self,
        device_id: str,
        storage_class: str,
        seed: int | str,
        thermal: ThermalCfg,
        period_s: int = 60,
    ) -> None:
        """Inicializa el dispositivo siguiendo el orden exacto de sorteos de la sección 3."""
        self.device_id = device_id
        self.storage_class = storage_class
        self.seed = seed
        self._thermal = thermal
        self.period_s = period_s

        if storage_class not in thermal.classes:
            raise ValueError(f"Clase de almacenamiento desconocida: {storage_class}")
        self._class_params: ClassParams = thermal.classes[storage_class]

        # Generador pseudoaleatorio propio por dispositivo (semilla determinista)
        self._rng = random.Random(f"{seed}:{device_id}")

        # Sorteos iniciales en orden estricto (sección 3):
        # 1. Temperatura inicial: uniform(banda)
        band_low, band_high = self._class_params.band_c
        self._temp: float = self._rng.uniform(band_low, band_high)

        # 2. Estado del compresor: random() < 0.5
        self._comp: bool = self._rng.random() < 0.5

        # 3. Media de RSSI de línea base: uniform(rango)
        rssi_range = self._thermal.rssi.baseline_range_dbm
        self._baseline_rssi_mean: float = self._rng.uniform(rssi_range[0], rssi_range[1])

        # Estado físico interno no sorteado
        self._bump: float = 0.0
        self._drift: float = 0.0
        self._battery: float = 100.0
        self._last_low_battery_start_min: float | None = None

        if self._class_params.humidity is not None:
            self._humidity: float | None = self._class_params.humidity.mean_pct
        else:
            self._humidity = None

    def step(
        self,
        ts: datetime,
        t_min: float,
        effects: ActiveEffects | None = None,
    ) -> Sample:
        """Avanza el modelo térmico un tick (dt = period_s / 60 min) y genera una muestra."""
        if effects is None:
            effects = ActiveEffects()

        dt = self.period_s / 60.0

        # 1. Hora UTC con fracción y temperatura ambiente exterior (sección 4.1)
        ts_utc = ts.astimezone(UTC) if ts.tzinfo is not None else ts
        hora = (
            ts_utc.hour
            + ts_utc.minute / 60.0
            + ts_utc.second / 3600.0
            + ts_utc.microsecond / 3_600_000_000.0
        )
        amb = self._thermal.ambient.mean_c + self._thermal.ambient.daily_amplitude_c * math.sin(
            2.0 * math.pi * (hora - (self._thermal.ambient.peak_hour_utc - 6.0)) / 24.0
        )

        # 2. Controlador del compresor con histéresis (sección 4.1)
        cooling_lost = self._thermal.scenarios.power_outage.cooling_lost
        compressor_enabled = (
            not (effects.power_outage and cooling_lost) and not effects.compressor_failure
        )

        band_low, band_high = self._class_params.band_c
        if compressor_enabled:
            if self._comp and self._temp <= band_low:
                self._comp = False
            elif not self._comp and self._temp >= band_high:
                self._comp = True
        else:
            self._comp = False

        # 3. Evolución de la temperatura del producto (sección 4.1)
        leak = self._class_params.leak_per_min * (amb - self._temp)
        cool = self._class_params.cool_c_per_min if self._comp else 0.0
        door_gain = (
            self._class_params.door_left_open_gain_c_per_min if effects.door_left_open else 0.0
        )
        dT_per_min = leak - cool + door_gain
        self._temp += dT_per_min * dt

        # 4. Bulto de aire por apertura de puerta corta (sección 4.1)
        if effects.door_open is not None:
            door = effects.door_open
            fraction = (
                (t_min - door.start_min + dt) / door.duration_min if door.duration_min > 0 else 1.0
            )
            self._bump = door.peak_c * min(1.0, max(0.0, fraction))
        else:
            decay_tau = self._thermal.door_open.decay_tau_min
            self._bump = self._bump * math.exp(-dt / decay_tau)

        # 5. Deriva del sensor de temperatura (sección 4.1)
        if effects.sensor_drift:
            self._drift += self._thermal.scenarios.sensor_drift.rate_c_per_min * dt

        # 6. Estado de batería y alimentación de respaldo (sección 4.2)
        if effects.power_outage:
            drain = self._thermal.scenarios.power_outage.battery_drain_pct_per_min
            self._battery = max(0.0, self._battery - drain * dt)
            on_backup_power = True
        elif effects.low_battery_start_min is not None:
            on_backup_power = False
            if self._last_low_battery_start_min != effects.low_battery_start_min:
                self._last_low_battery_start_min = effects.low_battery_start_min
                self._battery = self._thermal.scenarios.low_battery.start_pct
            else:
                drain = self._thermal.scenarios.low_battery.battery_drain_pct_per_min
                self._battery = max(0.0, self._battery - drain * dt)
        else:
            self._last_low_battery_start_min = None
            on_backup_power = False
            recharge = self._thermal.battery.recharge_pct_per_min
            self._battery = min(100.0, self._battery + recharge * dt)

        # 7. Sorteos aleatorios por tick en orden estricto (sección 3):
        # Primero: ruido de temperatura gauss(0, noise_sigma_c)
        temp_noise = self._rng.gauss(0.0, self._class_params.noise_sigma_c)

        # Segundo: paso de humedad gauss(0, step_sigma) si la clase mide humedad
        if self._class_params.humidity is not None:
            hum_noise = self._rng.gauss(0.0, self._class_params.humidity.step_sigma)
        else:
            hum_noise = None

        # Tercero: ruido de RSSI gauss (media, sigma_dbm)
        if effects.weak_signal:
            rssi_media = self._thermal.scenarios.weak_signal.mean_dbm
            rssi_sigma = self._thermal.scenarios.weak_signal.sigma_dbm
        else:
            rssi_media = self._baseline_rssi_mean
            rssi_sigma = self._thermal.rssi.sigma_dbm
        rssi_raw = self._rng.gauss(rssi_media, rssi_sigma)

        # 8. Cálculo de los valores muestreados
        temp_c = round(self._temp + self._bump + self._drift + temp_noise, 1)

        if self._class_params.humidity is not None and hum_noise is not None:
            hum_cfg = self._class_params.humidity
            hum_prev = self._humidity if self._humidity is not None else hum_cfg.mean_pct
            self._humidity = max(
                hum_cfg.min_pct,
                min(hum_cfg.max_pct, hum_prev + 0.1 * (hum_cfg.mean_pct - hum_prev) + hum_noise),
            )
            humidity_pct: float | None = round(self._humidity, 1)
        else:
            humidity_pct = None

        rssi_dbm = max(-140, min(0, int(round(rssi_raw))))
        battery_pct = round(self._battery, 1)
        silent = bool(effects.node_offline)

        return Sample(
            temp_c=temp_c,
            humidity_pct=humidity_pct,
            rssi_dbm=rssi_dbm,
            battery_pct=battery_pct,
            on_backup_power=on_backup_power,
            silent=silent,
        )
