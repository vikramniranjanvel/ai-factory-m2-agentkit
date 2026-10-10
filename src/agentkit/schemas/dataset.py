"""Dataset: the scenarios a suite runs (`evals/*.json`).

Each scenario is one conversation: the user turns sent to the agent, an optional expected answer used by the
deterministic checks, and optional natural-language assertions used by AgentCore batch evaluation (suites with
`score: true`). The file is identified by its path; the runner records its content hash with every run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

NonEmpty = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Turn(_Strict):
    """One user message sent to the agent."""

    input: NonEmpty = Field(description="The user message.", examples=["TCK-3190: user is stuck in a login loop"])
    expected_response: NonEmpty | None = Field(
        default=None,
        description="Reference reply for this turn, passed to AgentCore evaluators as ground truth. Optional.",
    )


class Expected(_Strict):
    """What the final reply must satisfy in the deterministic checks."""

    answer: NonEmpty = Field(
        description=(
            "Pattern the final reply is checked against. A regular expression (Python `re` syntax), so a plain word "
            "also works; use `(?i)` for case-insensitive."
        ),
        examples=["P1", "(?i)split"],
    )

    @field_validator("answer")
    @classmethod
    def _valid_regex(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"not a valid regular expression: {exc}") from exc
        return value


class Scenario(_Strict):
    """One test conversation."""

    scenario_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")] = Field(
        description="Unique id within the dataset: letters, digits, `_` and `-`, up to 64 characters.",
        examples=["smoke-001-login-loop"],
    )
    turns: list[Turn] = Field(min_length=1, description="User messages, sent in order in one session. At least one.")
    expected: Expected | None = Field(
        default=None, description="Expected answer for the deterministic checks. Omit to skip the checks."
    )
    assertions: list[NonEmpty] = Field(
        default=[],
        description=(
            "Plain-language statements about what the agent should do. Used by AgentCore evaluators (for example "
            "GoalSuccessRate) in suites with `score: true`."
        ),
        examples=[["The agent says to clear the cookie nb_sess_v4, with priority P2"]],
    )


class Dataset(_Strict):
    """A list of scenarios."""

    schema_version: Literal[1] = Field(description="Version of this file format. Always 1 for now.")
    scenarios: list[Scenario] = Field(min_length=1, description="The scenarios, run in order. At least one.")

    @model_validator(mode="after")
    def _unique_ids(self) -> Dataset:
        seen: set[str] = set()
        for s in self.scenarios:
            if s.scenario_id in seen:
                raise ValueError(f"duplicate scenario_id '{s.scenario_id}'")
            seen.add(s.scenario_id)
        return self


def load_dataset(path: str | Path) -> Dataset:
    """Parse and validate a dataset JSON file."""
    path = Path(path)
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object at the top level")
    return Dataset.model_validate(data)
