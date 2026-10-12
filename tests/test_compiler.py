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


def test_tools_are_not_compiled_yet():
    """Declared tools must fail loudly rather than be dropped, until the grant check and gateway wiring exist."""
    with pytest.raises(CompileError, match="tools"):
        compile_(tools=[{"server": "kb-search", "tools": ["search"]}])
