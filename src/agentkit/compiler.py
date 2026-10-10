"""Compile an agent definition (`agent.yaml`) into the native AgentCore `harness.json`.

Authored keys are snake_case and environment-free; the output is the native camelCase spec with the environment's
values filled in from SSM settings: the credential provider ARN and AgentKit's own tags. The spec keeps the plain agent
name; the deployed name `<resource_prefix>_<name>` is produced by the patched CDK template (see assemble.py), and
`harness_name` checks that it fits the native 40 character limit. The system prompt and skills are not inlined: they
are returned as files to copy into the harness directory (the prompt as `system-prompt.md`, which the native CLI
discovers).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentkit.schemas.agent_definition import AgentDefinition
from agentkit.settings import Settings

MAX_HARNESS_NAME_LENGTH = 40  # native HarnessNameSchema
PROMPT_FILE = "system-prompt.md"


class CompileError(ValueError):
    """The definition cannot be compiled for this environment."""


@dataclass(frozen=True)
class Copy:
    """A file or directory to copy into the harness directory."""

    source: Path
    dest: str  # relative to the harness directory


@dataclass(frozen=True)
class CompiledHarness:
    spec: dict  # contents of harness.json
    copies: tuple[Copy, ...]


def harness_name(settings: Settings, definition: AgentDefinition) -> str:
    name = f"{settings.resource_prefix}_{definition.name}"
    if len(name) > MAX_HARNESS_NAME_LENGTH:
        raise CompileError(
            f"deployed name '{name}' is {len(name)} characters; the limit is {MAX_HARNESS_NAME_LENGTH}. "
            f"Shorten the agent name (resource prefix '{settings.resource_prefix}' is added in front)."
        )
    return name


def credential_arn(settings: Settings, credential: str) -> str:
    return (
        f"arn:aws:bedrock-agentcore:{settings.region}:{settings.account}"
        f":token-vault/default/apikeycredentialprovider/{credential}"
    )


def _drop_none(data: dict) -> dict:
    return {k: v for k, v in data.items() if v is not None}


def _model(definition: AgentDefinition, settings: Settings) -> dict:
    m = definition.model
    return _drop_none(
        {
            "provider": m.provider,
            "modelId": m.model_id,
            "apiKeyArn": credential_arn(settings, m.credential) if m.credential else None,
            "apiFormat": m.api_format,
            "temperature": m.temperature,
            "topP": m.top_p,
            "topK": m.top_k,
            "maxTokens": m.max_tokens,
        }
    )


def _memory(definition: AgentDefinition) -> dict:
    memory = definition.memory
    if memory.mode == "disabled":
        return {"mode": "disabled"}
    return _drop_none(
        {
            "mode": "managed",
            "strategies": list(memory.strategies) if memory.strategies else None,
            "eventExpiryDuration": memory.event_expiry_days,
        }
    )


def _truncation(definition: AgentDefinition) -> dict | None:
    t = definition.truncation
    if t is None:
        return None
    if t.strategy == "sliding_window" and t.messages_count is not None:
        config = {"slidingWindow": {"messagesCount": t.messages_count}}
    elif t.strategy == "summarization" and (t.summary_ratio is not None or t.preserve_recent_messages is not None):
        config = {
            "summarization": _drop_none(
                {"summaryRatio": t.summary_ratio, "preserveRecentMessages": t.preserve_recent_messages}
            )
        }
    else:
        config = None
    return _drop_none({"strategy": t.strategy, "config": config})


def compile_harness(definition: AgentDefinition, settings: Settings, env: str, root: str | Path) -> CompiledHarness:
    """Build the native harness spec for `definition` in the given environment. `root` is the project directory."""
    harness_name(settings, definition)  # fail early if the deployed name would be too long
    root = Path(root)
    prompt = root / definition.prompt
    if not prompt.read_text().strip():
        raise CompileError(f"prompt file {definition.prompt} is empty")

    copies = [Copy(prompt, PROMPT_FILE)]
    skills = []
    for skill in definition.skills:
        dest = skill.path
        copies.append(Copy(root / skill.path, dest))
        skills.append({"path": dest})

    spec = _drop_none(
        {
            "name": definition.name,
            "model": _model(definition, settings),
            "tools": [],
            "skills": skills,
            "memory": _memory(definition),
            "maxIterations": definition.limits.max_iterations,
            "maxTokens": definition.limits.max_tokens,
            "timeoutSeconds": definition.limits.timeout_seconds,
            "truncation": _truncation(definition),
            "tags": {**definition.tags, "agentkit:environment": env, "agentkit:managed-by": "agentkit"},
        }
    )
    return CompiledHarness(spec=spec, copies=tuple(copies))
