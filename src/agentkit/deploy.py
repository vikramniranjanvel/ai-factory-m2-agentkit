"""Deploy an agent project to an environment: `agentkit deploy --env dev`.

Steps: validate, assume the project's deploy role (`agentkit-<env>-<project>-deploy`; its trust policy is the access
check, so a project not registered for the environment, or a caller who is not trusted, stops here), load the project's
settings from SSM with that role, assemble the temporary native project, write `.env.local` from the SSM SecureString,
`npm install` in the CDK app, `agentcore deploy`, delete the temp project.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agentkit.assemble import assemble_project
from agentkit.schemas.agent_definition import load_agent_definition
from agentkit.settings import deploy_role_arn, load_settings, project_path, ssm_prefix
from agentkit.validate import validate_project

Runner = Callable[[list[str], Path, Mapping[str, str]], None]


class DeployError(RuntimeError):
    """The deploy could not be completed."""


def credential_env_var(credential: str) -> str:
    """Variable the native CLI reads a credential's API key from: openai-key -> AGENTCORE_CREDENTIAL_OPENAI_KEY."""
    return "AGENTCORE_CREDENTIAL_" + re.sub(r"[^A-Za-z0-9]", "_", credential).upper()


def read_credential(env: str, project: str, credential: str, client: Any, prefix: str | None = None) -> str:
    """Decrypt `/agentkit/<env>/projects/<project>/credentials/<credential>`: the only place a secret is read."""
    name = f"{project_path(env, project, prefix or ssm_prefix())}/credentials/{credential}"
    try:
        return client.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    except client.exceptions.ParameterNotFound as exc:
        raise DeployError(f"API key not found in SSM: {name}") from exc


def assume_role_env(
    role_arn: str, region: str, session_name: str = "agentkit-deploy", sts_client: Any | None = None
) -> dict[str, str]:
    """Environment variables carrying temporary credentials for `role_arn`."""
    import boto3
    from botocore.exceptions import ClientError

    sts = sts_client or boto3.client("sts", region_name=region)
    try:
        creds = sts.assume_role(RoleArn=role_arn, RoleSessionName=session_name)["Credentials"]
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("AccessDenied", "AccessDeniedException"):
            raise DeployError(
                f"cannot assume {role_arn}: either the project is not registered for this environment in the "
                "platform registry, or your identity is not trusted to deploy there (only CI deploys to test and prod)"
            ) from exc
        raise
    return {
        "AWS_ACCESS_KEY_ID": creds["AccessKeyId"],
        "AWS_SECRET_ACCESS_KEY": creds["SecretAccessKey"],
        "AWS_SESSION_TOKEN": creds["SessionToken"],
        "AWS_REGION": region,
        "AWS_DEFAULT_REGION": region,
    }


def run_command(cmd: list[str], cwd: Path, env: Mapping[str, str]) -> None:
    result = subprocess.run(cmd, cwd=cwd, env={**os.environ, **env})
    if result.returncode != 0:
        raise DeployError(f"`{' '.join(cmd)}` failed with exit code {result.returncode}")


def deploy_project(
    root: str | Path,
    env: str,
    *,
    use_role: bool = True,
    ssm_client: Any | None = None,
    sts_client: Any | None = None,
    region: str | None = None,
    runner: Runner = run_command,
    native_root: Path | None = None,
    keep: bool = False,
) -> None:
    """Deploy the project in `root` to `env`. Raises DeployError (or a settings/assemble error) on failure.

    With `use_role` (the default) the project's deploy role is assumed first and everything else runs as that role.
    CI passes `use_role=False`: its OIDC session already is the role.
    """
    root = Path(root)
    _, issues = validate_project(root)
    if issues:
        raise DeployError("the project is not valid:\n" + "\n".join(str(i) for i in issues))
    definition = load_agent_definition(root / "agent.yaml")
    project_name = definition.project

    aws_env: dict[str, str] = {}
    if use_role:
        region = region or _default_region()
        account = _caller_account(sts_client)
        aws_env = assume_role_env(deploy_role_arn(account, env, project_name), region, sts_client=sts_client)
        ssm_client = _client_with(aws_env, region)
    elif ssm_client is None:
        import boto3

        ssm_client = boto3.client("ssm")
    settings = load_settings(env, project_name, client=ssm_client)

    tmp = Path(tempfile.mkdtemp(prefix=f"agentkit-{env}-"))
    try:
        project = assemble_project(definition, settings, env, root, tmp / "project", native_root=native_root)
        if definition.model.credential:
            key = read_credential(env, project_name, definition.model.credential, ssm_client)
            env_file = project / "agentcore" / ".env.local"
            env_file.write_text(f"{credential_env_var(definition.model.credential)}={key}\n")
            env_file.chmod(0o600)
        print(
            f"deploying {definition.name} ({project_name}) to {env} ({settings.account}, {settings.region})",
            file=sys.stderr,
        )
        runner(["npm", "install", "--no-audit", "--no-fund"], project / "agentcore" / "cdk", aws_env)
        runner(["agentcore", "deploy", "--target", env, "-y"], project, aws_env)
    finally:
        if keep:
            print(f"temporary project kept at {tmp}", file=sys.stderr)
        else:
            _remove(tmp)


def _default_region() -> str:
    import boto3

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or boto3.session.Session().region_name
    if not region:
        raise DeployError("no AWS region: set AWS_REGION (or a region in your AWS profile)")
    return region


def _caller_account(sts_client: Any | None) -> str:
    import boto3

    return (sts_client or boto3.client("sts")).get_caller_identity()["Account"]


def _client_with(aws_env: Mapping[str, str], region: str) -> Any:
    import boto3

    return boto3.client(
        "ssm",
        region_name=region,
        aws_access_key_id=aws_env["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=aws_env["AWS_SECRET_ACCESS_KEY"],
        aws_session_token=aws_env["AWS_SESSION_TOKEN"],
    )


def _remove(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
