"""Agent definition: what a project author may set about an agent (`agent.yaml`).

A curated subset of the native AgentCore HarnessSpec. Environment-specific values (account, region, credential ARNs,
resource prefix, execution role, network) are never authored here; AgentKit resolves them from SSM and compiles this
file into a native harness.json at deploy time. Keys are snake_case; the compiler maps them to the native camelCase.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

# Native harness names allow 40 chars and the deployed name is `<prefix>_<name>`; the combined length is checked at
# deploy time, the cap here leaves room for a prefix such as `test_`.
MAX_NAME_LENGTH = 32
RESERVED_TAG_PREFIXES = ("agentcore:", "agentkit:", "aws:")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _project_relative(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError(f"must be a relative path inside the project (no '..', no absolute path): {value!r}")
    return value


class Model(_Strict):
    provider: Literal["bedrock", "open_ai", "gemini", "lite_llm"]
    model_id: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
    # Name of a credential registered in the environment (resolved to a provider ARN via SSM). Required for every
    # provider except bedrock, which uses the execution role.
    credential: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")] | None = None
    api_format: Literal["converse_stream", "responses", "chat_completions"] | None = None
    temperature: Annotated[float, Field(ge=0, le=2)] | None = None
    top_p: Annotated[float, Field(ge=0, le=1)] | None = None
    top_k: Annotated[int, Field(ge=0, le=500)] | None = None
    max_tokens: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def _provider_rules(self) -> Model:
        if self.provider == "bedrock" and self.credential is not None:
            raise ValueError("bedrock models use the execution role; remove 'credential'")
        if self.provider != "bedrock" and self.credential is None:
            raise ValueError(f"provider '{self.provider}' requires 'credential'")
        if self.api_format is not None:
            allowed = {
                "bedrock": {"converse_stream", "responses", "chat_completions"},
                "open_ai": {"responses", "chat_completions"},
            }.get(self.provider)
            if allowed is None:
                raise ValueError(f"'api_format' is not supported for provider '{self.provider}'")
            if self.api_format not in allowed:
                raise ValueError(
                    f"api_format '{self.api_format}' is not valid for {self.provider} "
                    f"(allowed: {', '.join(sorted(allowed))})"
                )
        return self


class Skill(_Strict):
    """A skill kept in this project repository."""

    path: str

    _check_path = field_validator("path")(_project_relative)


class MemoryDisabled(_Strict):
    mode: Literal["disabled"]


class MemoryManaged(_Strict):
    mode: Literal["managed"]
    strategies: (
        Annotated[
            list[Literal["SEMANTIC", "SUMMARIZATION", "USER_PREFERENCE", "EPISODIC"]], Field(min_length=1, max_length=4)
        ]
        | None
    ) = None
    event_expiry_days: Annotated[int, Field(ge=3, le=365)] | None = None


Memory = Annotated[MemoryDisabled | MemoryManaged, Field(discriminator="mode")]


class Limits(_Strict):
    max_iterations: Annotated[int, Field(ge=1)] | None = None
    timeout_seconds: Annotated[int, Field(ge=1)] | None = None
    max_tokens: Annotated[int, Field(ge=1)] | None = None


class Truncation(_Strict):
    strategy: Literal["none", "sliding_window", "summarization"]
    messages_count: Annotated[int, Field(ge=1)] | None = None  # sliding_window only
    summary_ratio: Annotated[float, Field(gt=0, lt=1)] | None = None  # summarization only
    preserve_recent_messages: Annotated[int, Field(ge=0)] | None = None  # summarization only

    @model_validator(mode="after")
    def _strategy_rules(self) -> Truncation:
        window, summary = {"messages_count"}, {"summary_ratio", "preserve_recent_messages"}
        given = {f for f in window | summary if getattr(self, f) is not None}
        allowed = {"sliding_window": window, "summarization": summary}.get(self.strategy, set())
        if given - allowed:
            raise ValueError(f"{sorted(given - allowed)} not valid for strategy '{self.strategy}'")
        return self


class AgentDefinition(_Strict):
    schema_version: Literal[1]
    name: Annotated[str, StringConstraints(pattern=rf"^[A-Za-z][A-Za-z0-9]{{0,{MAX_NAME_LENGTH - 1}}}$")]
    description: Annotated[str, StringConstraints(max_length=500)] | None = None
    model: Model
    prompt: str = "prompts/system.md"
    skills: list[Skill] = []
    # Reserved: no tools or MCP gateway in this iteration (see implementation plan, decision 2).
    tools: list[dict] = []
    memory: Memory = MemoryDisabled(mode="disabled")
    limits: Limits = Limits()
    truncation: Truncation | None = None
    tags: dict[str, Annotated[str, StringConstraints(max_length=256)]] = {}

    _check_prompt = field_validator("prompt")(_project_relative)

    @field_validator("tools")
    @classmethod
    def _no_tools_yet(cls, value: list[dict]) -> list[dict]:
        if value:
            raise ValueError("tools are not supported in this iteration")
        return value

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 40:  # native max 50; leave room for tags added by AgentKit
            raise ValueError("at most 40 tags")
        for key in value:
            if not 1 <= len(key) <= 128:
                raise ValueError(f"tag key {key!r} must be 1-128 characters")
            if key.lower().startswith(RESERVED_TAG_PREFIXES):
                raise ValueError(f"tag key {key!r} uses a reserved prefix {RESERVED_TAG_PREFIXES}")
        return value


def load_agent_definition(path: str | Path) -> AgentDefinition:
    """Parse and validate an agent.yaml, and check that the files it references exist next to it."""
    path = Path(path)
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping at the top level")
    definition = AgentDefinition.model_validate(data)
    root = path.parent
    missing = [p for p in [definition.prompt, *(s.path for s in definition.skills)] if not (root / p).exists()]
    if missing:
        raise ValueError(f"{path}: referenced files not found: {', '.join(missing)}")
    return definition
