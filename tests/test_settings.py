import pytest

from agentkit.settings import KEY_TO_FIELD, SettingsError, load_project_settings, load_settings, ssm_key, ssm_prefix

CORE = {
    "account": "167769274046",
    "region": "us-east-1",
    "resource-prefix": "dev",
    "deploy-role-arn": "arn:aws:iam::167769274046:role/agentkit-deploy-dev",
}


class FakeSsm:
    """Minimal stand-in for the boto3 SSM client: serves get_parameters_by_path in two pages."""

    def __init__(self, params: dict[str, str]):
        self.params = params

    def get_paginator(self, name):
        assert name == "get_parameters_by_path"
        return self

    def paginate(self, Path, Recursive, WithDecryption):  # noqa: N803
        assert Recursive and WithDecryption
        items = [{"Name": k, "Value": v} for k, v in self.params.items() if k.startswith(Path + "/")]
        yield {"Parameters": items[:3]}
        yield {"Parameters": items[3:]}


def ssm(env="dev", **extra):
    return FakeSsm({f"/agentkit/{env}/{k}": v for k, v in {**CORE, **extra}.items()})


def test_key_mapping():
    assert ssm_key("deploy_role_arn") == "deploy-role-arn"
    assert ssm_key("eval_state_machine_arn") == "eval/state-machine-arn"
    assert ssm_key("mlflow_tracking_uri") == "mlflow-tracking-uri"
    assert KEY_TO_FIELD["eval/results-bucket"] == "eval_results_bucket"


def test_core_settings():
    s = load_settings("dev", ssm())
    assert (s.account, s.region, s.resource_prefix) == ("167769274046", "us-east-1", "dev")
    assert s.eval_state_machine_arn is None


def test_all_settings_and_unknown_keys_ignored():
    client = ssm(
        **{
            "eval/state-machine-arn": "arn:aws:states:us-east-1:167769274046:stateMachine:eval",
            "eval/results-bucket": "agentkit-dev-results",
            "mlflow-tracking-uri": "https://mlflow.example.com",
            "something-new": "x",
            "projects/supportTriage/foo": "bar",
        }
    )
    s = load_settings("dev", client)
    s.require("eval_state_machine_arn", "eval_results_bucket", "mlflow_tracking_uri")
    assert s.eval_results_bucket == "agentkit-dev-results"


def test_environments_are_separate():
    client = FakeSsm({**ssm().params, **ssm("test", **{"resource-prefix": "test"}).params})
    assert load_settings("test", client).resource_prefix == "test"
    assert load_settings("dev", client).resource_prefix == "dev"


def test_missing_core_setting_names_the_ssm_key():
    client = FakeSsm({k: v for k, v in ssm().params.items() if not k.endswith("deploy-role-arn")})
    with pytest.raises(SettingsError, match="deploy-role-arn"):
        load_settings("dev", client)


def test_invalid_value_names_the_ssm_key():
    with pytest.raises(SettingsError, match=r"account .*pattern"):
        load_settings("dev", ssm(account="abc"))


def test_require_optional():
    s = load_settings("dev", ssm())
    with pytest.raises(SettingsError, match="eval/state-machine-arn, mlflow-tracking-uri"):
        s.require("eval_state_machine_arn", "mlflow_tracking_uri")


def test_unknown_environment():
    with pytest.raises(SettingsError, match="unknown environment"):
        load_settings("staging", ssm())


def test_prefix_from_env_var(monkeypatch):
    monkeypatch.setenv("AGENTKIT_SSM_PREFIX", "/other/")
    assert ssm_prefix() == "/other"
    client = FakeSsm({f"/other/dev/{k}": v for k, v in CORE.items()})
    assert load_settings("dev", client).account == "167769274046"
    monkeypatch.setenv("AGENTKIT_SSM_PREFIX", "nope")
    with pytest.raises(SettingsError, match="must start with"):
        ssm_prefix()


def test_default_prefix(monkeypatch):
    monkeypatch.delenv("AGENTKIT_SSM_PREFIX", raising=False)
    assert ssm_prefix() == "/agentkit"


def test_project_settings():
    client = ssm(**{"projects/supportTriage/api-key-name": "openai-prod", "projects/other/x": "y"})
    assert load_project_settings("dev", "supportTriage", client) == {"api-key-name": "openai-prod"}


def test_ssm_failure_is_wrapped():
    class Broken:
        def get_paginator(self, name):
            raise RuntimeError("AccessDenied")

    with pytest.raises(SettingsError, match="AccessDenied"):
        load_settings("dev", Broken())
