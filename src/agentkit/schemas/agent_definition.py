"""Agent definition: what a project author may set about an agent (`agent.yaml`).

A curated subset of the native AgentCore HarnessSpec. Environment-specific values (account, region, credential ARNs,
resource prefix, execution role, network) are never authored here; AgentKit resolves them from SSM and compiles this
file into a native harness.json at deploy time. Keys are snake_case; the compiler maps them to the native camelCase.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from agentkit.settings import PROJECT_PATTERN

# Native harness names allow 40 chars and the deployed name is `<prefix>_<name>`; the combined length is checked at
# deploy time, the cap here leaves room for a prefix such as `test_`.
MAX_NAME_LENGTH = 32
RESERVED_TAG_PREFIXES = ("agentcore:", "agentkit:", "aws:")
TAG_CHARS = re.compile(r"^[\w\s.:/=+\-@]*$")  # the characters AWS tagging (and the native schema) allow


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _project_relative(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError(f"must be a relative path inside the project (no '..', no absolute path): {value!r}")
    return value


class Model(_Strict):
    """The foundation model the agent runs on."""

    provider: Literal["bedrock", "open_ai", "gemini", "lite_llm"] = Field(
        description=(
            "Model provider. `bedrock` uses the environment's execution role; every other provider needs a "
            "`credential`."
        ),
        examples=["bedrock"],
    )
    model_id: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Provider-specific model identifier.",
        examples=["us.anthropic.claude-sonnet-4-5-20250929-v1:0", "gpt-4.1"],
    )
    credential: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")] | None = Field(
        default=None,
        description=(
            "Name of a credential registered in the environment (AgentKit resolves it to a provider ARN via SSM). "
            "Required for every provider except bedrock."
        ),
        examples=["openai-prod"],
    )
    api_format: Literal["converse_stream", "responses", "chat_completions"] | None = Field(
        default=None,
        description=(
            "Wire format for the provider. Only supported for bedrock (converse_stream, responses, chat_completions) "
            "and open_ai (responses, chat_completions)."
        ),
    )
    temperature: Annotated[float, Field(ge=0, le=2)] | None = Field(
        default=None, description="Sampling temperature, 0 to 2. Lower is more deterministic.", examples=[0.2]
    )
    top_p: Annotated[float, Field(ge=0, le=1)] | None = Field(
        default=None, description="Nucleus sampling probability mass, 0 to 1.", examples=[0.9]
    )
    top_k: Annotated[int, Field(ge=0, le=500)] | None = Field(
        default=None,
        description="Sample only from the k most likely tokens, 0 to 500. Gemini models only.",
        examples=[250],
    )
    max_tokens: Annotated[int, Field(ge=1)] | None = Field(
        default=None, description="Maximum tokens the model may generate per response.", examples=[4096]
    )

    @model_validator(mode="after")
    def _provider_rules(self) -> Model:
        if self.provider == "bedrock" and self.credential is not None:
            raise ValueError("bedrock models use the execution role; remove 'credential'")
        if self.provider != "bedrock" and self.credential is None:
            raise ValueError(f"provider '{self.provider}' requires 'credential'")
        if self.top_k is not None and self.provider != "gemini":
            raise ValueError("'top_k' is only supported for provider 'gemini'")
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

    path: str = Field(
        description="Path to the skill, relative to agent.yaml. No `..` and no absolute paths.",
        examples=["skills/escalation"],
    )

    _check_path = field_validator("path")(_project_relative)


# Catalogue ids and tool names are lowercase and explicit. `*` can never match, so wildcards are rejected by pattern.
SERVER_ID_PATTERN = r"^[a-z][a-z0-9-]{0,39}$"
TOOL_NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"


class ToolRequest(_Strict):
    """Tools the agent needs from one MCP server. A request: the platform registry decides whether it is granted."""

    server: Annotated[str, StringConstraints(pattern=SERVER_ID_PATTERN)] = Field(
        description="Id of an MCP server in the platform catalogue (`mcp/servers/<id>.json`).",
        examples=["kb-search"],
    )
    tools: Annotated[
        list[Annotated[str, StringConstraints(pattern=TOOL_NAME_PATTERN)]], Field(min_length=1, max_length=50)
    ] = Field(
        description="Tool names on that server, listed explicitly. Wildcards are not allowed.",
        examples=[["search", "get_doc"]],
    )

    @field_validator("tools")
    @classmethod
    def _unique_tools(cls, value: list[str]) -> list[str]:
        duplicates = sorted({t for t in value if value.count(t) > 1})
        if duplicates:
            raise ValueError(f"tools listed more than once: {', '.join(duplicates)}")
        return value


class MemoryDisabled(_Strict):
    """No memory: every conversation starts empty."""

    mode: Literal["disabled"] = Field(description="Memory off.")


class MemoryManaged(_Strict):
    """AgentCore-managed memory."""

    mode: Literal["managed"] = Field(description="Memory managed by AgentCore.")
    strategies: (
        Annotated[
            list[Literal["SEMANTIC", "SUMMARIZATION", "USER_PREFERENCE", "EPISODIC"]], Field(min_length=1, max_length=4)
        ]
        | None
    ) = Field(
        default=None,
        description="Long-term memory strategies to enable. Omit to use the AgentCore default.",
        examples=[["SEMANTIC", "SUMMARIZATION"]],
    )
    event_expiry_days: Annotated[int, Field(ge=3, le=365)] | None = Field(
        default=None, description="Days before memory events expire, 3 to 365.", examples=[30]
    )


Memory = Annotated[MemoryDisabled | MemoryManaged, Field(discriminator="mode")]


class Limits(_Strict):
    """Runtime limits for one invocation. Omitted values use the AgentCore defaults."""

    max_iterations: Annotated[int, Field(ge=1)] | None = Field(
        default=None, description="Maximum reasoning/tool-use loop iterations.", examples=[10]
    )
    timeout_seconds: Annotated[int, Field(ge=1)] | None = Field(
        default=None, description="Wall-clock timeout for one invocation, in seconds.", examples=[120]
    )
    max_tokens: Annotated[int, Field(ge=1)] | None = Field(
        default=None, description="Maximum total tokens generated across one invocation.", examples=[8192]
    )


class Truncation(_Strict):
    """How conversation history is shortened when it grows too long."""

    strategy: Literal["none", "sliding_window", "summarization"] = Field(
        description=(
            "`none` keeps everything; `sliding_window` keeps the last N messages; `summarization` summarizes older "
            "messages."
        )
    )
    messages_count: Annotated[int, Field(ge=1)] | None = Field(
        default=None, description="Messages to keep. Only for `sliding_window`.", examples=[20]
    )
    summary_ratio: Annotated[float, Field(gt=0, lt=1)] | None = Field(
        default=None,
        description="Fraction of history to summarize, between 0 and 1. Only for `summarization`.",
        examples=[0.3],
    )
    preserve_recent_messages: Annotated[int, Field(ge=0)] | None = Field(
        default=None, description="Most recent messages kept verbatim. Only for `summarization`.", examples=[5]
    )

    @model_validator(mode="after")
    def _strategy_rules(self) -> Truncation:
        window, summary = {"messages_count"}, {"summary_ratio", "preserve_recent_messages"}
        given = {f for f in window | summary if getattr(self, f) is not None}
        allowed = {"sliding_window": window, "summarization": summary}.get(self.strategy, set())
        if given - allowed:
            raise ValueError(f"{sorted(given - allowed)} not valid for strategy '{self.strategy}'")
        return self


class AgentDefinition(_Strict):
    """An agent as the project author describes it (`agent.yaml`)."""

    schema_version: Literal[1] = Field(description="Version of this file format. Always 1 for now.")
    project: Annotated[str, StringConstraints(pattern=PROJECT_PATTERN)] = Field(
        description=(
            "Name of this project in the platform registry (`registry/<project>.json`): lowercase letters, digits and "
            "hyphens, up to 40 characters. AgentKit deploys with the role `agentkit-<env>-<project>-deploy` and reads "
            "`/agentkit/<env>/projects/<project>/`, which exist only for the environments the project is registered "
            "for."
        ),
        examples=["support-triage"],
    )
    name: Annotated[str, StringConstraints(pattern=rf"^[A-Za-z][A-Za-z0-9]{{0,{MAX_NAME_LENGTH - 1}}}$")] = Field(
        description=(
            f"Agent name: a letter, then letters and digits, up to {MAX_NAME_LENGTH} characters (no underscores or "
            "hyphens). The deployed harness is named `<environment prefix>_<name>`."
        ),
        examples=["supportTriage"],
    )
    description: Annotated[str, StringConstraints(max_length=500)] | None = Field(
        default=None, description="What the agent does, up to 500 characters.", examples=["Triages support tickets."]
    )
    model: Model = Field(description="The foundation model and its sampling settings.")
    prompt: str = Field(
        default="prompts/system.md",
        description="Path to the system prompt, relative to agent.yaml.",
        examples=["prompts/system.md"],
    )
    skills: list[Skill] = Field(default=[], description="Skills available to the agent.")
    tools: list[ToolRequest] = Field(
        default=[],
        description=(
            "MCP tools the agent needs, per server. This is a request: the deploy fails if the platform registry "
            "does not grant every listed tool for the environment."
        ),
    )
    memory: Memory = Field(default=MemoryDisabled(mode="disabled"), description="Conversation memory. Off by default.")
    limits: Limits = Field(default=Limits(), description="Runtime limits for one invocation.")
    truncation: Truncation | None = Field(
        default=None, description="How to shorten long conversation history. Omit to use the AgentCore default."
    )
    tags: dict[str, Annotated[str, StringConstraints(max_length=256)]] = Field(
        default={},
        description="Resource tags (at most 40). Keys starting with `agentcore:`, `agentkit:` or `aws:` are reserved.",
        examples=[{"team": "support"}],
    )

    _check_prompt = field_validator("prompt")(_project_relative)

    @field_validator("tools")
    @classmethod
    def _one_entry_per_server(cls, value: list[ToolRequest]) -> list[ToolRequest]:
        servers = [t.server for t in value]
        duplicates = sorted({s for s in servers if servers.count(s) > 1})
        if duplicates:
            raise ValueError(f"server listed more than once: {', '.join(duplicates)}")
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
            if not TAG_CHARS.match(key) or not TAG_CHARS.match(value[key]):
                raise ValueError(f"tag {key!r}: only letters, digits, spaces and _ . : / = + - @ are allowed")
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
