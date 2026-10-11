"""Evaluate a deployed agent: `agentkit evaluate --env dev --suite smoke`.

Steps: validate, load settings, find the Harness version under test, pin a temporary endpoint to it, run every
scenario, run the deterministic checks, optionally score the conversations with AgentCore batch evaluation (suites
with `score: true`), apply the gates, delete the endpoint, write a JSON report.

Outcome: PASS (every gate met), FAIL (a gate missed), ERROR (something could not be run or scored: never a PASS).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from agentkit import harness as hs
from agentkit import scoring
from agentkit.schemas.agent_definition import load_agent_definition
from agentkit.schemas.dataset import Scenario, load_dataset
from agentkit.schemas.evaluation_config import (
    Check,
    EvaluationConfig,
    evaluator_metric,
    load_evaluation_config,
)
from agentkit.settings import load_settings
from agentkit.validate import validate_project

PASS, FAIL, ERROR = "PASS", "FAIL", "ERROR"
Outcome = Literal["PASS", "FAIL", "ERROR"]

CHECKS: dict[str, Callable[[str, str], bool]] = {
    "response_contains": lambda text, expected: expected.lower() in text.lower(),
    "response_not_contains": lambda text, expected: expected.lower() not in text.lower(),
    "response_matches_regex": lambda text, expected: re.search(expected, text) is not None,
}


class EvaluateError(RuntimeError):
    """The evaluation could not be set up."""


def lookup(scenario: Scenario, dotted: str) -> str | None:
    """Value at a dotted path in the scenario (`expected.answer`), or None when it is not there."""
    value: Any = scenario.model_dump()
    for part in dotted.split("."):
        if not isinstance(value, dict) or value.get(part) is None:
            return None
        value = value[part]
    return str(value)


def run_checks(checks: list[Check], scenario: Scenario, reply: str) -> list[dict[str, str]]:
    """Apply each check to the final reply. A scenario without the expected value skips the check."""
    out = []
    for check in checks:
        expected = lookup(scenario, check.expected_from)
        if expected is None:
            continue
        ok = CHECKS[check.type](reply, expected)
        out.append(
            {
                "check": check.type,
                "status": PASS if ok else FAIL,
                "reason": f"{'matched' if ok else 'did not match'} {expected!r}",
            }
        )
    return out


def scenario_status(result: dict[str, Any]) -> str:
    if result.get("invoke_error") or any(e["status"] == ERROR for e in result.get("evaluations", [])):
        return ERROR
    failed = any(c["status"] == FAIL for c in result["checks"]) or any(
        e["status"] == FAIL for e in result.get("evaluations", [])
    )
    return FAIL if failed else PASS


def compute_metrics(results: list[dict[str, Any]], config: EvaluationConfig, scored: bool) -> dict[str, float]:
    """Run-level metrics. `deterministic_pass_rate` counts scenarios whose checks all passed (of all scenarios)."""
    n = len(results)
    completed = [r for r in results if not r.get("invoke_error")]
    metrics = {
        "completion_rate": len(completed) / n,
        "deterministic_pass_rate": sum(all(c["status"] == PASS for c in r["checks"]) for r in completed) / n,
    }
    if scored:
        for evaluator in config.evaluators:
            passes = sum(
                e["status"] == PASS for r in results for e in r.get("evaluations", []) if e["evaluator"] == evaluator.id
            )
            metrics[evaluator_metric(evaluator.id)] = passes / n
    return metrics


def evaluate_gates(metrics: dict[str, float], config: EvaluationConfig) -> list[dict[str, Any]]:
    """Gates on metrics this run produced. Evaluator gates are skipped for suites that do not score."""
    return [
        {
            "gate": g.metric,
            "threshold": g.min,
            "value": metrics[g.metric],
            "status": PASS if metrics[g.metric] >= g.min else FAIL,
        }
        for g in config.gates
        if g.metric in metrics
    ]


def outcome(results: list[dict[str, Any]], gates: list[dict[str, Any]]) -> Outcome:
    if any(scenario_status(r) == ERROR for r in results):
        return ERROR
    return FAIL if any(g["status"] == FAIL for g in gates) else PASS


def dataset_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()[:16]


@dataclass
class Clients:
    """The AWS clients a run uses. Built from the current credentials unless a test passes its own."""

    control: Any
    agentcore: Any
    logs: Any
    xray: Any
    account: str

    @classmethod
    def create(cls, region: str) -> Clients:
        import boto3

        session = boto3.Session(region_name=region)
        return cls(
            control=session.client("bedrock-agentcore-control"),
            agentcore=session.client("bedrock-agentcore"),
            logs=session.client("logs"),
            xray=session.client("xray"),
            account=session.client("sts").get_caller_identity()["Account"],
        )


def evaluate_project(
    root: str | Path,
    env: str,
    suite: str,
    *,
    version: str = "latest",
    run_id: str | None = None,
    report_path: str | Path | None = None,
    commit: str = "unknown",
    ssm_client: Any | None = None,
    clients: Clients | None = None,
    out: Callable[[str], None] = print,
) -> tuple[Outcome, Path]:
    """Run `suite` against the agent in `env`. Returns the outcome and the report path."""
    root = Path(root)
    _, issues = validate_project(root)
    if issues:
        raise EvaluateError("the project is not valid:\n" + "\n".join(str(i) for i in issues))
    config = load_evaluation_config(root / "evals" / "evaluation.yaml")
    if suite not in config.suites:
        raise EvaluateError(f"suite '{suite}' is not defined in evaluation.yaml (have: {', '.join(config.suites)})")
    dataset_file = root / "evals" / config.suites[suite].dataset
    scenarios = load_dataset(dataset_file).scenarios
    scored = config.suites[suite].score

    definition = load_agent_definition(root / "agent.yaml")
    settings = load_settings(env, client=ssm_client)
    clients = clients or Clients.create(settings.region)
    if clients.account != settings.account:
        raise EvaluateError(f"credentials are for account {clients.account}, but {env} is {settings.account}")
    harness = hs.resolve_harness(
        clients.control, f"{settings.resource_prefix}_{definition.name}", version, settings.account, settings.region
    )
    out(
        f"{definition.name} | {env} | account {harness.account} {harness.region} | harness version {harness.version} "
        f"| suite {suite} ({len(scenarios)} scenarios) | source {commit}"
    )
    if scored:
        if not harness.runtime_arn:
            raise EvaluateError("the Harness has no runtime ARN, so its telemetry cannot be located")
        scoring.preflight(clients.xray, harness.region)

    started = datetime.now(UTC) - timedelta(minutes=1)
    qualifier = hs.create_endpoint(clients.control, harness, run_id)
    results: list[dict[str, Any]] = []
    sessions: dict[str, Scenario] = {}
    jobs: list[dict[str, Any]] = []
    deleter: threading.Thread | None = None
    try:
        for scenario in scenarios:
            session = f"{scenario.scenario_id}-{uuid.uuid4()}"  # <scenario_id>-<uuid4>: at least 33 characters
            result: dict[str, Any] = {"id": scenario.scenario_id, "session_id": session, "turns": [], "checks": []}
            try:
                reply = ""
                for turn in scenario.turns:
                    reply = hs.invoke(clients.agentcore, harness, qualifier, turn.input, session)
                    result["turns"].append({"input": turn.input, "reply": reply})
                result["checks"] = run_checks(config.checks, scenario, reply)  # checks apply to the final reply
                sessions[session] = scenario
            except hs.HarnessError as exc:
                result["invoke_error"] = str(exc)
            results.append(result)
            out(
                f"  {scenario_status(result):5} {result['id']}"
                + (f"  ({result['invoke_error']})" if "invoke_error" in result else "")
            )
        # All invocations are done: delete the endpoint in the background while scoring waits for the telemetry.
        deleter = threading.Thread(target=hs.delete_endpoint, args=(clients.control, harness, qualifier))
        deleter.start()
        if scored and sessions:
            out(f"scoring {len(sessions)} session(s) with {', '.join(e.id for e in config.evaluators)}")
            try:
                judged, jobs = scoring.score(
                    region=harness.region,
                    runtime_arn=harness.runtime_arn or "",
                    qualifier=qualifier,
                    sessions=sessions,
                    evaluators=config.evaluators,
                    started=started,
                    name=f"{definition.name}_{env}_{suite}",
                    agentcore=clients.agentcore,
                    logs=clients.logs,
                    out=lambda m: out(f"  {m}"),
                )
            except scoring.ScoringError as exc:
                out(f"error: scoring failed: {exc}")
                judged = {
                    sid: [
                        {"evaluator": e.id, "status": ERROR, "score": None, "reason": str(exc)}
                        for e in config.evaluators
                    ]
                    for sid in sessions
                }
            for r in results:
                r["evaluations"] = judged.get(r["session_id"], [])
                for e in r["evaluations"]:
                    out(f"  {e['status']:5} {r['id']} {e['evaluator']}: {e['reason'][:160]}")
    finally:
        if deleter is None:
            hs.delete_endpoint(clients.control, harness, qualifier)
        else:
            deleter.join()

    metrics = compute_metrics(results, config, scored)
    gates = evaluate_gates(metrics, config)
    for g in gates:
        out(f"  gate {g['gate']}: {g['value']:.2f} (needs >= {g['threshold']}) {g['status']}")
    result_outcome = outcome(results, gates)
    out(f"OUTCOME: {result_outcome}")

    now = datetime.now(UTC)
    report = {
        "agent": definition.name,
        "env": env,
        "suite": suite,
        "outcome": result_outcome,
        "commit": commit,
        "account": harness.account,
        "region": harness.region,
        "harness_id": harness.id,
        "harness_version": harness.version,
        "dataset": {"file": str(dataset_file.relative_to(root)), "hash": dataset_hash(dataset_file)},
        "run_at": now.isoformat(),
        "metrics": metrics,
        "batch_jobs": jobs,
        "gates": gates,
        "scenarios": results,
    }
    path = (
        Path(report_path)
        if report_path
        else root / "reports" / f"{definition.name}-{env}-{suite}-{now:%Y%m%dT%H%M%SZ}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    out(f"report: {path}")
    return result_outcome, path


def cleanup_run(
    root: str | Path,
    env: str,
    run_id: str,
    *,
    ssm_client: Any | None = None,
    clients: Clients | None = None,
) -> bool:
    """Delete the temporary endpoint of run `run_id` if it is still there. Used after a task that broke mid-run."""
    definition = load_agent_definition(Path(root) / "agent.yaml")
    settings = load_settings(env, client=ssm_client)
    clients = clients or Clients.create(settings.region)
    harness = hs.resolve_harness(
        clients.control, f"{settings.resource_prefix}_{definition.name}", "latest", settings.account, settings.region
    )
    return hs.remove_run_endpoint(clients.control, harness, run_id)


def summarize(report: dict[str, Any], out: Callable[[str], None] = print) -> None:
    """Print what a finished run looks like, from its report (the remote path has only the report)."""
    for r in report.get("scenarios", []):
        out(f"  {scenario_status(r):5} {r['id']}" + (f"  ({r['invoke_error']})" if "invoke_error" in r else ""))
        for e in r.get("evaluations", []):
            out(f"  {e['status']:5} {r['id']} {e['evaluator']}: {e['reason'][:160]}")
    for g in report.get("gates", []):
        out(f"  gate {g['gate']}: {g['value']:.2f} (needs >= {g['threshold']}) {g['status']}")
    out(f"OUTCOME: {report.get('outcome', ERROR)}" + (f" ({report['error']})" if "error" in report else ""))
