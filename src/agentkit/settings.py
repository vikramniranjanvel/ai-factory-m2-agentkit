"""Environment settings published by the platform repo to SSM Parameter Store.

Layout (decision 3 of the implementation plan): `<prefix>/<env>/<setting>` and
`<prefix>/<env>/projects/<project>/<setting>`. The prefix comes from the `AGENTKIT_SSM_PREFIX` environment variable
(default `/agentkit`), so moving an environment to another account is a matter of pointing the prefix at that account.
"""

from __future__ import annotations

import os
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

PREFIX_ENV_VAR = "AGENTKIT_SSM_PREFIX"
DEFAULT_PREFIX = "/agentkit"
Environment = Literal["dev", "test", "prod"]
ENVIRONMENTS: tuple[str, ...] = ("dev", "test", "prod")


class SettingsError(RuntimeError):
    """Settings could not be read from SSM or are incomplete."""


class Settings(BaseModel):
    """What AgentKit knows about one environment. Field `deploy_role_arn` is SSM key `deploy-role-arn`, and so on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    account: Annotated[str, StringConstraints(pattern=r"^\d{12}$")] = Field(description="AWS account id.")
    region: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[a-z]+)+-\d$")] = Field(description="AWS region.")
    resource_prefix: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{0,7}$")] = Field(
        description="Prefix of every deployed resource: harnesses are named `<resource_prefix>_<agent name>`."
    )
    deploy_role_arn: Annotated[str, StringConstraints(pattern=r"^arn:aws:iam::\d{12}:role/.+$")] = Field(
        description="IAM role AgentKit assumes to deploy."
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

    def require(self, *names: str) -> None:
        """Raise SettingsError naming the SSM keys for any of the given optional settings that are not set."""
        missing = [ssm_key(n) for n in names if getattr(self, n) is None]
        if missing:
            raise SettingsError(f"missing settings in SSM: {', '.join(missing)}")


def ssm_key(field: str) -> str:
    """Field name to SSM key: eval_state_machine_arn -> eval/state-machine-arn."""
    head, _, rest = field.partition("_")
    return f"eval/{rest.replace('_', '-')}" if head == "eval" else field.replace("_", "-")


KEY_TO_FIELD = {ssm_key(f): f for f in Settings.model_fields}


def ssm_prefix() -> str:
    prefix = os.environ.get(PREFIX_ENV_VAR, DEFAULT_PREFIX).rstrip("/")
    if not prefix.startswith("/"):
        raise SettingsError(f"{PREFIX_ENV_VAR} must start with '/': {prefix!r}")
    return prefix


def _parameters(client: Any, path: str) -> dict[str, str]:
    """Parameters under `path` as {relative key: value}."""
    try:
        pages = client.get_paginator("get_parameters_by_path").paginate(Path=path, Recursive=True, WithDecryption=True)
        return {p["Name"].removeprefix(path + "/"): p["Value"] for page in pages for p in page["Parameters"]}
    except Exception as exc:
        raise SettingsError(f"could not read SSM parameters under {path}: {exc}") from exc


def _client(client: Any | None) -> Any:
    if client is not None:
        return client
    import boto3

    return boto3.client("ssm")


def load_settings(env: str, client: Any | None = None, prefix: str | None = None) -> Settings:
    """Read the environment's settings from SSM. Unknown keys are ignored; invalid or missing ones raise."""
    if env not in ENVIRONMENTS:
        raise SettingsError(f"unknown environment {env!r} (expected one of: {', '.join(ENVIRONMENTS)})")
    path = f"{prefix.rstrip('/') if prefix else ssm_prefix()}/{env}"
    found = _parameters(_client(client), path)
    values = {KEY_TO_FIELD[k]: v for k, v in found.items() if k in KEY_TO_FIELD}
    try:
        return Settings.model_validate(values)
    except ValidationError as exc:
        problems = [f"{ssm_key(str(e['loc'][0]))} ({e['msg']})" for e in exc.errors()]
        raise SettingsError(f"invalid settings under {path}: {'; '.join(problems)}") from exc


def load_project_settings(
    env: str, project: str, client: Any | None = None, prefix: str | None = None
) -> dict[str, str]:
    """Free-form per-project settings under `<prefix>/<env>/projects/<project>/`."""
    if env not in ENVIRONMENTS:
        raise SettingsError(f"unknown environment {env!r} (expected one of: {', '.join(ENVIRONMENTS)})")
    path = f"{prefix.rstrip('/') if prefix else ssm_prefix()}/{env}/projects/{project}"
    return _parameters(_client(client), path)
