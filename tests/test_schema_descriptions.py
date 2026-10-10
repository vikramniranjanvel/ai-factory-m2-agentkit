"""Every schema key and class must be documented: descriptions feed editor hover help and the generated reference."""

import pytest

from agentkit.schemas.export import SCHEMAS


def _undocumented(schema: dict) -> list[str]:
    missing = [f"{name}: class docstring" for name, d in schema.get("$defs", {}).items() if not d.get("description")]
    if not schema.get("description"):
        missing.append(f"{schema.get('title')}: class docstring")
    for name, d in {schema.get("title"): schema, **schema.get("$defs", {})}.items():
        missing += [f"{name}.{prop}" for prop, p in d.get("properties", {}).items() if not p.get("description")]
    return missing


@pytest.mark.parametrize("filename", SCHEMAS)
def test_every_field_and_class_has_a_description(filename):
    assert _undocumented(SCHEMAS[filename].model_json_schema()) == []
