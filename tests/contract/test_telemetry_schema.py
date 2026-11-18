import json
from pathlib import Path
import pytest
import jsonschema
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "telemetry.v1.schema.json"
EXAMPLES_DIR = REPO_ROOT / "examples" / "telemetry"

VALID_EXAMPLES = sorted(EXAMPLES_DIR.glob("valid_*.json"))
INVALID_EXAMPLES = sorted(EXAMPLES_DIR.glob("invalid_*.json"))


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def schema() -> dict:
    return load_json(SCHEMA_PATH)


@pytest.fixture(scope="module")
def validator(schema: dict) -> Draft202012Validator:
    return Draft202012Validator(schema)


def test_schema_is_valid():
    schema = load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("example_path", VALID_EXAMPLES, ids=lambda p: p.name)
def test_valid_examples_pass(example_path: Path, validator: Draft202012Validator):
    payload = load_json(example_path)
    validator.validate(payload)


@pytest.mark.parametrize("example_path", INVALID_EXAMPLES, ids=lambda p: p.name)
def test_invalid_examples_fail(example_path: Path, validator: Draft202012Validator):
    payload = load_json(example_path)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(payload)
