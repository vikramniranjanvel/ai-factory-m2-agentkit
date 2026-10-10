import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agentkit.assemble import (
    NATIVE_CLI_VERSION,
    PATCH_ANCHOR,
    AssembleError,
    assemble_project,
    native_cli_root,
)
from agentkit.schemas import load_agent_definition
from agentkit.settings import Settings

FIXTURE = Path(__file__).parent / "fixtures" / "valid"
SETTINGS = Settings(
    account="167769274046",
    region="us-east-1",
    resource_prefix="dev",
    deploy_role_arn="arn:aws:iam::167769274046:role/agentkit-deploy-dev",
)

CDK_TS = f"""import x from 'y';
    new AgentCoreStack(app, stackName, {{
{PATCH_ANCHOR}      credentials,
    }});
"""


def fake_native(tmp_path, version=NATIVE_CLI_VERSION, cdk_ts=CDK_TS) -> Path:
    """A stand-in @aws/agentcore package: just what assembly reads."""
    root = tmp_path / "native"
    (root / "dist/assets/cdk/bin").mkdir(parents=True)
    (root / "dist/assets/cdk/lib").mkdir()
    (root / "package.json").write_text(json.dumps({"name": "@aws/agentcore", "version": version}))
    (root / "dist/assets/cdk/bin/cdk.ts").write_text(cdk_ts)
    (root / "dist/assets/cdk/lib/cdk-stack.ts").write_text("// stack\n")
    (root / "dist/assets/cdk/package.json").write_text("{}")
    (root / "dist/assets/cdk/gitignore.template").write_text("node_modules\n")
    (root / "dist/assets/cdk/npmignore.template").write_text("src\n")
    (root / "dist/assets/cdk/README.md").write_text("readme\n")
    (root / "dist/assets/cdk/test").mkdir()
    return root


def assemble(tmp_path, env="dev", settings=SETTINGS, **kwargs):
    if "native_root" not in kwargs:
        kwargs["native_root"] = fake_native(tmp_path)
    return assemble_project(
        load_agent_definition(FIXTURE / "agent.yaml"), settings, env, FIXTURE, tmp_path / "out", **kwargs
    )


def read(path: Path):
    return json.loads(path.read_text())


def test_layout(tmp_path):
    out = assemble(tmp_path)
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
    assert files == [
        "agentcore/agentcore.json",
        "agentcore/aws-targets.json",
        "agentcore/cdk/.gitignore",
        "agentcore/cdk/.npmignore",
        "agentcore/cdk/bin/cdk.ts",
        "agentcore/cdk/lib/cdk-stack.ts",
        "agentcore/cdk/package.json",
        "app/supportTriage/harness.json",
        "app/supportTriage/system-prompt.md",
    ]
    assert (out / "app/supportTriage/system-prompt.md").read_text() == (FIXTURE / "prompts/system.md").read_text()


def test_single_target_named_after_the_environment(tmp_path):
    out = assemble(tmp_path, env="test")
    assert read(out / "agentcore/aws-targets.json") == [
        {"name": "test", "description": "test", "account": "167769274046", "region": "us-east-1"}
    ]


def test_project_manifest(tmp_path):
    project = read(assemble(tmp_path) / "agentcore/agentcore.json")
    assert project["name"] == "supportTriage"
    assert project["credentials"] == [{"authorizerType": "ApiKeyCredentialProvider", "name": "openai-key"}]
    assert project["harnesses"] == [{"name": "supportTriage", "path": "app/supportTriage"}]


def test_manifest_matches_v1(tmp_path):
    """Same shape as the v1 supportTriage agentcore.json except the created-by tag."""
    v1 = json.loads((Path(__file__).parent / "fixtures/v1/supportTriage.agentcore.json").read_text())
    mine = read(assemble(tmp_path) / "agentcore/agentcore.json")
    v1["tags"]["agentcore:created-by"] = "agentkit"
    assert mine == v1


