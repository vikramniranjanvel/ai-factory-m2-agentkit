import shutil
from pathlib import Path

import pytest

from agentkit.deploy import DeployError, credential_env_var, deploy_project, read_credential
from agentkit.settings import SettingsError
from test_assemble import FIXTURE, fake_native
from test_settings import ssm


class SsmWithSecret:
    """FakeSsm plus get_parameter for the decrypted API key."""

    class exceptions:  # noqa: N801
        class ParameterNotFound(Exception):
            pass

    def __init__(self, secrets=None, **kwargs):
        self.inner = ssm(**kwargs)
        self.secrets = secrets if secrets is not None else {"/agentkit/dev/credentials/openai-key": "sk-test"}

    def get_paginator(self, name):
        return self.inner.get_paginator(name)

    def get_parameter(self, Name, WithDecryption):  # noqa: N803
        assert WithDecryption
        if Name not in self.secrets:
            raise self.exceptions.ParameterNotFound(Name)
        return {"Parameter": {"Value": self.secrets[Name]}}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    shutil.copytree(FIXTURE, root)
    return root


def test_env_var_name():
    assert credential_env_var("openai-key") == "AGENTCORE_CREDENTIAL_OPENAI_KEY"


def test_read_credential_missing():
    with pytest.raises(DeployError, match="/agentkit/dev/credentials/nope"):
        read_credential("dev", "nope", SsmWithSecret())


def test_deploy_runs_steps_and_cleans_up(project, tmp_path):
    calls = []
    seen = {}

    def runner(cmd, cwd, env):
        calls.append((cmd, cwd.name))
        if cmd[0] == "agentcore":
            seen["env_file"] = (cwd / "agentcore" / ".env.local").read_text()
            seen["project"] = cwd
            seen["targets"] = (cwd / "agentcore" / "aws-targets.json").read_text()

    deploy_project(
        project, "dev", use_role=False, ssm_client=SsmWithSecret(), runner=runner, native_root=fake_native(tmp_path)
    )
    assert [c[0] for c in calls] == [
        ["npm", "install", "--no-audit", "--no-fund"],
        ["agentcore", "deploy", "--target", "dev", "-y"],
    ]
    assert calls[0][1] == "cdk"
    assert seen["env_file"] == "AGENTCORE_CREDENTIAL_OPENAI_KEY=sk-test\n"
    assert '"name": "dev"' in seen["targets"]
    assert not seen["project"].parent.exists()  # temp dir removed, with the key in it


def test_deploy_cleans_up_when_a_step_fails(project, tmp_path):
    seen = {}

    def runner(cmd, cwd, env):
        seen["dir"] = cwd
        raise DeployError("boom")

    with pytest.raises(DeployError, match="boom"):
        deploy_project(
            project, "dev", use_role=False, ssm_client=SsmWithSecret(), runner=runner, native_root=fake_native(tmp_path)
        )
    assert not Path(seen["dir"]).exists()


def test_invalid_project_stops_before_aws(project):
    (project / "agent.yaml").write_text("schema_version: 1\n")
    with pytest.raises(DeployError, match="not valid"):
        deploy_project(project, "dev", use_role=False, ssm_client=object(), runner=lambda *a: None)


def test_missing_settings(project):
    client = SsmWithSecret()
    client.inner.params.clear()
    with pytest.raises(SettingsError):
        deploy_project(project, "dev", use_role=False, ssm_client=client, runner=lambda *a: None)


def test_missing_api_key(project, tmp_path):
    with pytest.raises(DeployError, match="API key not found"):
        deploy_project(
            project,
            "dev",
            use_role=False,
            ssm_client=SsmWithSecret(secrets={}),
            runner=lambda *a: None,
            native_root=fake_native(tmp_path),
        )
