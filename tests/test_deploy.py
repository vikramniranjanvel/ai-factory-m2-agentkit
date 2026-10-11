import shutil
from pathlib import Path

import pytest

from agentkit.deploy import DeployError, assume_role_env, credential_env_var, deploy_project, read_credential
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
        self.secrets = (
            secrets
            if secrets is not None
            else {"/agentkit/dev/projects/support-triage/credentials/openai-key": "sk-test"}
        )

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
    with pytest.raises(DeployError, match="/agentkit/dev/projects/support-triage/credentials/nope"):
        read_credential("dev", "support-triage", "nope", SsmWithSecret())


def test_credentials_are_read_from_the_projects_own_path():
    client = SsmWithSecret(secrets={"/agentkit/dev/projects/other/credentials/openai-key": "not-mine"})
    with pytest.raises(DeployError, match="API key not found"):
        read_credential("dev", "support-triage", "openai-key", client)


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


class Sts:
    """STS stand-in: reports the caller's account, and allows assuming only the roles in `allowed`."""

    def __init__(self, allowed=(), account="167769274046"):
        self.allowed, self.account, self.assumed = set(allowed), account, []

    def get_caller_identity(self):
        return {"Account": self.account}

    def assume_role(self, RoleArn, RoleSessionName):  # noqa: N803
        from botocore.exceptions import ClientError

        self.assumed.append(RoleArn)
        if RoleArn not in self.allowed:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "not authorized"}}, "AssumeRole")
        return {"Credentials": {"AccessKeyId": "AK", "SecretAccessKey": "SK", "SessionToken": "ST"}}


def test_deploy_assumes_the_projects_own_role_and_uses_its_credentials(project, tmp_path, monkeypatch):
    role = "arn:aws:iam::167769274046:role/agentkit-dev-support-triage-deploy"
    seen = {}

    class Boto:
        def client(self, service, **kwargs):
            seen["client"] = (service, kwargs)
            return SsmWithSecret()

    monkeypatch.setattr("boto3.client", Boto().client)
    sts = Sts(allowed=[role])
    deploy_project(
        project,
        "dev",
        sts_client=sts,
        region="us-east-1",
        runner=lambda cmd, cwd, env: seen.setdefault("env", dict(env)),
        native_root=fake_native(tmp_path),
    )
    assert sts.assumed == [role]
    assert seen["client"][0] == "ssm" and seen["client"][1]["aws_session_token"] == "ST"  # settings read as the role
    assert seen["env"]["AWS_ACCESS_KEY_ID"] == "AK"  # and the deploy runs as the role


def test_a_project_not_registered_for_the_environment_is_refused_before_anything_is_read(project, monkeypatch):
    def no_aws(*a, **k):
        raise AssertionError("must not call AWS")

    monkeypatch.setattr("boto3.client", no_aws)
    sts = Sts(allowed=["arn:aws:iam::167769274046:role/agentkit-dev-support-triage-deploy"])
    ran = []
    with pytest.raises(DeployError, match=r"cannot assume .*agentkit-test-support-triage-deploy.*not registered"):
        deploy_project(project, "test", sts_client=sts, region="us-east-1", runner=lambda *a: ran.append(a))
    assert ran == []


def test_a_caller_who_is_not_trusted_cannot_deploy(project):
    with pytest.raises(DeployError, match="only CI deploys to test and prod"):
        deploy_project(project, "prod", sts_client=Sts(), region="us-east-1", runner=lambda *a: None)


def test_another_projects_role_is_never_tried(project):
    sts = Sts()
    with pytest.raises(DeployError):
        deploy_project(project, "dev", sts_client=sts, region="us-east-1", runner=lambda *a: None)
    assert sts.assumed == ["arn:aws:iam::167769274046:role/agentkit-dev-support-triage-deploy"]


def test_other_sts_errors_are_not_reported_as_a_missing_registration():
    from botocore.exceptions import ClientError

    class Throttled(Sts):
        def assume_role(self, RoleArn, RoleSessionName):  # noqa: N803
            raise ClientError({"Error": {"Code": "Throttling", "Message": "slow down"}}, "AssumeRole")

    with pytest.raises(ClientError):
        assume_role_env("arn:aws:iam::1:role/x", "us-east-1", sts_client=Throttled())
