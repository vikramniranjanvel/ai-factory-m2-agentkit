import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from agentkit.compiler import CompileError, compile_harness, credential_arn, harness_name
from agentkit.schemas import AgentDefinition, load_agent_definition
from agentkit.settings import Settings

FIXTURE = Path(__file__).parent / "fixtures" / "valid"
V1_HARNESS = Path(__file__).parent / "fixtures" / "v1" / "supportTriage.harness.json"

SETTINGS = Settings(
    account="167769274046",
    region="us-east-1",
    resource_prefix="dev",
)


def definition(**overrides) -> AgentDefinition:
    data = yaml.safe_load((FIXTURE / "agent.yaml").read_text())
    data.update(overrides)
    return AgentDefinition.model_validate(data)


def compile_(**overrides):
    return compile_harness(definition(**overrides), SETTINGS, "dev", FIXTURE)


def test_matches_v1_harness_json():
    """The v1 supportTriage harness.json is the same agent: model, tools, skills, memory and name are identical."""
    spec = compile_().spec
    v1 = json.loads(V1_HARNESS.read_text())
    assert spec["model"] == v1["model"]
    assert (spec["tools"], spec["skills"], spec["memory"]) == (v1["tools"], v1["skills"], v1["memory"])
    assert spec["name"] == v1["name"]  # the <env>_ prefix is added by the patched CDK at deploy time


def test_name_credential_and_tags():
    spec = compile_().spec
    assert spec["name"] == "supportTriage"
    assert spec["model"]["apiKeyArn"] == (
        "arn:aws:bedrock-agentcore:us-east-1:167769274046:token-vault/default/apikeycredentialprovider/openai-key"
    )
    assert spec["tags"] == {"agentkit:environment": "dev", "agentkit:managed-by": "agentkit"}


def test_author_tags_are_kept():
    assert compile_(tags={"team": "support"}).spec["tags"]["team"] == "support"


def test_prompt_is_copied_not_inlined():
    compiled = compile_()
    assert "systemPrompt" not in compiled.spec
    assert [(c.source, c.dest) for c in compiled.copies] == [(FIXTURE / "prompts/system.md", "system-prompt.md")]


def test_skills_are_copied(tmp_path):
    root = tmp_path / "p"
    shutil.copytree(FIXTURE, root)
    (root / "skills" / "escalation").mkdir(parents=True)
    d = definition(skills=[{"path": "skills/escalation"}])
    compiled = compile_harness(d, SETTINGS, "dev", root)
    assert compiled.spec["skills"] == [{"path": "skills/escalation"}]
    assert (
        Path(compiled.copies[1].source) == root / "skills/escalation" and compiled.copies[1].dest == "skills/escalation"
    )


def test_bedrock_has_no_api_key():
    spec = compile_(model={"provider": "bedrock", "model_id": "m", "temperature": 0.2, "max_tokens": 100}).spec
    assert spec["model"] == {"provider": "bedrock", "modelId": "m", "temperature": 0.2, "maxTokens": 100}


def test_limits_memory_truncation():
    spec = compile_(
        limits={"max_iterations": 5, "timeout_seconds": 60, "max_tokens": 1000},
        memory={"mode": "managed", "strategies": ["SEMANTIC"], "event_expiry_days": 30},
        truncation={"strategy": "summarization", "summary_ratio": 0.3, "preserve_recent_messages": 4},
    ).spec
    assert (spec["maxIterations"], spec["timeoutSeconds"], spec["maxTokens"]) == (5, 60, 1000)
    assert spec["memory"] == {"mode": "managed", "strategies": ["SEMANTIC"], "eventExpiryDuration": 30}
    assert spec["truncation"] == {
        "strategy": "summarization",
        "config": {"summarization": {"summaryRatio": 0.3, "preserveRecentMessages": 4}},
    }


@pytest.mark.parametrize(
    "truncation,expected",
    [
        ({"strategy": "none"}, {"strategy": "none"}),
        ({"strategy": "sliding_window"}, {"strategy": "sliding_window"}),
        (
            {"strategy": "sliding_window", "messages_count": 20},
            {"strategy": "sliding_window", "config": {"slidingWindow": {"messagesCount": 20}}},
        ),
    ],
)
def test_truncation_shapes(truncation, expected):
    assert compile_(truncation=truncation).spec["truncation"] == expected


def test_optional_keys_are_omitted_when_unset():
    spec = compile_().spec
    for key in ("maxIterations", "maxTokens", "timeoutSeconds", "truncation", "executionRoleArn", "systemPrompt"):
        assert key not in spec


def test_name_too_long_for_prefix():
    long = definition(name="a" * 32)  # valid on its own (max 32), 'dev_' pushes it to 36: fine
    assert len(harness_name(SETTINGS, long)) == 36
    big_prefix = Settings(**{**SETTINGS.model_dump(), "resource_prefix": "abcdefgh"})  # 8 + 1 + 32 = 41
    with pytest.raises(CompileError, match="41 characters"):
        harness_name(big_prefix, long)


def test_deployed_name_length_is_checked_when_compiling():
    big_prefix = Settings(**{**SETTINGS.model_dump(), "resource_prefix": "abcdefgh"})
    with pytest.raises(CompileError, match="41 characters"):
        compile_harness(definition(name="a" * 32), big_prefix, "dev", FIXTURE)


def test_empty_prompt(tmp_path):
    root = tmp_path / "p"
    shutil.copytree(FIXTURE, root)
    (root / "prompts" / "system.md").write_text("  \n")
    with pytest.raises(CompileError, match="empty"):
        compile_harness(load_agent_definition(root / "agent.yaml"), SETTINGS, "dev", root)


