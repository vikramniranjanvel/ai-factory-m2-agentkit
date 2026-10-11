import json
import shutil
from datetime import UTC, datetime

import pytest

from agentkit import evaluate as ev
from agentkit import harness as hs
from agentkit import scoring
from agentkit.cli import main
from agentkit.schemas import load_evaluation_config
from agentkit.schemas.dataset import Scenario
from test_assemble import FIXTURE
from test_settings import ssm


def scenario(answer=None, **kw):
    data = {"scenario_id": "s-1", "turns": [{"input": "hi"}], **kw}
    if answer:
        data["expected"] = {"answer": answer}
    return Scenario.model_validate(data)


def config(tmp_path, gates="", evaluators="", score=False):
    (tmp_path / "d.json").write_text(
        json.dumps(
            {"schema_version": 1, "scenarios": [{"scenario_id": "a", "turns": [{"input": "x"}], "assertions": ["ok"]}]}
        )
    )
    text = f"""schema_version: 1
suites:
  s: {{dataset: d.json, score: {str(score).lower()}}}
checks:
  - {{type: response_matches_regex, expected_from: expected.answer}}
{evaluators}
gates:
{gates}
"""
    (tmp_path / "evaluation.yaml").write_text(text)
    return load_evaluation_config(tmp_path / "evaluation.yaml")


def test_checks_pass_fail_and_skip():
    cfg = [ev.Check(type="response_matches_regex", expected_from="expected.answer")]
    assert ev.run_checks(cfg, scenario("P1"), "priority P1")[0]["status"] == "PASS"
    assert ev.run_checks(cfg, scenario("P1"), "priority P2")[0]["status"] == "FAIL"
    assert ev.run_checks(cfg, scenario(), "anything") == []  # no expected answer: skipped


def test_contains_checks_ignore_case():
    assert ev.CHECKS["response_contains"]("Hello WORLD", "world")
    assert ev.CHECKS["response_not_contains"]("Hello", "bye")


def test_metrics_and_gates(tmp_path):
    cfg = config(
        tmp_path, gates="  - {metric: completion_rate, min: 1.0}\n  - {metric: deterministic_pass_rate, min: 0.5}"
    )
    results = [
        {"id": "a", "checks": [{"status": "PASS"}]},
        {"id": "b", "checks": [{"status": "FAIL"}]},
        {"id": "c", "checks": [], "invoke_error": "boom"},
    ]
    metrics = ev.compute_metrics(results, cfg, scored=False)
    assert metrics == {"completion_rate": 2 / 3, "deterministic_pass_rate": 1 / 3}
    gates = ev.evaluate_gates(metrics, cfg)
    assert [g["status"] for g in gates] == ["FAIL", "FAIL"]
    assert ev.outcome(results, gates) == "ERROR"  # an invoke error is never a pass or a mere fail
    assert ev.outcome(results[:2], gates) == "FAIL"


def test_evaluator_gates_only_apply_when_scoring(tmp_path):
    cfg = config(
        tmp_path,
        score=True,
        evaluators="evaluators:\n  - {id: Builtin.Helpfulness}",
        gates="  - {metric: helpfulness_pass_rate, min: 1.0}",
    )
    results = [{"id": "a", "checks": [], "evaluations": [{"evaluator": "Builtin.Helpfulness", "status": "PASS"}]}]
    assert ev.evaluate_gates(ev.compute_metrics(results, cfg, scored=False), cfg) == []
    gates = ev.evaluate_gates(ev.compute_metrics(results, cfg, scored=True), cfg)
    assert gates[0]["value"] == 1.0 and gates[0]["status"] == "PASS"


def test_scoring_error_is_an_error_outcome():
    results = [{"id": "a", "checks": [], "evaluations": [{"evaluator": "x", "status": "ERROR"}]}]
    assert ev.outcome(results, []) == "ERROR"


def test_judge():
    helpful = ev.Evaluator(id="Builtin.Helpfulness") if hasattr(ev, "Evaluator") else None
    from agentkit.schemas.evaluation_config import Evaluator

    helpful = Evaluator(id="Builtin.Helpfulness")
    ok = {"value": 0.83, "label": "Very Helpful", "explanation": "e"}
    low = {"value": 0.33, "label": "Not", "explanation": "e"}
    assert scoring.judge(helpful, [ok, ok])["status"] == "PASS"
    assert scoring.judge(helpful, [ok, low])["status"] == "FAIL"  # the worst turn decides
    assert scoring.judge(helpful, [])["status"] == "ERROR"
    assert scoring.judge(helpful, [{"value": None, "error": "X", "message": "m"}])["status"] == "ERROR"
    assert scoring.judge(Evaluator(id="Builtin.Helpfulness", min_score=0.9), [ok])["status"] == "FAIL"


def test_ground_truth():
    sc = scenario(assertions=["a"], turns=[{"input": "q", "expected_response": "r"}])
    gt = scoring.ground_truth(sc)
    assert gt["assertions"] == [{"text": "a"}]
    assert gt["turns"][0]["expectedResponse"] == {"text": "r"}
    assert scoring.ground_truth(scenario()) is None


