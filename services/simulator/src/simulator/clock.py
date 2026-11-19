"""Gestión del tiempo simulado y generación de instantes de muestreo."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta


def parse_ts(text: str) -> datetime:
    """Parsea una marca de tiempo ISO-8601 a un datetime UTC con zona horaria."""
    # Python 3.11+ soporta sufijo 'Z' directamente en fromisoformat
    ts = datetime.fromisoformat(text)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def format_ts(ts: datetime) -> str:
    """Formatea un datetime UTC con el formato '%Y-%m-%dT%H:%M:%SZ'."""
    if ts.tzinfo is not None:
        ts = ts.astimezone(UTC)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def tick_times(
    start: datetime,
    period_s: int,
    phase_s: int,
    count: int | None = None,
) -> Iterator[datetime]:
    """Genera marcas de tiempo sucesivas separadas por period_s a partir de start + phase_s.

    Si count es None, la secuencia es infinita.
    """
    start_utc = start.astimezone(UTC) if start.tzinfo is not None else start.replace(tzinfo=UTC)
    current = start_utc + timedelta(seconds=phase_s)
    delta = timedelta(seconds=period_s)

    if count is None:
        while True:
            yield current
            current += delta
    else:
        for _ in range(count):
            yield current
            current += delta
