import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentkit.schemas import Dataset, load_dataset, load_evaluation_config
from agentkit.schemas.export import SCHEMAS

EVALS = Path(__file__).parent / "fixtures" / "valid" / "evals"
SCHEMA_FILE = Path(__file__).parents[1] / "schemas" / "dataset.schema.json"


def scenario(**overrides):
    return {"scenario_id": "s-1", "turns": [{"input": "hi"}], **overrides}


def doc(*scenarios):
    return {"schema_version": 1, "scenarios": list(scenarios) or [scenario()]}


def test_v1_datasets_load():
    assert len(load_dataset(EVALS / "smoke.json").scenarios) == 2
    full = load_dataset(EVALS / "dataset.json")
    assert len(full.scenarios) == 5 and all(s.assertions for s in full.scenarios)


def test_minimal_scenario():
    s = Dataset.model_validate(doc()).scenarios[0]
    assert s.expected is None and s.assertions == [] and s.turns[0].expected_response is None


def test_multi_turn_and_expected_response():
    s = Dataset.model_validate(
        doc(scenario(turns=[{"input": "a", "expected_response": "b"}, {"input": "c"}], expected={"answer": "P1"}))
    ).scenarios[0]
    assert len(s.turns) == 2 and s.turns[0].expected_response == "b"


@pytest.mark.parametrize(
    "data",
    [
        doc(scenario(), scenario()),  # duplicate id
        {"schema_version": 2, "scenarios": [scenario()]},
        {"schema_version": 1, "scenarios": []},
        {"scenarios": [scenario()]},  # missing schema_version
        doc(scenario(turns=[])),
        doc(scenario(turns=[{"input": "  "}])),
        doc(scenario(turns=[{"input": "a", "extra": 1}])),
        doc(scenario(scenario_id="has space")),
        doc(scenario(scenario_id="")),
        doc(scenario(expected={"answer": "("})),  # bad regex
        doc(scenario(expected={"answer": ""})),
        doc(scenario(expected={"answer": "x", "other": 1})),
        doc(scenario(assertions=[""])),
        doc(scenario(unknown=1)),
    ],
)
def test_invalid(data):
    with pytest.raises(ValidationError):
        Dataset.model_validate(data)


def test_bare_list_rejected(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps([scenario()]))
    with pytest.raises(ValueError, match="JSON object"):
        load_dataset(p)


def test_score_suite_needs_assertions(tmp_path):
    (tmp_path / "d.json").write_text(json.dumps(doc(scenario())))
    (tmp_path / "evaluation.yaml").write_text(
        "schema_version: 1\nsuites:\n  full: {dataset: d.json, score: true}\nevaluators:\n  - id: Builtin.Helpfulness\n"
    )
    with pytest.raises(ValueError, match="no assertions"):
        load_evaluation_config(tmp_path / "evaluation.yaml")


def test_invalid_dataset_fails_evaluation_load(tmp_path):
    (tmp_path / "d.json").write_text(json.dumps(doc(scenario(), scenario())))
    (tmp_path / "evaluation.yaml").write_text("schema_version: 1\nsuites:\n  smoke: {dataset: d.json}\n")
    with pytest.raises(ValidationError, match="duplicate"):
        load_evaluation_config(tmp_path / "evaluation.yaml")


def test_json_schema_file_is_current():
    """schemas/dataset.schema.json is generated: regenerate with `uv run python -m agentkit.schemas.export`."""
    assert json.loads(SCHEMA_FILE.read_text()) == SCHEMAS["dataset.schema.json"].model_json_schema()
