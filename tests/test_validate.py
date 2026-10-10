import json
import shutil
from pathlib import Path

import pytest

from agentkit.cli import main
from agentkit.validate import key_path, validate_project

FIXTURE = Path(__file__).parent / "fixtures" / "valid"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(FIXTURE, root)
    return root


def messages(root):
    return [str(i) for i in validate_project(root)[1]]


def test_valid_project(project):
    checked, issues = validate_project(project)
    assert issues == []
    assert checked == ["agent.yaml", "evals/evaluation.yaml", "evals/dataset.json", "evals/smoke.json"]


def test_key_path():
    assert key_path(("gates", 2, "metric")) == "gates[2].metric"
    assert key_path(()) == ""


def test_agent_errors_have_key_paths(project):
    f = project / "agent.yaml"
    f.write_text(f.read_text().replace("provider: open_ai", "provider: nope") + "bogus: 1\n")
    out = messages(project)
    assert any(m.startswith("agent.yaml: model.provider: Input should be") for m in out)
    assert any(m.startswith("agent.yaml: bogus:") for m in out)


def test_missing_prompt_file(project):
    (project / "prompts" / "system.md").unlink()
    assert messages(project) == ["agent.yaml: referenced files not found: prompts/system.md"]


def test_missing_files(tmp_path):
    out = messages(tmp_path)
    assert out == ["agent.yaml: file not found", "evals/evaluation.yaml: file not found"]


def test_bad_gate_metric(project):
    f = project / "evals" / "evaluation.yaml"
    f.write_text(f.read_text().replace("metric: completion_rate", "metric: nope"))
    assert any(
        "gate metric 'nope' is not available" in m and m.startswith("evals/evaluation.yaml:") for m in messages(project)
    )


def test_dataset_error_names_the_dataset_file(project):
    f = project / "evals" / "smoke.json"
    data = json.loads(f.read_text())
    data["scenarios"][1]["scenario_id"] = data["scenarios"][0]["scenario_id"]
    f.write_text(json.dumps(data))
    out = messages(project)
    assert len(out) == 1 and out[0].startswith("evals/smoke.json: duplicate scenario_id")


def test_missing_dataset(project):
    (project / "evals" / "smoke.json").unlink()
    assert messages(project) == ["evals/evaluation.yaml: suites: dataset not found: evals/smoke.json"]


def test_invalid_json_and_yaml(project):
    (project / "evals" / "smoke.json").write_text("{")
    (project / "agent.yaml").write_text("a: [")
    out = messages(project)
    assert any(m.startswith("evals/smoke.json: not valid JSON") for m in out)
    assert any(m.startswith("agent.yaml: not valid YAML") for m in out)


def test_scored_suite_without_assertions(project):
    f = project / "evals" / "dataset.json"
    data = json.loads(f.read_text())
    data["scenarios"][0].pop("assertions")
    f.write_text(json.dumps(data))
    out = messages(project)
    assert len(out) == 1 and out[0].startswith("evals/evaluation.yaml:") and "no assertions" in out[0]


def test_cli_exit_codes(project, capsys):
    assert main(["validate", str(project)]) == 0
    assert capsys.readouterr().out.startswith("OK: agent.yaml")
    (project / "prompts" / "system.md").unlink()
    assert main(["validate", str(project)]) == 1
    err = capsys.readouterr().err
    assert "prompts/system.md" in err and "1 problem(s) in 1 file(s)" in err
