import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agentkit.schemas import AgentDefinition, load_agent_definition

FIXTURE = Path(__file__).parent / "fixtures" / "valid" / "agent.yaml"
SCHEMA_FILE = Path(__file__).parents[1] / "schemas" / "agent-definition.schema.json"


def base(**overrides):
    data = yaml.safe_load(FIXTURE.read_text())
    data.update(overrides)
    return data


def test_v1_equivalent_loads():
    d = load_agent_definition(FIXTURE)
    assert d.name == "supportTriage" and d.model.credential == "openai-key" and d.memory.mode == "disabled"


def test_defaults():
    d = AgentDefinition.model_validate(
        {"schema_version": 1, "project": "p", "name": "a", "model": {"provider": "bedrock", "model_id": "m"}}
    )
    assert d.prompt == "prompts/system.md" and d.skills == [] and d.memory.mode == "disabled" and d.tags == {}


@pytest.mark.parametrize(
    "data",
    [
        base(unknown_field=1),  # strict: typos fail
        base(schema_version=2),
        base(name="has_underscore"),
        base(project="Support"),  # lowercase letters, digits and hyphens only
        base(project="support_triage"),
        base(project="../prod"),
        base(project="1abc"),
        base(project="x" * 41),
        base(name="x" * 33),
        base(name="1abc"),
        base(prompt="../outside.md"),
        base(prompt="/etc/passwd"),
        base(skills=[{"path": "../x"}]),
        base(tools=[{"type": "remote_mcp", "name": "t"}]),
        base(tags={"agentcore:x": "1"}),
        base(tags={"AWS:x": "1"}),
        base(memory={"mode": "existing", "name": "m"}),  # not exposed
        base(memory={"mode": "managed", "strategies": []}),
        base(limits={"max_iterations": 0}),
        base(truncation={"strategy": "sliding_window", "summary_ratio": 0.5}),
        base(model={"provider": "open_ai", "model_id": "m"}),  # missing credential
        base(model={"provider": "bedrock", "model_id": "m", "credential": "c"}),
        base(model={"provider": "open_ai", "model_id": "m", "credential": "c", "api_format": "converse_stream"}),
        base(model={"provider": "open_ai", "model_id": "m", "credential": "arn:aws:x"}),  # ARNs not allowed
        base(model={"provider": "open_ai", "model_id": "m", "credential": "c", "temperature": 3}),
        base(model={"provider": "open_ai", "model_id": "m", "credential": "c", "api_key_arn": "x"}),
        base(model={"provider": "open_ai", "model_id": "m", "credential": "c", "top_k": 40}),  # gemini only
        base(tags={"team": "a#b"}),
        base(tags={"te$m": "a"}),
    ],
)
def test_invalid(data):
    with pytest.raises(ValidationError):
        AgentDefinition.model_validate(data)


def test_missing_referenced_file(tmp_path):
    (tmp_path / "agent.yaml").write_text(FIXTURE.read_text())
    with pytest.raises(ValueError, match="not found"):
        load_agent_definition(tmp_path / "agent.yaml")


def test_json_schema_file_is_current():
    """schemas/agent-definition.schema.json is generated: regenerate with `uv run python -m agentkit.schemas.export`."""
    assert json.loads(SCHEMA_FILE.read_text()) == AgentDefinition.model_json_schema()


def test_project_is_required():
    data = base()
    del data["project"]
    with pytest.raises(ValidationError, match="project"):
        AgentDefinition.model_validate(data)


def test_project_is_in_the_published_schema():
    schema = json.loads(SCHEMA_FILE.read_text())
    assert "project" in schema["required"] and "registry" in schema["properties"]["project"]["description"]
