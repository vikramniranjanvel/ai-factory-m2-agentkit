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
from agentkit.schemas.dataset import load_dataset

DETERMINISTIC_METRICS = ("completion_rate", "deterministic_pass_rate")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def evaluator_metric(evaluator_id: str) -> str:
    """Gate metric name for a built-in evaluator: Builtin.GoalSuccessRate -> goal_success_rate_pass_rate."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", evaluator_id.split(".")[-1]).lower() + "_pass_rate"


class Suite(_Strict):
    """A named set of scenarios (one dataset file)."""

    dataset: str = Field(description="Path to the dataset file, relative to evaluation.yaml.", examples=["smoke.json"])
    score: bool = Field(
        default=False,
        description="Also run AgentCore batch evaluation on this suite's conversations. Slower: waits for telemetry.",
    )

    _relative = field_validator("dataset")(_project_relative)


class Check(_Strict):
    """Deterministic check on the final reply. `expected_from` is a dotted path into the scenario (expected.answer)."""

    type: Literal["response_contains", "response_not_contains", "response_matches_regex"] = Field(
        description="How the reply is compared with the expected value."
    )
    expected_from: Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")] = (
        Field(
            description="Dotted path to the expected value inside each scenario. Scenarios without it skip the check.",
            examples=["expected.answer"],
        )
    )


class Evaluator(_Strict):
    """An AgentCore built-in evaluator that scores conversations in suites with `score: true`."""

    id: Annotated[str, StringConstraints(pattern=r"^Builtin\.[A-Za-z]+$")] = Field(
        description="Built-in evaluator id. Its gate metric is the snake_case name plus `_pass_rate`.",
        examples=["Builtin.GoalSuccessRate", "Builtin.Helpfulness"],
    )
    min_score: Annotated[float, Field(ge=0, le=1)] | None = Field(
        default=None,
        description=(
            "Score at or above which one scenario counts as passed. Omit to use the runner's per-evaluator default."
        ),
        examples=[0.67],
    )


class Gate(_Strict):
    """A pass threshold on a run-level metric."""

    metric: str = Field(
        description=(
            "`completion_rate`, `deterministic_pass_rate`, or `<evaluator>_pass_rate` for a declared evaluator."
        ),
        examples=["completion_rate", "goal_success_rate_pass_rate"],
    )
    min: Annotated[float, Field(ge=0, le=1)] = Field(
        description="The run fails when the metric is below this value, 0 to 1.", examples=[1.0]
    )


class EvaluationConfig(_Strict):
    """How an agent is tested (`evals/evaluation.yaml`) and what must pass."""

    schema_version: Literal[1] = Field(description="Version of this file format. Always 1 for now.")
    suites: Annotated[
        dict[Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,31}$")], Suite], Field(min_length=1)
    ] = Field(description="Named suites: lowercase name to dataset. At least one.")
    checks: list[Check] = Field(
        default=[], description="Deterministic checks applied to every scenario in every suite."
    )
    evaluators: list[Evaluator] = Field(
        default=[], description="Built-in evaluators; only run for suites with `score: true`."
    )
    gates: list[Gate] = Field(default=[], description="Thresholds the run must meet; any miss fails the run.")

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
    """Parse and validate an evaluation.yaml and the datasets it names (they must exist next to it)."""
    path = Path(path)
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping at the top level")
    config = EvaluationConfig.model_validate(data)
    missing = [s.dataset for s in config.suites.values() if not (path.parent / s.dataset).exists()]
    if missing:
        raise ValueError(f"{path}: referenced files not found: {', '.join(sorted(set(missing)))}")
    for name, suite in config.suites.items():
        dataset = load_dataset(path.parent / suite.dataset)
        if suite.score:
            bare = [s.scenario_id for s in dataset.scenarios if not s.assertions]
            if bare:
                raise ValueError(f"{path}: suite '{name}' has 'score: true' but scenarios have no assertions: {bare}")
    return config
