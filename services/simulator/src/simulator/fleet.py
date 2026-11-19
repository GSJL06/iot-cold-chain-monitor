"""Flota de dispositivos simulados y generación coordinada de telemetría."""

import heapq
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from simulator.clock import format_ts, tick_times
from simulator.config import CatalogEntry, DeviceCfg, FleetCfg, ScenarioPlan, ThermalCfg
from simulator.model import ActiveEffects, Sample, ThermalDevice
from simulator.scenarios import build_ground_truth, effects_at, expand_plan


@dataclass(frozen=True)
class Reading:
    """Lectura individual de telemetría emitida por un dispositivo de la flota."""

    site_id: str
    device_id: str
    ts: datetime
    seq: int
    sample: Sample
    firmware: str

    def to_payload(self) -> dict[str, Any]:
        """Convierte la lectura al payload JSON del contrato telemetry.v1."""
        payload: dict[str, Any] = {
            "schema": "telemetry.v1",
            "device_id": self.device_id,
            "ts": format_ts(self.ts),
            "seq": self.seq,
            "temp_c": self.sample.temp_c,
        }
        if self.sample.humidity_pct is not None:
            payload["humidity_pct"] = self.sample.humidity_pct
        payload["rssi_dbm"] = self.sample.rssi_dbm
        payload["battery_pct"] = self.sample.battery_pct
        payload["on_backup_power"] = self.sample.on_backup_power
        payload["fw"] = self.firmware
        return payload


class Fleet:
    """Conjunto de dispositivos de frío simulados y generación coordinada de lecturas."""

    def __init__(
        self,
        fleet_cfg: FleetCfg,
        thermal_cfg: ThermalCfg,
        catalog: dict[str, CatalogEntry],
        plan: ScenarioPlan,
        start_ts: datetime,
        backfill_min: int = 0,
        seed: int | None = None,
    ) -> None:
        """Inicializa la flota con sus dispositivos y prepara el calendario de eventos."""
        self._fleet_cfg = fleet_cfg
        self._thermal_cfg = thermal_cfg
        self._catalog = catalog
        self._plan = plan
        self._start_ts = (
            start_ts.astimezone(UTC)
            if start_ts.tzinfo is not None
            else start_ts.replace(tzinfo=UTC)
        )
        self._backfill_min = backfill_min
        self._seed = seed if seed is not None else plan.seed
        self._anchor_ts = self._start_ts + timedelta(minutes=backfill_min)

        effective_plan = plan
        if seed is not None and seed != plan.seed:
            effective_plan = ScenarioPlan(
                name=plan.name,
                seed=seed,
                duration_min=plan.duration_min,
                faults=plan.faults,
                benign_specs=plan.benign_specs,
            )
        self._events = expand_plan(effective_plan, fleet_cfg, catalog)

    def generate(self, duration_min: int | None = None) -> Iterator[Reading]:
        """Genera las lecturas de todos los dispositivos en orden de (ts, índice de dispositivo)."""
        count = (
            int(duration_min * 60 // self._fleet_cfg.period_s) if duration_min is not None else None
        )

        def _device_stream(
            dev_idx: int,
            dev_cfg: DeviceCfg,
        ) -> Iterator[tuple[datetime, int, Reading]]:
            phase_s = dev_idx * self._fleet_cfg.phase_step_s
            device = ThermalDevice(
                device_id=dev_cfg.device_id,
                storage_class=dev_cfg.storage_class,
                seed=self._seed,
                thermal=self._thermal_cfg,
                period_s=self._fleet_cfg.period_s,
            )
            seq = 0
            for ts in tick_times(
                start=self._start_ts,
                period_s=self._fleet_cfg.period_s,
                phase_s=phase_s,
                count=count,
            ):
                t_min = (ts - self._anchor_ts).total_seconds() / 60.0
                if 0 <= t_min < self._plan.duration_min:
                    effects = effects_at(self._events, dev_cfg.device_id, t_min)
                else:
                    effects = ActiveEffects()

                sample = device.step(ts=ts, t_min=t_min, effects=effects)
                if sample.silent:
                    continue

                reading = Reading(
                    site_id=dev_cfg.site_id,
                    device_id=dev_cfg.device_id,
                    ts=ts,
                    seq=seq,
                    sample=sample,
                    firmware=self._fleet_cfg.firmware,
                )
                seq += 1
                yield (ts, dev_idx, reading)

        streams = [_device_stream(i, dev_cfg) for i, dev_cfg in enumerate(self._fleet_cfg.devices)]
        merged = heapq.merge(*streams, key=lambda item: (item[0], item[1]))
        for _, _, reading in merged:
            yield reading

    def ground_truth(self, sim_end_ts: datetime | None = None) -> list[dict[str, Any]]:
        """Genera la verdad de referencia (ground truth) de los eventos de la flota."""
        end_ts = (
            sim_end_ts
            if sim_end_ts is not None
            else self._anchor_ts + timedelta(minutes=self._plan.duration_min)
        )
        return build_ground_truth(self._events, self._anchor_ts, end_ts, self._catalog)
