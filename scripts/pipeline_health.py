"""Consulta de salud del pipeline de ingestión en TimescaleDB (solo lectura)."""

import argparse
import sys

import _common
import psycopg

_SQL_INGESTION_LAG = """
SELECT extract(epoch FROM now() - max(received_at)), max(received_at)
FROM readings
WHERE seq >= %s
"""

_SQL_LATENCY_P95 = """
SELECT
    count(*),
    percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM received_at - ts))
FROM readings
WHERE received_at > now() - make_interval(mins => %s)
  AND seq >= %s
"""

_SQL_SEQ_GAPS = """
SELECT device_id, sum(gap) AS missing
FROM (
  SELECT device_id, seq - lag(seq) OVER (PARTITION BY device_id ORDER BY ts) - 1 AS gap
  FROM readings
  WHERE ts > now() - make_interval(mins => %s)
    AND seq >= %s
) g
WHERE gap > 0
GROUP BY device_id
ORDER BY device_id
"""

_SQL_REJECTIONS = """
SELECT reason, count(*) AS n
FROM rejected_messages
WHERE received_at > now() - make_interval(mins => %s)
GROUP BY reason
ORDER BY reason
"""

_SQL_DEVICE_STATUS = """
SELECT device_id, state::text, last_seen
FROM device_status
ORDER BY device_id
"""


def main() -> int:
    """Imprime métricas operativas del pipeline y devuelve 0 si conecta o 2 si falla."""
    parser = argparse.ArgumentParser(
        description="Muestra el estado de salud del pipeline de ingestión (solo lectura)",
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=5,
        help="Ventana temporal en minutos (por defecto 5)",
    )
    parser.add_argument(
        "--load-only",
        action="store_true",
        help="Limita lecturas a mensajes de prueba de carga (seq >= 1000000000)",
    )
    args = parser.parse_args()

    if args.minutes < 1:
        sys.stderr.write("Error: --minutes debe ser >= 1\n")
        return 2

    min_seq = 1_000_000_000 if args.load_only else 0

    try:
        conn = _common.db_connect()
    except (psycopg.Error, RuntimeError, OSError, ValueError) as exc:
        sys.stderr.write(f"Error conectando a la base de datos: {type(exc).__name__}\n")
        return 2

    try:
        with conn:
            offset_ms = _common.clock_offset_s(conn) * 1000.0
            with conn.cursor() as cur:
                cur.execute(_SQL_INGESTION_LAG, (min_seq,))
                lag_row = cur.fetchone()

                cur.execute(_SQL_LATENCY_P95, (args.minutes, min_seq))
                p95_row = cur.fetchone()

                cur.execute(_SQL_SEQ_GAPS, (args.minutes, min_seq))
                gaps_rows = cur.fetchall()

                cur.execute(_SQL_REJECTIONS, (args.minutes,))
                rejection_rows = cur.fetchall()

                cur.execute(_SQL_DEVICE_STATUS)
                status_rows = cur.fetchall()
    except psycopg.Error as exc:
        sys.stderr.write(f"Error consultando métricas de salud: {type(exc).__name__}\n")
        return 2
    finally:
        conn.close()

    print(f"Desfase de reloj (BD - local): {offset_ms:+.1f} ms")
    if abs(offset_ms) > 500.0:
        print("AVISO: el desfase de reloj supera 500 ms")

    if lag_row is None or lag_row[0] is None:
        print("Retraso de ingestión: sin lecturas")
    else:
        print(f"Retraso de ingestión: {float(lag_row[0]):.3f} s (última: {lag_row[1]})")

    reading_count = int(p95_row[0]) if p95_row is not None else 0
    p95_val = p95_row[1] if p95_row is not None else None
    p95_text = f"{float(p95_val):.3f} s" if p95_val is not None else "N/A"
    print(f"Latencia p95 (últimos {args.minutes} min, lecturas={reading_count}): {p95_text}")

    total_gaps = sum(int(row[1]) for row in gaps_rows)
    print(f"Huecos de seq (últimos {args.minutes} min, total={total_gaps}):")
    if not gaps_rows:
        print("  (sin huecos)")
    else:
        for dev_id, missing in gaps_rows:
            print(f"  {dev_id}: {int(missing)}")

    print(f"Rechazos por motivo (últimos {args.minutes} min):")
    if not rejection_rows:
        print("  (sin rechazos)")
    else:
        for reason, cnt in rejection_rows:
            print(f"  {reason}: {int(cnt)}")

    print("Estado de dispositivos:")
    if not status_rows:
        print("  (sin filas en device_status)")
    else:
        for dev_id, state, last_seen in status_rows:
            seen_str = str(last_seen) if last_seen is not None else "N/A"
            print(f"  {dev_id:<12} state={state:<8} last_seen={seen_str}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