def test_harness_json_is_the_compiled_spec(tmp_path):
    spec = read(assemble(tmp_path) / "app/supportTriage/harness.json")
    assert spec["name"] == "supportTriage" and spec["tags"]["agentkit:environment"] == "dev"


def test_cdk_template_is_copied_and_patched_once(tmp_path):
    cdk = (assemble(tmp_path) / "agentcore/cdk/bin/cdk.ts").read_text()
    assert cdk.count('spec: { ...spec, name: "dev" },') == 1 and "AGENTKIT PATCH" in cdk
    assert cdk.replace(cdk[cdk.index("      // AGENTKIT") : cdk.index("      mcpSpec")], "      spec,\n") == CDK_TS


def test_prefix_not_environment_names_the_harness(tmp_path):
    settings = Settings(**{**SETTINGS.model_dump(), "resource_prefix": "qa"})
    cdk = (assemble(tmp_path, env="test", settings=settings) / "agentcore/cdk/bin/cdk.ts").read_text()
    assert 'name: "qa"' in cdk


def test_template_renames_and_skips(tmp_path):
    out = assemble(tmp_path)
    assert (out / "agentcore/cdk/.gitignore").read_text() == "node_modules\n"
    assert not (out / "agentcore/cdk/test").exists() and not (out / "agentcore/cdk/README.md").exists()


def test_changed_template_fails_loudly(tmp_path):
    native = fake_native(tmp_path, cdk_ts="new AgentCoreStack(app, stackName, { spec });\n")
    with pytest.raises(AssembleError, match="patch anchor"):
        assemble(tmp_path, native_root=native)


def test_version_mismatch(tmp_path):
    with pytest.raises(AssembleError, match=f"9.9.9 is installed .* {NATIVE_CLI_VERSION}"):
        assemble(tmp_path, native_root=fake_native(tmp_path, version="9.9.9"))


def test_destination_must_be_empty(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "x").write_text("")
    with pytest.raises(AssembleError, match="not empty"):
        assemble(tmp_path)


def test_skills_are_copied(tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(FIXTURE, root)
    (root / "skills" / "escalation").mkdir(parents=True)
    (root / "skills" / "escalation" / "SKILL.md").write_text("# skill\n")
    (root / "agent.yaml").write_text((root / "agent.yaml").read_text() + "skills:\n  - path: skills/escalation\n")
    out = assemble_project(
        load_agent_definition(root / "agent.yaml"), SETTINGS, "dev", root, tmp_path / "out", fake_native(tmp_path)
    )
    assert (out / "app/supportTriage/skills/escalation/SKILL.md").read_text() == "# skill\n"
    assert read(out / "app/supportTriage/harness.json")["skills"] == [{"path": "skills/escalation"}]


def test_bedrock_agent_declares_no_credentials(tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(FIXTURE, root)
    text = (
        (root / "agent.yaml")
        .read_text()
        .replace("provider: open_ai", "provider: bedrock")
        .replace("  credential: openai-key\n", "")
    )
    (root / "agent.yaml").write_text(text)
    out = assemble_project(
        load_agent_definition(root / "agent.yaml"), SETTINGS, "dev", root, tmp_path / "out", fake_native(tmp_path)
    )
    assert read(out / "agentcore/agentcore.json")["credentials"] == []


# --- against the real installed CLI ----------------------------------------------------------------------------


def real_native() -> Path | None:
    try:
        root = native_cli_root()
        return root if json.loads((root / "package.json").read_text())["version"] == NATIVE_CLI_VERSION else None
    except AssembleError:
        return None


@pytest.mark.skipif(real_native() is None, reason=f"@aws/agentcore@{NATIVE_CLI_VERSION} is not installed")
def test_real_template_patches_and_native_cli_validates_the_project(tmp_path):
    out = assemble(tmp_path, native_root=real_native())
    assert "AGENTKIT PATCH" in (out / "agentcore/cdk/bin/cdk.ts").read_text()
    result = subprocess.run(["agentcore", "validate", "-d", str(out)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
