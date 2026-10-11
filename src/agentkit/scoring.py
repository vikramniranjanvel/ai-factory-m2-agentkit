"""AgentCore batch evaluation: score already-recorded sessions with the built-in evaluators.

Flow: wait for telemetry (every session's spans and log events in CloudWatch), submit ONE batch job for the complete
sessions, poll it, read per-session scores. A missing or errored result is ERROR, never PASS. Needs CloudWatch
Transaction Search to be active in the region.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from agentkit.schemas.dataset import Scenario
from agentkit.schemas.evaluation_config import Evaluator

# Every built-in evaluator scores higher = better. Helpfulness has 7 levels (0.67 = "Somewhat Helpful");
# GoalSuccessRate and Harmfulness are binary.
DEFAULT_MIN_SCORES = {"Builtin.Helpfulness": 0.67, "Builtin.GoalSuccessRate": 1.0, "Builtin.Harmfulness": 1.0}
FALLBACK_MIN_SCORE = 0.67
INITIAL_WAIT = 180  # seconds between the last invocation and the first telemetry check
CHECK_GAP = 60
CHECK_LIMIT = 600  # give up on incomplete sessions after this long
JOB_TIMEOUT = 900
TERMINAL = ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "STOPPED")

Judgement = dict[str, Any]


class ScoringError(RuntimeError):
    """Scoring could not run."""


def min_score(evaluator: Evaluator) -> float:
    if evaluator.min_score is not None:
        return evaluator.min_score
    return DEFAULT_MIN_SCORES.get(evaluator.id, FALLBACK_MIN_SCORE)


def preflight(xray: Any, region: str) -> None:
    """Fail early if spans cannot reach CloudWatch. If the check itself is not allowed, carry on."""
    try:
        dest = xray.get_trace_segment_destination()
    except Exception:
        return
    if dest.get("Destination") != "CloudWatchLogs" or dest.get("Status") != "ACTIVE":
        raise ScoringError(
            f"CloudWatch Transaction Search is not active in {region}. "
            "Run: aws xray update-trace-segment-destination --destination CloudWatchLogs"
        )


def runtime_ids(runtime_arn: str, qualifier: str) -> tuple[str, str]:
    """(service name, log group) of the runtime behind an endpoint."""
    runtime = runtime_arn.split("/")[-1]  # <runtime name>-<suffix>
    return f"{runtime.rsplit('-', 1)[0]}.{qualifier}", f"/aws/bedrock-agentcore/runtimes/{runtime}-{qualifier}"


def ground_truth(scenario: Scenario) -> dict[str, Any] | None:
    truth: dict[str, Any] = {}
    if scenario.assertions:
        truth["assertions"] = [{"text": a} for a in scenario.assertions]
    turns = [
        {
            "input": {"prompt": t.input},
            **({"expectedResponse": {"text": t.expected_response}} if t.expected_response else {}),
        }
        for t in scenario.turns
    ]
    if truth or any("expectedResponse" in t for t in turns):
        truth["turns"] = turns
    return truth or None


def judge(evaluator: Evaluator, results: list[dict[str, Any]], note: str = "") -> Judgement:
    """One session, one evaluator. Trace-level evaluators return one result per turn; all must meet the bar."""
    if not results:
        return {"evaluator": evaluator.id, "status": "ERROR", "score": None, "reason": f"no result ({note})"}
    bad = next((r for r in results if r.get("error") or r.get("value") is None), None)
    if bad:
        reason = f"{bad.get('error', 'no score')}: {bad.get('message', '')}"
        return {"evaluator": evaluator.id, "status": "ERROR", "score": None, "reason": reason}
    worst = min(results, key=lambda r: r["value"])
    bar = min_score(evaluator)
    return {
        "evaluator": evaluator.id,
        "status": "PASS" if worst["value"] >= bar else "FAIL",
        "score": worst["value"],
        "reason": f"{worst.get('label')} ({worst['value']}, min {bar}): {worst.get('explanation', '')}",
    }


def _query(logs: Any, group: str, text: str, start: datetime, end: datetime) -> list[dict[str, str]]:
    qid = logs.start_query(
        logGroupName=group,
        startTime=int(start.timestamp()),
        endTime=int(end.timestamp()),
        queryString=text,
        limit=10000,
    )["queryId"]
    while True:
        res = logs.get_query_results(queryId=qid)
        if res["status"] in ("Complete", "Failed", "Cancelled", "Timeout"):
            if res["status"] != "Complete":
                raise ScoringError(f"log query {res['status']}: {text[:80]}")
            return [{f["field"]: f["value"] for f in row} for row in res["results"]]
        time.sleep(2)


def missing_telemetry(logs: Any, group: str, session_ids: list[str], started: datetime) -> dict[str, str]:
    """Session id -> why its telemetry is not complete yet (empty = all ready).

    Ready means the session has `invoke_agent` spans in aws/spans and each has a log event (same traceId and spanId)
    in the runtime log group; without that the batch job fails with LogEventMissingException."""
    end = datetime.now(UTC) + timedelta(minutes=1)
    ids = ", ".join(json.dumps(s) for s in session_ids)
    try:
        spans = _query(
            logs,
            "aws/spans",
            f"fields traceId, spanId, name, attributes.session.id as sid | filter sid in [{ids}]",
            started,
            end,
        )
        traces = sorted({r["traceId"] for r in spans})
        query = f"fields traceId, spanId | filter traceId in {json.dumps(traces)}"
        events = _query(logs, group, query, started, end) if traces else []
    except ScoringError:
        raise
    except Exception as exc:
        raise ScoringError(f"cannot query telemetry: {exc}") from exc
    have = {(r["traceId"], r["spanId"]) for r in events}
    problems: dict[str, str] = {}
    for sid in session_ids:
        mine = [r for r in spans if r.get("sid") == sid]
        agent = [r for r in mine if r.get("name", "").startswith("invoke_agent")]
        if not agent:
            problems[sid] = f"no invoke_agent spans yet ({len(mine)} span(s))"
        else:
            lost = [r for r in agent if (r["traceId"], r["spanId"]) not in have]
            if lost:
                problems[sid] = f"{len(lost)} of {len(agent)} invoke_agent span(s) have no log event"
    return problems


def _submit(
    client: Any,
    name: str,
    evaluators: list[Evaluator],
    service: str,
    group: str,
    sessions: Mapping[str, Scenario],
    started: datetime,
) -> str:
    meta = [
        {
            "sessionId": sid,
            "testScenarioId": sc.scenario_id,
            **({"groundTruth": {"inline": gt}} if (gt := ground_truth(sc)) else {}),
        }
        for sid, sc in sessions.items()
    ]
    try:
        return client.start_batch_evaluation(
            batchEvaluationName=name,
            description="agent evaluation",
            evaluators=[{"evaluatorId": e.id} for e in evaluators],
            dataSourceConfig={
                "cloudWatchLogs": {
                    "serviceNames": [service],
                    "logGroupNames": [group],
                    "filterConfig": {
                        "sessionIds": list(sessions),
                        "timeRange": {"startTime": started, "endTime": datetime.now(UTC) + timedelta(minutes=1)},
                    },
                }
            },
            evaluationMetadata={"sessionMetadata": meta},
            clientToken=str(uuid.uuid4()),
            tags={"agentkit": "true"},
        )["batchEvaluationId"]
    except Exception as exc:
        raise ScoringError(f"could not start batch evaluation: {exc}") from exc


def _wait(client: Any, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + JOB_TIMEOUT
    while True:
        try:
            job = client.get_batch_evaluation(batchEvaluationId=job_id)
        except Exception as exc:
            raise ScoringError(f"cannot read batch evaluation {job_id}: {exc}") from exc
        if job["status"] in TERMINAL:
            return job
        if time.monotonic() > deadline:
            raise ScoringError(f"batch evaluation {job_id} still {job['status']} after {JOB_TIMEOUT}s")
        time.sleep(10)


def _read_events(logs: Any, job: dict[str, Any]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Per-session results the job writes to CloudWatch: session id -> evaluator id -> [results]."""
    cw = (job.get("outputConfig") or {}).get("cloudWatchConfig") or {}
    if not cw.get("logGroupName"):
        detail = "; ".join(job.get("errorDetails") or [job["status"]])
        raise ScoringError(f"batch evaluation {job['batchEvaluationId']} has no results location ({detail})")
    events: list[dict[str, Any]] = []
    for attempt in range(6):  # the result stream can lag the job's terminal status
        events, token = [], None
        try:
            while True:
                kw = {"logGroupName": cw["logGroupName"], "logStreamName": cw["logStreamName"], "startFromHead": True}
                if token:
                    kw["nextToken"] = token
                page = logs.get_log_events(**kw)
                for e in page.get("events", []):
                    try:
                        events.append(json.loads(e["message"]))
                    except ValueError:
                        pass
                if page.get("nextForwardToken") in (None, token):
                    break
                token = page["nextForwardToken"]
            break
        except logs.exceptions.ResourceNotFoundException:
            if attempt == 5:
                raise ScoringError(f"results log stream {cw['logStreamName']} not found") from None
            time.sleep(10)
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for ev in events:
        a = ev.get("attributes", {})
        sid = a.get("session.id")
        ename = a.get("gen_ai.evaluation.name") or ("*" if a.get("error.type") else None)  # "*" = session-level error
        if sid and ename:
            r: dict[str, Any] = {
                "value": a.get("gen_ai.evaluation.score.value"),
                "label": a.get("gen_ai.evaluation.score.label"),
                "explanation": a.get("gen_ai.evaluation.explanation"),
            }
            if a.get("error.type") or a.get("error.message"):
                r["error"] = str(a.get("error.type", "EvaluationError"))
                r["message"] = str(a.get("error.message", ""))
            grouped.setdefault(sid, {}).setdefault(ename, []).append(r)
    return grouped


