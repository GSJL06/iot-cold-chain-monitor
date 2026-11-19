"""Interfaz de línea de comandos del simulador de telemetría IoT."""

import argparse
import hashlib
import json
import os
import signal
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from simulator.clock import parse_ts
from simulator.config import (
    FleetCfg,
    load_catalog,
    load_fleet,
    load_plan,
    load_thermal,
    validate_plan,
)
from simulator.fleet import Fleet
from simulator.publisher import SimPublisher


def build_parser() -> argparse.ArgumentParser:
    """Construye el analizador de argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Simulador de telemetría IoT para monitor de cadena de frío",
    )
    parser.add_argument(
        "--config-dir",
        default=os.environ.get("SIM_CONFIG_DIR", "config"),
        help="Directorio de archivos de configuración (por defecto $SIM_CONFIG_DIR o 'config')",
    )

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # Subcomando: generate
    gen_parser = subparsers.add_parser(
        "generate",
        help="Genera telemetría sin red y escribe archivos JSON Lines y ground truth",
    )
    gen_parser.add_argument(
        "--scenario",
        required=True,
        help="Ruta al archivo YAML del escenario a simular",
    )
    gen_parser.add_argument(
        "--start",
        default="2026-01-01T00:00:00Z",
        help="Instante inicial en UTC (formato ISO-8601, por defecto 2026-01-01T00:00:00Z)",
    )
    gen_parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Semilla pseudoaleatoria (sustituye a la del plan si se especifica)",
    )
    gen_parser.add_argument(
        "--out",
        default=None,
        help="Ruta del archivo de salida JSON Lines (.jsonl)",
    )
    gen_parser.add_argument(
        "--truth",
        default=None,
        help="Ruta del archivo de salida de verdad de referencia (.json)",
    )

    # Subcomando: publish
    pub_parser = subparsers.add_parser(
        "publish",
        help="Publica telemetría en tiempo real hacia el broker MQTT",
    )
    pub_parser.add_argument(
        "--scenario",
        required=True,
        help="Ruta al archivo YAML del escenario a simular",
    )
    pub_parser.add_argument(
        "--backfill-hours",
        type=int,
        default=0,
        help="Horas de histórico normal previo para publicar en modo acelerado (por defecto 0)",
    )
    pub_parser.add_argument(
        "--period-s",
        type=int,
        default=None,
        help="Periodo de telemetría en segundos (sustituye al de fleet.yaml; debe ser >= 1)",
    )
    pub_parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Semilla pseudoaleatoria (sustituye a la del plan si se especifica)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada principal para la CLI del simulador."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "publish":
        password = os.environ.get("MQTT_DEVICE_PASSWORD")
        if not password:
            sys.stderr.write(
                "Error: falta la variable de entorno obligatoria MQTT_DEVICE_PASSWORD\n"
            )
            return 1

        host = os.environ.get("MQTT_HOST", "localhost")
        try:
            port = int(os.environ.get("MQTT_PORT", "1883"))
        except ValueError:
            sys.stderr.write("Error: MQTT_PORT debe ser un entero válido\n")
            return 1

        if args.backfill_hours < 0:
            sys.stderr.write("Error: --backfill-hours debe ser >= 0\n")
            return 1

        if args.period_s is not None and args.period_s < 1:
            sys.stderr.write("Error: --period-s debe ser un entero >= 1\n")
            return 1

        config_dir = Path(args.config_dir)
        fleet_cfg = load_fleet(config_dir / "fleet.yaml")
        thermal_cfg = load_thermal(config_dir / "thermal_models.yaml")
        catalog = load_catalog(config_dir / "scenario_catalog.yaml")
        plan = load_plan(args.scenario)
        validate_plan(plan, fleet_cfg, catalog)

        if args.period_s is not None:
            fleet_cfg = FleetCfg(
                period_s=args.period_s,
                firmware=fleet_cfg.firmware,
                phase_step_s=fleet_cfg.phase_step_s,
                devices=fleet_cfg.devices,
            )

        stop_event = threading.Event()

        def _handle_signal(signum: int, frame: Any) -> None:
            stop_event.set()

        signal.signal(signal.SIGINT, _handle_signal)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle_signal)

        now_start = datetime.now(UTC).replace(microsecond=0)
        fleet = Fleet(
            fleet_cfg=fleet_cfg,
            thermal_cfg=thermal_cfg,
            catalog=catalog,
            plan=plan,
            start_ts=now_start,
            seed=args.seed,
        )

        publisher = SimPublisher(
            fleet=fleet,
            host=host,
            port=port,
            password=password,
        )
        publisher.run(backfill_hours=args.backfill_hours, stop_event=stop_event)
        return 0

    if args.subcommand == "generate":
        config_dir = Path(args.config_dir)
        fleet_cfg = load_fleet(config_dir / "fleet.yaml")
        thermal_cfg = load_thermal(config_dir / "thermal_models.yaml")
        catalog = load_catalog(config_dir / "scenario_catalog.yaml")
        plan = load_plan(args.scenario)
        validate_plan(plan, fleet_cfg, catalog)

        start_ts = parse_ts(args.start)
        fleet = Fleet(
            fleet_cfg=fleet_cfg,
            thermal_cfg=thermal_cfg,
            catalog=catalog,
            plan=plan,
            start_ts=start_ts,
            seed=args.seed,
        )

        hasher = hashlib.sha256()
        reading_count = 0

        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                for reading in fleet.generate(duration_min=plan.duration_min):
                    line = (
                        json.dumps(
                            reading.to_payload(),
                            separators=(",", ":"),
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    f.write(line)
                    hasher.update(line.encode("utf-8"))
                    reading_count += 1
        else:
            for reading in fleet.generate(duration_min=plan.duration_min):
                line = (
                    json.dumps(
                        reading.to_payload(),
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                hasher.update(line.encode("utf-8"))
                reading_count += 1

        sha256_hex = hasher.hexdigest()

        sim_end_ts = start_ts + timedelta(minutes=plan.duration_min)
        truth_events = fleet.ground_truth(sim_end_ts)

        class_counts: dict[str, int] = {
            "real_fault": 0,
            "benign": 0,
            "diagnostic": 0,
            "limitation": 0,
        }
        for ev in truth_events:
            cls_name = ev.get("class", "unknown")
            class_counts[cls_name] = class_counts.get(cls_name, 0) + 1

        if args.truth:
            truth_path = Path(args.truth)
            truth_path.parent.mkdir(parents=True, exist_ok=True)
            with open(truth_path, "w", encoding="utf-8") as f:
                json.dump(truth_events, f, indent=2, ensure_ascii=False)
                f.write("\n")

        print(f"Lecturas generadas: {reading_count}")
        print(
            f"Eventos ground truth: real_fault={class_counts['real_fault']}, "
            f"benign={class_counts['benign']}, diagnostic={class_counts['diagnostic']}, "
            f"limitation={class_counts['limitation']}"
        )
        target_name = args.out if args.out else "stdout"
        print(f"SHA-256 ({target_name}): {sha256_hex}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