def test_runtime_ids():
    arn = "arn:aws:bedrock-agentcore:us-east-1:1:runtime/dev_agent_x-AbC123"
    assert scoring.runtime_ids(arn, "eval_1") == (
        "dev_agent_x.eval_1",
        "/aws/bedrock-agentcore/runtimes/dev_agent_x-AbC123-eval_1",
    )


class Control:
    def __init__(self):
        self.endpoints = {}

    def list_harnesses(self, **_):
        return {"harnesses": [{"harnessName": "dev_supportTriage", "harnessId": "h1", "harnessVersion": 3}]}

    def get_harness(self, harnessId, harnessVersion):  # noqa: N803
        return {
            "harness": {
                "harnessId": harnessId,
                "arn": "arn:h1",
                "harnessVersion": int(harnessVersion),
                "status": "READY",
                "environment": {"agentCoreRuntimeEnvironment": {"agentRuntimeArn": "arn:rt/dev_x-Ab"}},
            }
        }

    def list_harness_endpoints(self, harnessId):  # noqa: N803
        return {"endpoints": [{"endpointName": n, "createdAt": c} for n, c in self.endpoints.items()]}

    def create_harness_endpoint(self, harnessId, endpointName, targetVersion):  # noqa: N803
        self.endpoints[endpointName] = datetime.now(UTC)
        self.target = targetVersion

    def get_harness_endpoint(self, harnessId, endpointName):  # noqa: N803
        if endpointName not in self.endpoints:
            raise Missing()
        return {"endpoint": {"status": "READY"}}

    def delete_harness_endpoint(self, harnessId, endpointName):  # noqa: N803
        del self.endpoints[endpointName]


class Missing(Exception):
    response = {"Error": {"Code": "ResourceNotFoundException"}}


class AgentCore:
    def __init__(self, reply):
        self.reply = reply
        self.qualifiers = []

    def invoke_harness(self, harnessArn, qualifier, runtimeSessionId, messages):  # noqa: N803
        self.qualifiers.append(qualifier)
        return {"stream": [{"contentBlockDelta": {"delta": {"text": self.reply}}}]}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    shutil.copytree(FIXTURE, root)
    return root


def run(project, tmp_path, reply, suite="smoke"):
    control = Control()
    clients = ev.Clients(control, AgentCore(reply), None, None, "167769274046")
    result = ev.evaluate_project(
        project, "dev", suite, report_path=tmp_path / "r.json", ssm_client=ssm(), clients=clients, out=lambda _: None
    )
    return result, control, clients


def test_evaluate_smoke_passes_and_cleans_up(project, tmp_path):
    suites = load_evaluation_config(project / "evals" / "evaluation.yaml").suites
    assert "smoke" in suites
    dataset = json.loads((project / "evals" / suites["smoke"].dataset).read_text())
    answer = " ".join(sc["expected"]["answer"].replace("(?i)", "") for sc in dataset["scenarios"] if "expected" in sc)
    (outcome, report), control, clients = run(project, tmp_path, answer, "smoke")
    data = json.loads(report.read_text())
    assert outcome == "PASS" and data["outcome"] == "PASS"
    assert data["harness_version"] == "3" and control.target == "3"
    assert control.endpoints == {}  # the temporary endpoint is gone
    assert all(q.startswith("eval_") for q in clients.agentcore.qualifiers)
    assert data["dataset"]["hash"].startswith("sha256:")


def test_evaluate_fails_a_wrong_answer(project, tmp_path):
    (outcome, _), control, _ = run(project, tmp_path, "zzz-no-match-zzz")
    assert outcome == "FAIL" and control.endpoints == {}


def test_wrong_account_is_refused(project, tmp_path):
    clients = ev.Clients(Control(), AgentCore("x"), None, None, "999999999999")
    with pytest.raises(ev.EvaluateError, match="credentials are for account"):
        ev.evaluate_project(project, "dev", "smoke", ssm_client=ssm(), clients=clients, out=lambda _: None)


def test_unknown_suite(project, tmp_path):
    clients = ev.Clients(Control(), AgentCore("x"), None, None, "167769274046")
    with pytest.raises(ev.EvaluateError, match="not defined"):
        ev.evaluate_project(project, "dev", "nope", ssm_client=ssm(), clients=clients, out=lambda _: None)


def test_only_eval_endpoints_are_deleted():
    h = hs.Harness("h1", "arn", "n", "1", None, "1", "r")
    with pytest.raises(hs.HarnessError):
        hs.delete_endpoint(Control(), h, "production")


def test_stale_endpoints_are_swept():
    control = Control()
    control.endpoints = {"eval_old": datetime(2020, 1, 1, tzinfo=UTC), "prod": datetime(2020, 1, 1, tzinfo=UTC)}
    h = hs.resolve_harness(control, "dev_supportTriage", "latest", "1", "r")
    hs.sweep_stale(control, h)
    assert list(control.endpoints) == ["prod"]


def test_cli_exit_codes(monkeypatch):
    monkeypatch.setattr("agentkit.cli.evaluate_project", lambda *a, **k: ("FAIL", None))
    assert main(["evaluate", "--env", "dev"]) == 1
    monkeypatch.setattr("agentkit.cli.evaluate_project", lambda *a, **k: ("PASS", None))
    assert main(["evaluate", "--env", "dev"]) == 0
