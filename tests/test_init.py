import os
import re
import subprocess

import pytest
import yaml

from agentkit.cli import main
from agentkit.init import REPO_PREFIX, InitError, init_project, slug
from agentkit.schemas import load_agent_definition
from agentkit.validate import validate_project


def files(root):
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_scaffold_is_valid_and_complete(tmp_path):
    target = init_project("myAgent", tmp_path / "out")
    assert validate_project(target)[1] == []
    assert files(target) == {
        "agent.yaml",
        "prompts/system.md",
        "evals/evaluation.yaml",
        "evals/smoke.json",
        "evals/dataset.json",
        ".github/workflows/deploy.yml",
        ".github/workflows/promote.yml",
        ".gitignore",
        "README.md",
        "scripts/setup.sh",
    }
    definition = load_agent_definition(target / "agent.yaml")
    assert (definition.name, definition.model.provider, definition.model.credential) == (
        "myAgent",
        "open_ai",
        "openai-key",
    )


def test_every_token_is_replaced(tmp_path):
    target = init_project("myAgent", tmp_path / "out", description="Does: things")
    for f in files(target):
        assert not re.search(r"__[A-Z_]+__", (target / f).read_text()), f
    assert yaml.safe_load((target / "agent.yaml").read_text())["description"] == "Does: things"
    setup = (target / "scripts/setup.sh").read_text()
    assert f'REPO="{REPO_PREFIX}my-agent"' in setup and 'PROJECT="my-agent"' in setup


def test_workflows_parse_and_match_the_platform_events(tmp_path):
    target = init_project("myAgent", tmp_path / "out")
    deploy = yaml.safe_load((target / ".github/workflows/deploy.yml").read_text())
    promote = yaml.safe_load((target / ".github/workflows/promote.yml").read_text())
    assert set(deploy[True]) == {"push", "pull_request"}  # YAML reads the key `on` as True
    assert "workflow_dispatch" in promote[True]


def test_setup_script_is_executable_and_valid_shell(tmp_path):
    target = init_project("myAgent", tmp_path / "out")
    script = target / "scripts/setup.sh"
    assert os.access(script, os.X_OK)
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_bedrock_has_no_credential(tmp_path):
    target = init_project("myAgent", tmp_path / "out", provider="bedrock", model_id="us.anthropic.claude-sonnet-5-5")
    assert load_agent_definition(target / "agent.yaml").model.credential is None
    assert 'CREDENTIAL=""' in (target / "scripts/setup.sh").read_text()


def test_options(tmp_path):
    target = init_project("myAgent", tmp_path / "o", credential="other-key", repo="my-repo")
    assert 'CREDENTIAL="other-key"' in (target / "scripts/setup.sh").read_text()
    assert 'REPO="my-repo"' in (target / "scripts/setup.sh").read_text()


@pytest.mark.parametrize("name", ["", "1abc", "my-agent", "my agent", "a" * 33])
def test_bad_names(tmp_path, name):
    with pytest.raises(InitError, match="not a valid agent name"):
        init_project(name, tmp_path / "x")


def test_refuses_a_directory_with_files(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "keep.txt").write_text("hi")
    with pytest.raises(InitError, match="not empty"):
        init_project("myAgent", tmp_path / "x")
    assert files(tmp_path / "x") == {"keep.txt"}
    init_project("myAgent", tmp_path / "empty-ok")


def test_bad_provider_values_are_reported(tmp_path):
    with pytest.raises(InitError, match="not valid"):
        init_project("myAgent", tmp_path / "x", provider="bedrock", credential="k", model_id="")


def test_slug():
    assert slug("supportTriage") == "support-triage"
    assert slug("agent") == "agent"
    assert slug("myAIAgent") == "my-a-i-agent"


def test_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "supportBot", "--description", "Answers support questions"]) == 0
    assert (tmp_path / "support-bot" / "agent.yaml").exists()
    assert "scripts/setup.sh" in capsys.readouterr().out
    assert main(["init", "supportBot"]) == 1  # already exists
    assert main(["init", "bad-name"]) == 1
