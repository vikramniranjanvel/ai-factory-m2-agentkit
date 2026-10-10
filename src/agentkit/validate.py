"""Validate a project directory against the schemas: `agent.yaml`, `evals/evaluation.yaml` and the datasets it names."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from agentkit.schemas import EvaluationConfig, load_agent_definition, load_dataset, load_evaluation_config

AGENT_FILE = "agent.yaml"
EVAL_FILE = "evals/evaluation.yaml"


@dataclass(frozen=True)
class Issue:
    file: str
    path: str  # key path inside the file, e.g. gates[2].metric; empty when the problem is the file as a whole
    message: str

    def __str__(self) -> str:
        return f"{self.file}: {self.path + ': ' if self.path else ''}{self.message}"


def key_path(loc: tuple[int | str, ...]) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def _issues(file: str, exc: Exception) -> list[Issue]:
    if isinstance(exc, ValidationError):
        return [
            Issue(file, key_path(e["loc"]), e["msg"].removeprefix("Value error, ").removeprefix("Assertion failed, "))
            for e in exc.errors()
        ]
    if isinstance(exc, yaml.YAMLError | json.JSONDecodeError):
        return [Issue(file, "", f"not valid {'YAML' if isinstance(exc, yaml.YAMLError) else 'JSON'}: {exc}")]
    return [Issue(file, "", str(exc).split(f"{file}: ", 1)[-1])]  # loaders prefix messages with the full path


def validate_project(root: str | Path) -> tuple[list[str], list[Issue]]:
    """Return (files checked, problems found). Problems are reported per file with the key path."""
    root = Path(root)
    checked: list[str] = []
    issues: list[Issue] = []

    if not (root / AGENT_FILE).exists():
        issues.append(Issue(AGENT_FILE, "", "file not found"))
    else:
        checked.append(AGENT_FILE)
        try:
            load_agent_definition(root / AGENT_FILE)
        except (ValidationError, ValueError, yaml.YAMLError) as exc:
            issues += _issues(AGENT_FILE, exc)

    eval_path = root / EVAL_FILE
    if not eval_path.exists():
        issues.append(Issue(EVAL_FILE, "", "file not found"))
        return checked, issues
    checked.append(EVAL_FILE)
    try:
        data = yaml.safe_load(eval_path.read_text())
        if not isinstance(data, dict):
            raise ValueError("expected a YAML mapping at the top level")
        config = EvaluationConfig.model_validate(data)
    except (ValidationError, ValueError, yaml.YAMLError) as exc:
        return checked, issues + _issues(EVAL_FILE, exc)

    before = len(issues)
    for dataset in sorted({s.dataset for s in config.suites.values()}):
        rel = (Path(EVAL_FILE).parent / dataset).as_posix()
        if not (root / rel).exists():
            issues.append(Issue(EVAL_FILE, "suites", f"dataset not found: {rel}"))
            continue
        checked.append(rel)
        try:
            load_dataset(root / rel)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            issues += _issues(rel, exc)
    if len(issues) == before:  # cross-file rules (e.g. scored suites need assertions) run on valid datasets only
        try:
            load_evaluation_config(eval_path)
        except ValueError as exc:
            issues += _issues(EVAL_FILE, exc)
    return checked, issues
