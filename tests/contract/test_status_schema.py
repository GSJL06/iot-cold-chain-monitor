"""Pruebas de contrato para validar ejemplos contra schemas/status.v1.schema.json."""

import json
from pathlib import Path

import jsonschema
import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "status.v1.schema.json"
EXAMPLES_DIR = REPO_ROOT / "examples" / "status"

VALID_EXAMPLES = sorted(EXAMPLES_DIR.glob("valid_*.json"))
INVALID_EXAMPLES = sorted(EXAMPLES_DIR.glob("invalid_*.json"))


def load_json(path: Path) -> dict:
    """Carga un archivo JSON en UTF-8."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def schema() -> dict:
    """Carga el JSON Schema status.v1."""
    return load_json(SCHEMA_PATH)


@pytest.fixture(scope="module")
def validator(schema: dict) -> Draft202012Validator:
    """Construye el validador Draft 2020-12 para status.v1."""
    return Draft202012Validator(schema)


def test_schema_is_valid():
    """Verifica que status.v1.schema.json sea un esquema Draft 2020-12 válido."""
    schema = load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("example_path", VALID_EXAMPLES, ids=lambda p: p.name)
def test_valid_examples_pass(example_path: Path, validator: Draft202012Validator):
    """Verifica que los ejemplos válidos cumplan el contrato status.v1."""
    payload = load_json(example_path)
    validator.validate(payload)


@pytest.mark.parametrize("example_path", INVALID_EXAMPLES, ids=lambda p: p.name)
def test_invalid_examples_fail(example_path: Path, validator: Draft202012Validator):
    """Verifica que los ejemplos inválidos fallen la validación de contrato."""
    payload = load_json(example_path)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(payload)
