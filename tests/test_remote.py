import io
import json
import shutil
import tarfile

import pytest

from agentkit import remote
from agentkit.evaluate import EvaluateError
from agentkit.settings import SettingsError
from test_assemble import FIXTURE
from test_settings import ssm

EVAL = {
    "eval/state-machine-arn": "arn:aws:states:us-east-1:1:stateMachine:dev-agentkit-eval",
    "eval/results-bucket": "bkt",
}


class S3:
    def __init__(self, report=None):
        self.objects, self.report = {}, report

    def put_object(self, Bucket, Key, Body):  # noqa: N803
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):  # noqa: N803
        if self.report is None:
            raise KeyError(Key)
        return {"Body": io.BytesIO(json.dumps(self.report).encode())}


class Sfn:
    def __init__(self, statuses, **final):
        self.statuses, self.final, self.started = list(statuses), final, None

    def start_execution(self, stateMachineArn, name, input):  # noqa: N803
        self.started = json.loads(input)
        return {"executionArn": "arn:exec"}

    def describe_execution(self, executionArn):  # noqa: N803
        status = self.statuses.pop(0)
        return {"status": status, **(self.final if status != "RUNNING" else {})}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    shutil.copytree(FIXTURE, root)
    (root / "reports").mkdir()
    (root / "reports" / "old.json").write_text("{}")
    return root


def settings_with_eval(**overrides):
    return ssm(**{**EVAL, **overrides})


def run(project, tmp_path, s3, sfn):
    return remote.evaluate_remote(
        project, "dev", "smoke", report_path=tmp_path / "r.json", ssm_client=settings_with_eval(), s3=s3, sfn=sfn,
        sleep=lambda _: None, out=lambda _: None,
    )  # fmt: skip


def test_bundle_leaves_out_local_files(project):
    (project / "node_modules").mkdir()
    (project / "node_modules" / "x.js").write_text("")
    names = tarfile.open(fileobj=io.BytesIO(remote.bundle(project))).getnames()
    assert "agent.yaml" in names and "evals/evaluation.yaml" in names
    assert not any(n.startswith(("reports", "node_modules")) for n in names)


def test_remote_run_returns_the_report_outcome(project, tmp_path):
    s3, sfn = S3({"outcome": "FAIL", "scenarios": [], "gates": []}), Sfn(["RUNNING", "RUNNING", "SUCCEEDED"])
    outcome, path = run(project, tmp_path, s3, sfn)
    assert outcome == "FAIL" and json.loads(path.read_text())["outcome"] == "FAIL"
    assert sfn.started["suite"] == "smoke" and sfn.started["targetEnv"] == "dev"
    keys = [k for _, k in s3.objects]
    assert keys == [sfn.started["inputKey"]]
    # keyed by project, so the project's deploy role can be limited to its own prefix
    assert sfn.started["inputKey"].startswith("inputs/support-triage/")
    assert sfn.started["reportKey"].startswith("results/support-triage/dev/")


def test_a_failed_execution_is_an_error(project, tmp_path):
    outcome, path = run(project, tmp_path, S3(), Sfn(["FAILED"], cause="task died"))
    assert outcome == "ERROR" and "task died" in json.loads(path.read_text())["error"]


def test_a_missing_report_is_an_error(project, tmp_path):
    assert run(project, tmp_path, S3(None), Sfn(["SUCCEEDED"]))[0] == "ERROR"


def test_needs_the_evaluation_settings(project, tmp_path):
    with pytest.raises(SettingsError, match="eval/state-machine-arn"):
        remote.evaluate_remote(project, "dev", "smoke", ssm_client=ssm(), s3=S3(), sfn=Sfn([]))


def test_invalid_project_is_refused(project, tmp_path):
    (project / "agent.yaml").write_text("nope: 1")
    with pytest.raises(EvaluateError):
        run(project, tmp_path, S3(), Sfn([]))