def test_credential_arn_uses_environment_values():
    other = Settings(**{**SETTINGS.model_dump(), "account": "111111111111", "region": "eu-west-1"})
    assert credential_arn(other, "k") == (
        "arn:aws:bedrock-agentcore:eu-west-1:111111111111:token-vault/default/apikeycredentialprovider/k"
    )


# --- the exit check from the plan: the native schema accepts what we produce -------------------------------------

NATIVE_CHECK = """
const path = require('path');
const dir = path.join(process.argv[1], '@aws/agentcore/dist/schema/schemas/primitives/harness.js');
const { HarnessSpecSchema } = require(dir);
const result = HarnessSpecSchema.safeParse(JSON.parse(process.argv[2]));
if (!result.success) { console.error(JSON.stringify(result.error.issues)); process.exit(1); }
"""


def native_root() -> str | None:
    try:
        out = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return out if (Path(out) / "@aws/agentcore").exists() else None


@pytest.mark.skipif(native_root() is None, reason="@aws/agentcore is not installed globally")
@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"model": {"provider": "bedrock", "model_id": "m", "temperature": 0.2, "top_p": 0.9, "max_tokens": 100}},
        {"model": {"provider": "gemini", "model_id": "m", "credential": "g", "top_k": 40}},
        {"memory": {"mode": "managed", "strategies": ["SEMANTIC", "EPISODIC"], "event_expiry_days": 30}},
        {"limits": {"max_iterations": 5, "timeout_seconds": 60, "max_tokens": 1000}},
        {"truncation": {"strategy": "sliding_window", "messages_count": 20}},
        {"truncation": {"strategy": "summarization", "summary_ratio": 0.3, "preserve_recent_messages": 4}},
        {"truncation": {"strategy": "none"}},
        {"tags": {"team": "support", "cost-center": "a/b=c"}},
        {"skills": [{"path": "skills/escalation"}]},
    ],
)
def test_native_schema_accepts_output(overrides):
    spec = compile_(**overrides).spec
    result = subprocess.run(
        ["node", "-e", NATIVE_CHECK, native_root(), json.dumps(spec)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


GATEWAY = "arn:aws:bedrock-agentcore:us-east-1:167769274046:gateway/dev-gw-abc123"
KB = [{"server": "kb-search", "tools": ["search", "get_doc"]}]


def with_mcp(grants=None, gateway=GATEWAY) -> Settings:
    return SETTINGS.model_copy(update={"mcp_grants": grants, "mcp_gateway_arn": gateway})


def compile_tools(requested, settings, env="dev"):
    return compile_harness(definition(tools=requested), settings, env, FIXTURE)


def test_granted_tools_compile_to_a_gateway_tool_and_an_allow_list():
    from agentkit.settings import McpGrant

    settings = with_mcp([McpGrant(server="kb-search", tools=["search", "get_doc", "list_docs"])])
    spec = compile_tools(KB, settings).spec
    assert spec["tools"] == [
        {
            "type": "agentcore_gateway",
            "name": "gateway",
            "config": {"agentCoreGateway": {"gatewayArn": GATEWAY, "outboundAuth": {"awsIam": {}}}},
        }
    ]
    # only what was requested, not everything that was granted
    assert spec["allowedTools"] == ["@gateway/kb-search___search", "@gateway/kb-search___get_doc"]


@pytest.mark.skipif(native_root() is None, reason="@aws/agentcore is not installed globally")
def test_native_schema_accepts_gateway_tools():
    from agentkit.settings import McpGrant

    spec = compile_tools(KB, with_mcp([McpGrant(server="kb-search", tools=["search", "get_doc"])])).spec
    result = subprocess.run(
        ["node", "-e", NATIVE_CHECK, native_root(), json.dumps(spec)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_no_tools_requested_adds_nothing_and_needs_no_grant_record():
    spec = compile_().spec  # SETTINGS has no grants and no gateway
    assert spec["tools"] == [] and "allowedTools" not in spec


def test_tool_not_granted_fails_naming_the_tool_and_environment():
    from agentkit.settings import McpGrant

    settings = with_mcp([McpGrant(server="kb-search", tools=["search"])])
    with pytest.raises(CompileError) as exc:
        compile_tools(KB, settings, env="prod")
    assert "kb-search.get_doc" in str(exc.value) and "in prod" in str(exc.value)
    assert "kb-search.search" not in str(exc.value)  # the granted one is not reported


def test_server_not_granted_fails():
    from agentkit.settings import McpGrant

    settings = with_mcp([McpGrant(server="ticketing", tools=["create_ticket"])])
    with pytest.raises(CompileError, match="server 'kb-search' is not granted"):
        compile_tools(KB, settings)


def test_every_problem_is_reported_at_once():
    settings = with_mcp([])
    with pytest.raises(CompileError) as exc:
        compile_tools(KB + [{"server": "ticketing", "tools": ["create_ticket"]}], settings)
    assert "kb-search" in str(exc.value) and "ticketing" in str(exc.value)


def test_nothing_granted_is_not_the_same_as_no_record():
    from agentkit.settings import SettingsError

    with pytest.raises(CompileError, match="not granted"):
        compile_tools(KB, with_mcp([]))  # record exists, empty
    with pytest.raises(SettingsError, match="mcp/grants"):
        compile_tools(KB, with_mcp(None))  # record missing


def test_missing_gateway_is_reported_not_guessed():
    from agentkit.settings import McpGrant, SettingsError

    with pytest.raises(SettingsError, match="mcp/gateway-arn"):
        compile_tools(KB, with_mcp([McpGrant(server="kb-search", tools=["search", "get_doc"])], gateway=None))