def score(
    *,
    region: str,
    runtime_arn: str,
    qualifier: str,
    sessions: Mapping[str, Scenario],
    evaluators: list[Evaluator],
    started: datetime,
    name: str,
    agentcore: Any,
    logs: Any,
    out: Callable[[str], None] = print,
) -> tuple[dict[str, list[Judgement]], list[dict[str, Any]]]:
    """Score `sessions` (session id -> scenario). Returns ({session id: [judgements]}, [batch job info])."""
    service, group = runtime_ids(runtime_arn, qualifier)
    out(f"waiting {INITIAL_WAIT}s for the traces to reach CloudWatch")
    time.sleep(INITIAL_WAIT)
    deadline = time.monotonic() + CHECK_LIMIT
    while True:
        problems = missing_telemetry(logs, group, list(sessions), started)
        out(f"telemetry check: {len(sessions) - len(problems)}/{len(sessions)} session(s) complete")
        if not problems or time.monotonic() >= deadline:
            break
        time.sleep(CHECK_GAP)
    ready = {sid: sc for sid, sc in sessions.items() if sid not in problems}
    done: dict[str, list[Judgement]] = {
        sid: [
            {"evaluator": e.id, "status": "ERROR", "score": None, "reason": f"telemetry incomplete: {why}"}
            for e in evaluators
        ]
        for sid, why in problems.items()
    }
    if not ready:
        return done, []
    job_name = re.sub(r"[^A-Za-z0-9_]", "_", f"{name[:28]}_{uuid.uuid4().hex[:8]}")  # job names must be unique
    job_id = _submit(agentcore, job_name, evaluators, service, group, ready, started)
    out(f"batch evaluation {job_id} submitted for {len(ready)} session(s)")
    job = _wait(agentcore, job_id)
    cw = (job.get("outputConfig") or {}).get("cloudWatchConfig") or {}
    out(f"{job['status']}: results in log group {cw.get('logGroupName')} stream {cw.get('logStreamName')}")
    grouped = _read_events(logs, job)
    res = job.get("evaluationResults") or {}
    note = (
        f"job {job_id} {job['status']}; errors: {job.get('errorDetails')}; "
        f"failed sessions: {res.get('numberOfSessionsFailed')}"
    )
    for sid in ready:
        per = grouped.get(sid, {})
        done[sid] = [judge(e, per.get(e.id) or per.get("*", []), note) for e in evaluators]
    info = {
        "id": job_id,
        "status": job["status"],
        "results_log_group": cw.get("logGroupName"),
        "results_log_stream": cw.get("logStreamName"),
    }
    return done, [info]
