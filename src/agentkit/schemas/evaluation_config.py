"""Evaluation config: how an agent is tested and what must pass (`evals/evaluation.yaml`).

Suites name datasets, checks are deterministic per-scenario assertions on the final reply, evaluators are AgentCore
built-in quality scorers (only run for suites with `score: true`), and gates are the pass thresholds. One set of
thresholds applies to every environment. Dataset paths are relative to this file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from agentkit.schemas.agent_definition import _project_relative

DETERMINISTIC_METRICS = ("completion_rate", "deterministic_pass_rate")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def evaluator_metric(evaluator_id: str) -> str:
    """Gate metric name for a built-in evaluator: Builtin.GoalSuccessRate -> goal_success_rate_pass_rate."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", evaluator_id.split(".")[-1]).lower() + "_pass_rate"


class Suite(_Strict):
    dataset: str  # path to a dataset file, relative to evaluation.yaml
    score: bool = False  # also run AgentCore batch evaluation (slow: waits for telemetry)

    _relative = field_validator("dataset")(_project_relative)


class Check(_Strict):
    """Deterministic check on the final reply. `expected_from` is a dotted path into the scenario (expected.answer)."""

    type: Literal["response_contains", "response_not_contains", "response_matches_regex"]
    expected_from: Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")]


class Evaluator(_Strict):
    id: Annotated[str, StringConstraints(pattern=r"^Builtin\.[A-Za-z]+$")]
    # Score at or above which one scenario counts as passed. Omitted: the runner's per-evaluator default applies.
    min_score: Annotated[float, Field(ge=0, le=1)] | None = None


class Gate(_Strict):
    metric: str  # completion_rate, deterministic_pass_rate, or <evaluator>_pass_rate
    min: Annotated[float, Field(ge=0, le=1)]  # the run fails when the metric is below this


class EvaluationConfig(_Strict):
    schema_version: Literal[1]
    suites: Annotated[
        dict[Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,31}$")], Suite], Field(min_length=1)
    ]
    checks: list[Check] = []
    evaluators: list[Evaluator] = []
    gates: list[Gate] = []

    @model_validator(mode="after")
    def _consistent(self) -> EvaluationConfig:
        ids = [e.id for e in self.evaluators]
        if len(set(ids)) != len(ids):
            raise ValueError("evaluators must be unique")
        scoring = [name for name, s in self.suites.items() if s.score]
        if self.evaluators and not scoring:
            raise ValueError("evaluators are declared but no suite has 'score: true'")
        if scoring and not self.evaluators:
            raise ValueError(f"suite '{scoring[0]}' has 'score: true' but no evaluators are declared")
        evaluator_metrics = {evaluator_metric(i) for i in ids}
        allowed = {*DETERMINISTIC_METRICS, *evaluator_metrics}
        seen: set[str] = set()
        for gate in self.gates:
            if gate.metric not in allowed:
                raise ValueError(f"gate metric '{gate.metric}' is not available (have: {', '.join(sorted(allowed))})")
            if gate.metric in seen:
                raise ValueError(f"duplicate gate for '{gate.metric}'")
            seen.add(gate.metric)
        if "deterministic_pass_rate" in seen and not self.checks:
            raise ValueError("gate 'deterministic_pass_rate' needs at least one check")
        return self


def load_evaluation_config(path: str | Path) -> EvaluationConfig:
    """Parse and validate an evaluation.yaml, and check that the datasets it names exist next to it."""
    path = Path(path)
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping at the top level")
    config = EvaluationConfig.model_validate(data)
    missing = [s.dataset for s in config.suites.values() if not (path.parent / s.dataset).exists()]
    if missing:
        raise ValueError(f"{path}: referenced files not found: {', '.join(sorted(set(missing)))}")
    return config
