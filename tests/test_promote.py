import json
import shutil

import pytest

from agentkit import harness as hs
from agentkit.cli import main
from agentkit.promote import ENDPOINT, PromoteError, check_report, promote_project
from test_assemble import FIXTURE
from test_settings import ssm


class Missing(Exception):
    response = {"Error": {"Code": "ResourceNotFoundException"}}


class Control:
    """The `live` endpoint of harness h1, whose versions are 1..3. Updates take effect on the next read."""

    def __init__(self, live=None, status="READY"):
        self.endpoint = {"status": status, "liveVersion": live} if live else None
        self.calls = []

    def list_harnesses(self, **_):
        return {"harnesses": [{"harnessName": "dev_supportTriage", "harnessId": "h1", "harnessVersion": 3}]}

    def get_harness(self, harnessId, harnessVersion):  # noqa: N803
        return {
            "harness": {"harnessId": "h1", "arn": "arn:h1", "harnessVersion": int(harnessVersion), "status": "READY"}
        }

    def get_harness_endpoint(self, harnessId, endpointName):  # noqa: N803
        assert endpointName == ENDPOINT
        if self.endpoint is None:
            raise Missing()
        return {"endpoint": self.endpoint}

    def create_harness_endpoint(self, harnessId, endpointName, targetVersion):  # noqa: N803
        self.calls.append(("create", targetVersion))
        self.endpoint = {"status": "READY", "liveVersion": int(targetVersion)}

    def update_harness_endpoint(self, harnessId, endpointName, targetVersion):  # noqa: N803
        self.calls.append(("update", targetVersion))
        self.endpoint = {"status": "READY", "liveVersion": int(targetVersion)}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    shutil.copytree(FIXTURE, root)
    return root


def promote(project, control, **kw):
    return promote_project(
        project, "dev", ssm_client=ssm(), control=control, sleep=lambda _: None, out=lambda _: None, **kw
    )


def report(tmp_path, **kw):
    data = {"agent": "supportTriage", "env": "dev", "harness_version": "3", "outcome": "PASS", **kw}
    path = tmp_path / "report.json"
    path.write_text(json.dumps(data))
    return path


def test_first_promotion_creates_the_endpoint(project):
    control = Control()
    manifest = promote(project, control)
    assert control.calls == [("create", "3")]
    assert manifest["version"] == "3" and manifest["previous_version"] is None
    assert json.loads((project / "reports" / "release-supportTriage-dev.json").read_text())["version"] == "3"


def test_later_promotion_updates_and_records_the_previous_version(project):
    control = Control(live=2)
    manifest = promote(project, control)
    assert control.calls == [("update", "3")]
    assert manifest["previous_version"] == "2"


def test_rollback_is_an_older_version(project):
    control = Control(live=3)
    manifest = promote(project, control, version="1")
    assert control.calls == [("update", "1")] and manifest["version"] == "1" and manifest["previous_version"] == "3"


def test_already_live_changes_nothing(project):
    control = Control(live=3)
    promote(project, control)
    assert control.calls == []


def test_report_must_be_a_pass_of_this_version(project, tmp_path):
    control = Control()
    manifest = promote(project, control, report=report(tmp_path))
    assert manifest["evaluation"]["outcome"] == "PASS"
    for bad, match in [
        ({"outcome": "FAIL"}, "only PASS"),
        ({"harness_version": "2"}, "version 2"),
        ({"env": "prod"}, "in prod"),
        ({"agent": "other"}, "other"),
    ]:
        with pytest.raises(PromoteError, match=match):
            promote(project, Control(), report=report(tmp_path, **bad))


def test_a_failed_endpoint_is_an_error(project):
    class Failing(Control):
        def update_harness_endpoint(self, **kw):
            self.endpoint = {"status": "UPDATE_FAILED", "failureReason": "boom"}

    with pytest.raises(PromoteError, match="UPDATE_FAILED.*boom"):
        promote(project, Failing(live=2))


def test_unknown_harness_version(project):
    with pytest.raises(hs.HarnessError, match="number or 'latest'"):
        promote(project, Control(), version="x")


def test_check_report_direct():
    harness = hs.Harness("h1", "a", "n", "3", None, "1", "r")
    check_report({"agent": "a", "env": "e", "harness_version": 3, "outcome": "PASS"}, "a", "e", harness)


def test_cli_exit_codes(monkeypatch):
    def fail(*a, **k):
        raise PromoteError("no")

    monkeypatch.setattr("agentkit.cli.promote_project", fail)
    assert main(["promote", "--env", "prod"]) == 1
    monkeypatch.setattr("agentkit.cli.promote_project", lambda *a, **k: {})
    assert main(["promote", "--env", "prod"]) == 0
