import pytest

from agentkit.settings import (
    KEY_TO_FIELD,
    SettingsError,
    deploy_role_arn,
    load_settings,
    project_path,
    ssm_key,
    ssm_prefix,
)

CORE = {
    "account": "167769274046",
    "region": "us-east-1",
    "resource-prefix": "dev",
}
PROJECT = "support-triage"


class FakeSsm:
    """Minimal stand-in for the boto3 SSM client: serves get_parameters_by_path in two pages."""

    def __init__(self, params: dict[str, str]):
        self.params = params

    def get_paginator(self, name):
        assert name == "get_parameters_by_path"
        return self

    def paginate(self, Path, Recursive, WithDecryption):  # noqa: N803
        assert Recursive and not WithDecryption  # secrets under credentials/ stay encrypted
        items = [{"Name": k, "Value": v} for k, v in self.params.items() if k.startswith(Path + "/")]
        yield {"Parameters": items[:3]}
        yield {"Parameters": items[3:]}


def ssm(env="dev", project=PROJECT, **extra):
    return FakeSsm({f"/agentkit/{env}/projects/{project}/{k}": v for k, v in {**CORE, **extra}.items()})


def test_key_mapping():
    assert ssm_key("resource_prefix") == "resource-prefix"
    assert ssm_key("eval_state_machine_arn") == "eval/state-machine-arn"
    assert ssm_key("mlflow_tracking_uri") == "mlflow-tracking-uri"
    assert KEY_TO_FIELD["eval/results-bucket"] == "eval_results_bucket"
    assert ssm_key("mcp_grants") == "mcp/grants" and KEY_TO_FIELD["mcp/gateway-arn"] == "mcp_gateway_arn"


def test_core_settings():
    s = load_settings("dev", PROJECT, ssm())
    assert (s.account, s.region, s.resource_prefix) == ("167769274046", "us-east-1", "dev")
    assert s.eval_state_machine_arn is None


def test_all_settings_and_unknown_keys_ignored():
    client = ssm(
        **{
            "eval/state-machine-arn": "arn:aws:states:us-east-1:167769274046:stateMachine:eval",
            "eval/results-bucket": "agentkit-dev-results",
            "mlflow-tracking-uri": "https://mlflow.example.com",
            "something-new": "x",
            "repository": "owner/repo",
            "credentials/openai-key": "ciphertext",
        }
    )
    s = load_settings("dev", PROJECT, client)
    s.require("eval_state_machine_arn", "eval_results_bucket", "mlflow_tracking_uri")
    assert s.eval_results_bucket == "agentkit-dev-results"


def test_environments_are_separate():
    client = FakeSsm({**ssm().params, **ssm("test", **{"resource-prefix": "test"}).params})
    assert load_settings("test", PROJECT, client).resource_prefix == "test"
    assert load_settings("dev", PROJECT, client).resource_prefix == "dev"


def test_projects_are_separate():
    client = FakeSsm({**ssm().params, **ssm(project="other", **{"resource-prefix": "dev2"}).params})
    assert load_settings("dev", PROJECT, client).resource_prefix == "dev"
    assert load_settings("dev", "other", client).resource_prefix == "dev2"


def test_a_project_name_that_is_a_prefix_of_another_does_not_read_its_settings():
    client = ssm(project="support-triage-2")
    with pytest.raises(SettingsError, match="no settings for project 'support-triage'"):
        load_settings("dev", PROJECT, client)


def test_a_project_not_registered_for_the_environment_has_no_settings():
    client = ssm()  # only dev
    with pytest.raises(SettingsError, match=r"project 'support-triage' in test .*registered for test"):
        load_settings("test", PROJECT, client)


def test_missing_core_setting_names_the_ssm_key():
    client = FakeSsm({k: v for k, v in ssm().params.items() if not k.endswith("/region")})
    with pytest.raises(SettingsError, match="region"):
        load_settings("dev", PROJECT, client)


def test_invalid_value_names_the_ssm_key():
    with pytest.raises(SettingsError, match=r"account .*pattern"):
        load_settings("dev", PROJECT, ssm(account="abc"))


def test_require_optional():
    s = load_settings("dev", PROJECT, ssm())
    with pytest.raises(SettingsError, match="eval/state-machine-arn, mlflow-tracking-uri"):
        s.require("eval_state_machine_arn", "mlflow_tracking_uri")


def test_unknown_environment():
    with pytest.raises(SettingsError, match="unknown environment"):
        load_settings("staging", PROJECT, ssm())


@pytest.mark.parametrize("name", ["Support", "support_triage", "../prod", "a/b", "", "-x", "x" * 41])
def test_unsafe_project_names_are_rejected(name):
    with pytest.raises(SettingsError, match="invalid project name"):
        project_path("dev", name)


def test_deploy_role_name_is_derived_not_looked_up():
    assert (
        deploy_role_arn("167769274046", "test", "support-triage")
        == "arn:aws:iam::167769274046:role/agentkit-test-support-triage-deploy"
    )


def test_prefix_from_env_var(monkeypatch):
    monkeypatch.setenv("AGENTKIT_SSM_PREFIX", "/other/")
    assert ssm_prefix() == "/other"
    client = FakeSsm({f"/other/dev/projects/{PROJECT}/{k}": v for k, v in CORE.items()})
    assert load_settings("dev", PROJECT, client).account == "167769274046"
    monkeypatch.setenv("AGENTKIT_SSM_PREFIX", "nope")
    with pytest.raises(SettingsError, match="must start with"):
        ssm_prefix()


def test_default_prefix(monkeypatch):
    monkeypatch.delenv("AGENTKIT_SSM_PREFIX", raising=False)
    assert ssm_prefix() == "/agentkit"


def test_ssm_failure_is_wrapped():
    class Broken:
        def get_paginator(self, name):
            raise RuntimeError("AccessDenied")

    with pytest.raises(SettingsError, match="AccessDenied"):
        load_settings("dev", PROJECT, Broken())


GATEWAY = "arn:aws:bedrock-agentcore:us-east-1:167769274046:gateway/dev-gw-abc123"


def test_mcp_grants_are_parsed_from_the_json_the_platform_publishes():
    grants = '[{"server":"kb-search","tools":["search","get_doc"]}]'
    s = load_settings("dev", PROJECT, ssm(**{"mcp/grants": grants, "mcp/gateway-arn": GATEWAY}))
    assert [(g.server, g.tools) for g in s.mcp_grants] == [("kb-search", ["search", "get_doc"])]
    assert s.mcp_gateway_arn == GATEWAY


def test_empty_grants_differ_from_a_missing_record():
    assert load_settings("dev", PROJECT, ssm(**{"mcp/grants": "[]"})).mcp_grants == []
    assert load_settings("dev", PROJECT, ssm()).mcp_grants is None


@pytest.mark.parametrize(
    "value", ["not json", '{"server":"x"}', '[{"server":"x"}]', '[{"server":"x","tools":[],"extra":1}]']
)
def test_malformed_grants_name_the_ssm_key(value):
    with pytest.raises(SettingsError, match="mcp/grants"):
        load_settings("dev", PROJECT, ssm(**{"mcp/grants": value}))
