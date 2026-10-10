import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agentkit.schemas import EvaluationConfig, load_evaluation_config
from agentkit.schemas.evaluation_config import evaluator_metric
from agentkit.schemas.export import SCHEMAS

FIXTURE = Path(__file__).parent / "fixtures" / "valid" / "evals" / "evaluation.yaml"
SCHEMA_FILE = Path(__file__).parents[1] / "schemas" / "evaluation-config.schema.json"


def base(**overrides):
    data = yaml.safe_load(FIXTURE.read_text())
    data.update(overrides)
    return data


def test_v1_equivalent_loads():
    c = load_evaluation_config(FIXTURE)
    assert c.suites["full"].score and c.checks[0].type == "response_matches_regex" and len(c.gates) == 5


def test_minimal():
    c = EvaluationConfig.model_validate({"schema_version": 1, "suites": {"smoke": {"dataset": "s.json"}}})
    assert c.checks == [] and c.evaluators == [] and c.gates == []


@pytest.mark.parametrize(
    "id_,metric",
    [
        ("Builtin.Helpfulness", "helpfulness_pass_rate"),
        ("Builtin.GoalSuccessRate", "goal_success_rate_pass_rate"),
        ("Builtin.Harmfulness", "harmfulness_pass_rate"),
    ],
)
def test_evaluator_metric_names_match_v1(id_, metric):
    assert evaluator_metric(id_) == metric


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": 2},
        {"dataset_version": "1.0.0"},
        {"suites": {}},
        {"suites": {"Smoke": {"dataset": "s.json"}}},
        {"suites": {"smoke": {"dataset": "../s.json"}}},
        {"suites": {"smoke": {"dataset": "/abs/s.json"}}},
        {"suites": {"smoke": {"dataset": "s.json", "extra": 1}}},
        {"checks": [{"type": "response_equals", "expected_from": "expected.answer"}]},
        {"checks": [{"type": "response_contains", "expected_from": "expected..answer"}]},
        {"evaluators": [{"id": "Helpfulness"}]},
        {"evaluators": [{"id": "Builtin.Helpfulness", "min_score": 1.5}]},
        {"evaluators": [{"id": "Builtin.Helpfulness"}, {"id": "Builtin.Helpfulness"}]},
        {"suites": {"full": {"dataset": "d.json", "score": True}}, "evaluators": []},
        {"gates": [{"metric": "latency", "min": 0.5}]},
        {"gates": [{"metric": "completion_rate", "min": 1.1}]},
        {"gates": [{"metric": "completion_rate", "min": 1}, {"metric": "completion_rate", "min": 0.5}]},
        {"gates": [{"metric": "deterministic_pass_rate", "min": 1}], "checks": []},
        {"unknown": 1},
    ],
)
def test_invalid(overrides):
    with pytest.raises(ValidationError):
        EvaluationConfig.model_validate(base(**overrides))


def test_gate_for_undeclared_evaluator_rejected():
    data = base(evaluators=[{"id": "Builtin.Helpfulness"}])
    with pytest.raises(ValidationError, match="goal_success_rate_pass_rate"):
        EvaluationConfig.model_validate(data)


def test_evaluators_without_scoring_suite_rejected():
    data = base(suites={"smoke": {"dataset": "smoke.json"}})
    with pytest.raises(ValidationError, match="no suite has 'score: true'"):
        EvaluationConfig.model_validate(data)


def test_missing_dataset_file(tmp_path):
    (tmp_path / "evaluation.yaml").write_text(yaml.safe_dump(base()))
    with pytest.raises(ValueError, match="not found: dataset.json, smoke.json"):
        load_evaluation_config(tmp_path / "evaluation.yaml")


def test_json_schema_file_is_current():
    assert json.loads(SCHEMA_FILE.read_text()) == SCHEMAS["evaluation-config.schema.json"].model_json_schema()
