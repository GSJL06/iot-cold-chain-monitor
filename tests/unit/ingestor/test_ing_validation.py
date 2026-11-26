"""Pruebas unitarias de clasificación y validación pura para el ingestor."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ingestor.validation import ParsedReading, ParsedStatus, Rejection, Schemas, classify

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMAS_DIR = REPO_ROOT / "schemas"
TELEMETRY_EXAMPLES_DIR = REPO_ROOT / "examples" / "telemetry"
STATUS_EXAMPLES_DIR = REPO_ROOT / "examples" / "status"

VALID_TELEMETRY_EXAMPLES = sorted(TELEMETRY_EXAMPLES_DIR.glob("valid_*.json"))
INVALID_TELEMETRY_EXAMPLES = sorted(TELEMETRY_EXAMPLES_DIR.glob("invalid_*.json"))
VALID_STATUS_EXAMPLES = sorted(STATUS_EXAMPLES_DIR.glob("valid_*.json"))
INVALID_STATUS_EXAMPLES = sorted(STATUS_EXAMPLES_DIR.glob("invalid_*.json"))

FIXED_NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
KNOWN_DEVICES = frozenset({"SIM-HA-001", "SIM-FB-001", "SIM-LC-001"})
DEFAULT_TOPIC = "coldchain/v1/hosp-a/SIM-HA-001/telemetry"
DEFAULT_STATUS_TOPIC = "coldchain/v1/hosp-a/SIM-HA-001/status"


@pytest.fixture(scope="module")
def schemas() -> Schemas:
    """Carga los esquemas oficiales una sola vez por módulo."""
    return Schemas.load(SCHEMAS_DIR)


def _telemetry_bytes(**overrides: object) -> bytes:
    """Construye un payload telemetry.v1 válido en bytes con sobrescrituras opcionales."""
    data: dict[str, object] = {
        "schema": "telemetry.v1",
        "device_id": "SIM-HA-001",
        "ts": "2026-11-18T12:00:00Z",
        "seq": 10,
        "temp_c": 4.5,
        "humidity_pct": 50.0,
        "rssi_dbm": -85,
        "battery_pct": 99.0,
        "on_backup_power": False,
        "fw": "sim-1.0.0",
    }
    data.update(overrides)
    return json.dumps(data).encode("utf-8")


def _status_bytes(**overrides: object) -> bytes:
    """Construye un payload status.v1 válido en bytes con sobrescrituras opcionales."""
    data: dict[str, object] = {
        "schema": "status.v1",
        "device_id": "SIM-HA-001",
        "state": "online",
        "ts": "2026-11-18T11:59:30Z",
    }
    data.update(overrides)
    return json.dumps(data).encode("utf-8")


def _is_known(device_id: str) -> bool:
    """Comprueba pertenencia al conjunto fijo de dispositivos conocidos."""
    return device_id in KNOWN_DEVICES


@pytest.mark.parametrize(
    ("topic", "payload", "expected_reason"),
    [
        pytest.param(
            DEFAULT_TOPIC,
            _telemetry_bytes(temp_c=150.0),
            "schema_invalid",
            id="schema_invalid",
        ),
        pytest.param(
            DEFAULT_TOPIC,
            _telemetry_bytes(schema="telemetry.v2"),
            "unsupported_schema",
            id="unsupported_schema",
        ),
        pytest.param(
            DEFAULT_TOPIC,
            _telemetry_bytes(device_id="SIM-FB-001"),
            "topic_mismatch",
            id="topic_mismatch_payload_differs",
        ),
        pytest.param(
            "coldchain/v1/hosp-a/SIM-ZZ-999/telemetry",
            _telemetry_bytes(device_id="SIM-ZZ-999"),
            "unknown_device",
            id="unknown_device",
        ),
        pytest.param(
            DEFAULT_TOPIC,
            _telemetry_bytes(ts="2026-11-18T12:05:01Z"),
            "clock_skew",
            id="clock_skew",
        ),
    ],
)
def test_design_row_1_five_rejection_reasons(
    schemas: Schemas, topic: str, payload: bytes, expected_reason: str
):
    """Fila 1 del diseño: cada uno de los cinco motivos produce el Rejection esperado."""
    result = classify(topic, payload, _is_known, FIXED_NOW, schemas)
    assert isinstance(result, Rejection)
    assert result.reason == expected_reason
    assert result.topic == topic


@pytest.mark.parametrize(
    ("topic", "payload", "expected_reason"),
    [
        pytest.param(
            DEFAULT_TOPIC,
            json.dumps({"schema": "telemetry.v2", "device_id": "SIM-ZZ-999"}).encode("utf-8"),
            "unsupported_schema",
            id="unsupported_schema_before_schema_and_topic_mismatch",
        ),
        pytest.param(
            "coldchain/v1/hosp-a/SIM-ZZ-999/telemetry",
            _telemetry_bytes(device_id="SIM-ZZ-999", temp_c=200.0),
            "schema_invalid",
            id="schema_invalid_before_unknown_device",
        ),
        pytest.param(
            "coldchain/v1/BAD_SITE/SIM-HA-001/telemetry",
            b"{broken json",
            "topic_mismatch",
            id="malformed_topic_before_broken_json",
        ),
        pytest.param(
            "coldchain/v2/hosp-a/SIM-HA-001/telemetry",
            json.dumps({"schema": "telemetry.v2"}).encode("utf-8"),
            "topic_mismatch",
            id="malformed_topic_before_unsupported_schema",
        ),
        pytest.param(
            "coldchain/v1/hosp-a/SIM-HA-001/telemetry\n",
            _telemetry_bytes(),
            "topic_mismatch",
            id="trailing_newline_in_topic_rejected_first",
        ),
        pytest.param(
            DEFAULT_TOPIC,
            json.dumps(
                {
                    "schema": 123,
                    "device_id": "SIM-ZZ-999",
                    "ts": "2026-11-18T12:10:00Z",
                }
            ).encode("utf-8"),
            "schema_invalid",
            id="non_string_schema_goes_to_schema_invalid",
        ),
        pytest.param(
            DEFAULT_TOPIC,
            _telemetry_bytes(device_id="SIM-ZZ-999", ts="2026-11-18T12:10:00Z"),
            "topic_mismatch",
            id="topic_mismatch_before_unknown_device_and_clock_skew",
        ),
        pytest.param(
            "coldchain/v1/hosp-a/SIM-ZZ-999/telemetry",
            _telemetry_bytes(device_id="SIM-ZZ-999", ts="2026-11-18T12:10:00Z"),
            "unknown_device",
            id="unknown_device_before_clock_skew",
        ),
    ],
)
def test_design_row_2_precedence(
    schemas: Schemas, topic: str, payload: bytes, expected_reason: str
):
    """Fila 2 del diseño: ante múltiples fallos, gana el motivo de mayor precedencia."""
    result = classify(topic, payload, _is_known, FIXED_NOW, schemas)
    assert isinstance(result, Rejection)
    assert result.reason == expected_reason


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            (
                b'{"schema":"telemetry.v1","device_id":"SIM-HA-001",'
                b'"ts":"2026-11-18T12:00:00Z","seq":1,"temp_c":NaN,'
                b'"rssi_dbm":-80,"battery_pct":90,"on_backup_power":false}'
            ),
            id="nan_value",
        ),
        pytest.param(
            (
                b'{"schema":"telemetry.v1","device_id":"SIM-HA-001",'
                b'"ts":"2026-11-18T12:00:00Z","seq":1,"temp_c":Infinity,'
                b'"rssi_dbm":-80,"battery_pct":90,"on_backup_power":false}'
            ),
            id="infinity_value",
        ),
        pytest.param(
            (
                b'{"schema":"telemetry.v1","device_id":"SIM-HA-001",'
                b'"ts":"2026-11-18T12:00:00Z","seq":1,"temp_c":-Infinity,'
                b'"rssi_dbm":-80,"battery_pct":90,"on_backup_power":false}'
            ),
            id="neg_infinity_value",
        ),
        pytest.param(
            (
                b'{"schema":"telemetry.v1","device_id":"SIM-HA-001",'
                b'"ts":"2026-11-18T12:00:00Z","seq":1,"temp_c":1e999,'
                b'"rssi_dbm":-80,"battery_pct":90,"on_backup_power":false}'
            ),
            id="overflow_1e999",
        ),
        pytest.param(
            _telemetry_bytes(seq=2**40),
            id="seq_2_pow_40",
        ),
        pytest.param(
            _telemetry_bytes(rssi_dbm=True),
            id="rssi_dbm_boolean",
        ),
        pytest.param(
            b"{" + b'"x":"' + (b"a" * 5000) + b'"}',
            id="payload_over_4096_bytes",
        ),
        pytest.param(
            b"\xff\xfe\xfd",
            id="invalid_utf8_bytes",
        ),
        pytest.param(
            b"[1, 2, 3]",
            id="json_list",
        ),
        pytest.param(
            b'{"schema": "telemetry.v1",',
            id="broken_json",
        ),
        pytest.param(
            _telemetry_bytes(ts="2026-02-30T12:00:00Z"),
            id="invalid_calendar_date",
        ),
        pytest.param(
            _telemetry_bytes(ts="2026-11-18T12:00:00Z\n"),
            id="trailing_newline_in_ts",
        ),
    ],
)
def test_design_row_3_hostile_inputs(schemas: Schemas, payload: bytes):
    """Fila 3 del diseño: todas las entradas hostiles se clasifican como schema_invalid."""
    result = classify(DEFAULT_TOPIC, payload, _is_known, FIXED_NOW, schemas)
    assert isinstance(result, Rejection)
    assert result.reason == "schema_invalid"


def test_float_integers_seq_and_rssi_converted_to_int(schemas: Schemas):
    """Un seq o rssi_dbm como flotante entero (5.0, -87.0) se acepta y se convierte a int."""
    payload = _telemetry_bytes(seq=5.0, rssi_dbm=-87.0)
    result = classify(DEFAULT_TOPIC, payload, _is_known, FIXED_NOW, schemas)
    assert isinstance(result, ParsedReading)
    assert result.seq == 5
    assert type(result.seq) is int
    assert result.rssi_dbm == -87
    assert type(result.rssi_dbm) is int


@pytest.mark.parametrize(
    ("ts_str", "expected_microsecond"),
    [
        ("2026-11-18T12:00:00.5Z", 500_000),
        ("2026-11-18T12:00:00.25Z", 250_000),
        ("2026-11-18T12:00:00.123Z", 123_000),
    ],
)
def test_ts_with_milliseconds_parsed_as_utc(
    schemas: Schemas, ts_str: str, expected_microsecond: int
):
    """Acepta de 1 a 3 decimales en ts conservando milisegundos en UTC."""
    payload = _telemetry_bytes(ts=ts_str)
    result = classify(DEFAULT_TOPIC, payload, _is_known, FIXED_NOW, schemas)
    assert isinstance(result, ParsedReading)
    assert result.ts.tzinfo == UTC
    assert result.ts.microsecond == expected_microsecond


def test_clock_skew_boundary(schemas: Schemas):
    """Exactamente +300 s se acepta; +301 s y +360 s se rechazan como clock_skew."""
    accepted_ts = (FIXED_NOW + timedelta(seconds=300)).strftime("%Y-%m-%dT%H:%M:%SZ")
    accepted = classify(
        DEFAULT_TOPIC,
        _telemetry_bytes(ts=accepted_ts),
        _is_known,
        FIXED_NOW,
        schemas,
        skew_s=300,
    )
    assert isinstance(accepted, ParsedReading)

    for delta_s in (301, 360):
        rejected_ts = (FIXED_NOW + timedelta(seconds=delta_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rejected = classify(
            DEFAULT_TOPIC,
            _telemetry_bytes(ts=rejected_ts),
            _is_known,
            FIXED_NOW,
            schemas,
            skew_s=300,
        )
        assert isinstance(rejected, Rejection)
        assert rejected.reason == "clock_skew"


def test_rejection_payload_text_sanitization_and_truncation(schemas: Schemas):
    """El payload_text reemplaza NUL y bytes no UTF-8 por U+FFFD y trunca a 4096 caracteres."""
    payload_nul_and_invalid = b"abc\x00def\xffghi"
    res_small = classify(DEFAULT_TOPIC, payload_nul_and_invalid, _is_known, FIXED_NOW, schemas)
    assert isinstance(res_small, Rejection)
    assert res_small.reason == "schema_invalid"
    assert res_small.payload_text == "abc\ufffddef\ufffdghi"
    assert "\x00" not in res_small.payload_text

    huge_payload = b"A\x00\xff" * 2000
    res_huge = classify(DEFAULT_TOPIC, huge_payload, _is_known, FIXED_NOW, schemas)
    assert isinstance(res_huge, Rejection)
    assert res_huge.reason == "schema_invalid"
    assert len(res_huge.payload_text) == 4096
    assert "\x00" not in res_huge.payload_text


@pytest.mark.parametrize("example_path", VALID_TELEMETRY_EXAMPLES, ids=lambda p: p.name)
def test_telemetry_valid_examples(schemas: Schemas, example_path: Path):
    """Todos los ejemplos valid_*.json de telemetría producen ParsedReading."""
    raw = example_path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    topic = f"coldchain/v1/hosp-a/{data['device_id']}/telemetry"
    result = classify(topic, raw, _is_known, FIXED_NOW, schemas)
    assert isinstance(result, ParsedReading)
    assert result.device_id == data["device_id"]
    assert result.ts.tzinfo == UTC


@pytest.mark.parametrize("example_path", INVALID_TELEMETRY_EXAMPLES, ids=lambda p: p.name)
def test_telemetry_invalid_examples(schemas: Schemas, example_path: Path):
    """Todos los ejemplos invalid_*.json de telemetría producen Rejection schema_invalid."""
    raw = example_path.read_bytes()
    result = classify(DEFAULT_TOPIC, raw, _is_known, FIXED_NOW, schemas)
    assert isinstance(result, Rejection)
    assert result.reason == "schema_invalid"


@pytest.mark.parametrize("example_path", VALID_STATUS_EXAMPLES, ids=lambda p: p.name)
def test_status_valid_examples(schemas: Schemas, example_path: Path):
    """Todos los ejemplos valid_*.json de status producen ParsedStatus."""
    raw = example_path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    topic = f"coldchain/v1/hosp-a/{data['device_id']}/status"
    result = classify(topic, raw, _is_known, FIXED_NOW, schemas)
    assert isinstance(result, ParsedStatus)
    assert result.device_id == data["device_id"]
    assert result.state == data["state"]
    assert result.ts.tzinfo == UTC


@pytest.mark.parametrize("example_path", INVALID_STATUS_EXAMPLES, ids=lambda p: p.name)
def test_status_invalid_examples(schemas: Schemas, example_path: Path):
    """Todos los ejemplos invalid_*.json de status producen Rejection schema_invalid."""
    raw = example_path.read_bytes()
    result = classify(DEFAULT_STATUS_TOPIC, raw, _is_known, FIXED_NOW, schemas)
    assert isinstance(result, Rejection)
    assert result.reason == "schema_invalid"


def test_status_unknown_device(schemas: Schemas):
    """Un mensaje status válido de un dispositivo desconocido se rechaza como unknown_device."""
    topic = "coldchain/v1/hosp-a/SIM-ZZ-999/status"
    payload = _status_bytes(device_id="SIM-ZZ-999")
    result = classify(topic, payload, _is_known, FIXED_NOW, schemas)
    assert isinstance(result, Rejection)
    assert result.reason == "unknown_device"
