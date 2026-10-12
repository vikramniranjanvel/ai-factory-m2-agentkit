"""Project settings published by the platform repo to SSM Parameter Store.

Layout: `<prefix>/<env>/projects/<project>/<setting>`. There are no environment-wide settings: the platform writes one
complete record per project and environment, only for the environments the project is registered for, and the
project's deploy role can read that path and nothing else. The prefix comes from the `AGENTKIT_SSM_PREFIX` environment
variable (default `/agentkit`), so moving an environment to another account is a matter of pointing the prefix at that
account.
"""

from __future__ import annotations

import json
import os
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

PREFIX_ENV_VAR = "AGENTKIT_SSM_PREFIX"
DEFAULT_PREFIX = "/agentkit"
Environment = Literal["dev", "test", "prod"]
ENVIRONMENTS: tuple[str, ...] = ("dev", "test", "prod")
# The project name is part of a role name and an SSM path, so it is restricted to a safe, short form. The platform
# registry applies the same rule.
PROJECT_PATTERN = r"^[a-z][a-z0-9-]{0,39}$"


class SettingsError(RuntimeError):
    """Settings could not be read from SSM or are incomplete."""


class McpGrant(BaseModel):
    """Tools of one MCP server that the platform lets this project use in this environment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    server: str
    tools: list[str]
    gateway_arn: Annotated[str, StringConstraints(pattern=r"^arn:aws:bedrock-agentcore:.+:gateway/.+")] | None = Field(
        default=None, description="Gateway serving this server. Set by the platform catalogue; agentkit never picks it."
    )


class Settings(BaseModel):
    """What AgentKit knows about one project in one environment (`resource_prefix` is SSM key `resource-prefix`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    account: Annotated[str, StringConstraints(pattern=r"^\d{12}$")] = Field(description="AWS account id.")
    region: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[a-z]+)+-\d$")] = Field(description="AWS region.")
    resource_prefix: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{0,7}$")] = Field(
        description="Prefix of every deployed resource: harnesses are named `<resource_prefix>_<agent name>`."
    )
    eval_state_machine_arn: Annotated[str, StringConstraints(pattern=r"^arn:aws:states:.+")] | None = Field(
        default=None, description="Step Functions state machine that runs evaluations. Needed by `evaluate`."
    )
    eval_results_bucket: Annotated[str, StringConstraints(min_length=3)] | None = Field(
        default=None, description="S3 bucket for evaluation results. Needed by `evaluate`."
    )
    mlflow_tracking_uri: Annotated[str, StringConstraints(pattern=r"^https?://.+")] | None = Field(
        default=None, description="MLflow tracking server. Needed by `evaluate`."
    )
    mcp_grants: list[McpGrant] | None = Field(
        default=None,
        description="MCP tools the platform grants this project here, each with the gateway that serves it "
        "(SSM `mcp/grants`, JSON). Needed when agent.yaml declares tools.",
    )

    @field_validator("mcp_grants", mode="before")
    @classmethod
    def _parse_grants(cls, value: Any) -> Any:
        """SSM stores the grants as a JSON string."""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"not valid JSON: {exc}") from exc
        return value

    def require(self, *names: str) -> None:
        """Raise SettingsError naming the SSM keys for any of the given optional settings that are not set."""
        missing = [ssm_key(n) for n in names if getattr(self, n) is None]
        if missing:
            raise SettingsError(f"missing settings in SSM: {', '.join(missing)}")


def ssm_key(field: str) -> str:
    """Field name to SSM key: eval_state_machine_arn -> eval/state-machine-arn, mcp_grants -> mcp/grants."""
    head, _, rest = field.partition("_")
    return f"{head}/{rest.replace('_', '-')}" if head in ("eval", "mcp") else field.replace("_", "-")


KEY_TO_FIELD = {ssm_key(f): f for f in Settings.model_fields}


def deploy_role_arn(account: str, env: str, project: str) -> str:
    """The role AgentKit assumes to deploy `project` to `env`. The platform creates it under this name, only for
    environments the project is registered for, so it needs no lookup: assuming it is the access check."""
    return f"arn:aws:iam::{account}:role/agentkit-{env}-{project}-deploy"


def project_path(env: str, project: str, prefix: str | None = None) -> str:
    """SSM path of a project's record in an environment."""
    if env not in ENVIRONMENTS:
        raise SettingsError(f"unknown environment {env!r} (expected one of: {', '.join(ENVIRONMENTS)})")
    if not re.match(PROJECT_PATTERN, project):
        raise SettingsError(
            f"invalid project name {project!r}: lowercase letters, digits and hyphens, starting with a letter"
        )
    return f"{prefix.rstrip('/') if prefix else ssm_prefix()}/{env}/projects/{project}"


def ssm_prefix() -> str:
    prefix = os.environ.get(PREFIX_ENV_VAR, DEFAULT_PREFIX).rstrip("/")
    if not prefix.startswith("/"):
        raise SettingsError(f"{PREFIX_ENV_VAR} must start with '/': {prefix!r}")
    return prefix


def _parameters(client: Any, path: str) -> dict[str, str]:
    """Parameters under `path` as {relative key: value}. SecureStrings are not decrypted: secrets are read on demand."""
    try:
        pages = client.get_paginator("get_parameters_by_path").paginate(Path=path, Recursive=True, WithDecryption=False)
        return {p["Name"].removeprefix(path + "/"): p["Value"] for page in pages for p in page["Parameters"]}
    except Exception as exc:
        raise SettingsError(f"could not read SSM parameters under {path}: {exc}") from exc


def _client(client: Any | None) -> Any:
    if client is not None:
        return client
    import boto3

    return boto3.client("ssm")


def load_settings(env: str, project: str, client: Any | None = None, prefix: str | None = None) -> Settings:
    """Read `project`'s settings in `env` from SSM. Unknown keys are ignored; invalid or missing ones raise."""
    path = project_path(env, project, prefix)
    found = _parameters(_client(client), path)
    if not found:
        raise SettingsError(
            f"no settings for project '{project}' in {env} under {path}. Is '{project}' registered for {env} in the "
            f"platform registry (its targets must include '{env}')?"
        )
    values = {KEY_TO_FIELD[k]: v for k, v in found.items() if k in KEY_TO_FIELD}
    try:
        return Settings.model_validate(values)
    except ValidationError as exc:
        problems = [f"{ssm_key(str(e['loc'][0]))} ({e['msg']})" for e in exc.errors()]
        raise SettingsError(f"invalid settings under {path}: {'; '.join(problems)}") from exc
