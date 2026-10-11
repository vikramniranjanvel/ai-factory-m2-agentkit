"""Scaffold a new agent project: `agentkit init myAgent`.

The files come from `templates/agent/` in this package. Tokens like `__NAME__` are replaced; everything else is copied
as is. The result passes `agentkit validate` (a test keeps it that way) and carries `scripts/setup.sh`, which creates
the GitHub repository and environments, sets the variable, writes the platform registry entry and stores the model key.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agentkit.schemas.agent_definition import MAX_NAME_LENGTH
from agentkit.validate import validate_project

TEMPLATE = Path(__file__).parent / "templates" / "agent"
REPO_PREFIX = "ai-factory-m2-"
NAME_PATTERN = re.compile(rf"^[A-Za-z][A-Za-z0-9]{{0,{MAX_NAME_LENGTH - 1}}}$")
REPO_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
EXECUTABLE = {"scripts/setup.sh"}


class InitError(RuntimeError):
    """The project could not be scaffolded."""


def slug(name: str) -> str:
    """`supportTriage` -> `support-triage`."""
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def init_project(
    name: str,
    directory: str | Path | None = None,
    *,
    description: str | None = None,
    provider: str = "open_ai",
    model_id: str = "gpt-6-luna",
    credential: str = "openai-key",
    repo: str | None = None,
) -> Path:
    """Write the template into `directory` (default `./<slug>`) and return it. The directory must be empty or new."""
    if not NAME_PATTERN.match(name):
        raise InitError(
            f"'{name}' is not a valid agent name: letters and digits only, starting with a letter, "
            f"at most {MAX_NAME_LENGTH} characters (for example supportTriage)"
        )
    repo = repo or REPO_PREFIX + slug(name)
    if not REPO_PATTERN.match(repo):
        raise InitError(f"'{repo}' is not a valid repository name")
    target = Path(directory) if directory else Path(slug(name))
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise InitError(f"{target} already exists and is not empty")

    bedrock = provider == "bedrock"
    values = {
        "NAME": name,
        "DESCRIPTION": description or f"TODO: describe what {name} does.",
        "PROVIDER": provider,
        "MODEL_ID": model_id,
        "CREDENTIAL": "" if bedrock else credential,
        "REPO": repo,
        "SLUG": slug(name),
    }
    for source in sorted(p for p in TEMPLATE.rglob("*") if p.is_file()):
        relative = source.relative_to(TEMPLATE)
        text = source.read_text()
        if source.suffix == ".yaml" and relative.parts[0] != ".github":
            # YAML scalars are written as JSON strings, so a colon or quote in a description cannot break the file.
            text = _fill(text, {k: json.dumps(v) for k, v in values.items()})
            if bedrock:  # bedrock uses the execution role: no credential
                text = re.sub(r"^ +credential: .*\n", "", text, flags=re.M)
        else:
            text = _fill(text, values)
        out = target / relative
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        if relative.as_posix() in EXECUTABLE:
            out.chmod(0o755)

    _, issues = validate_project(target)
    if issues:  # a bad --provider or --model-id, not a template fault
        raise InitError("the scaffold is not valid:\n" + "\n".join(str(i) for i in issues))
    return target


def _fill(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace(f"__{key}__", value)
    return text
