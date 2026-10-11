"""Run an evaluation in the environment's shared evaluation infrastructure: `agentkit evaluate --remote`.

The project bundle goes to the results bucket, the Step Functions state machine runs `agentkit evaluate` as a Fargate
task, and the report comes back from the bucket. Caller permissions are small on purpose (upload the bundle, start the
state machine, read the report): the task role does the AgentCore work. Both ends come from SSM settings.
"""

from __future__ import annotations

import io
import json
import tarfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentkit.evaluate import EvaluateError, Outcome, summarize
from agentkit.schemas.agent_definition import load_agent_definition
from agentkit.settings import load_settings
from agentkit.validate import validate_project

POLL_SECONDS = 10
SKIP = {".git", "node_modules", "reports", ".venv", "__pycache__", "agentcore"}


def bundle(root: Path) -> bytes:
    """The project as a .tgz: everything the evaluation needs (agent.yaml, prompts, evals), nothing local."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if path.is_file() and not (set(rel.parts) & SKIP):
                tar.add(path, arcname=str(rel))
    return buffer.getvalue()


def evaluate_remote(
    root: str | Path,
    env: str,
    suite: str,
    *,
    version: str = "latest",
    report_path: str | Path | None = None,
    ssm_client: Any | None = None,
    s3: Any | None = None,
    sfn: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> tuple[Outcome, Path]:
    root = Path(root)
    _, issues = validate_project(root)
    if issues:
        raise EvaluateError("the project is not valid:\n" + "\n".join(str(i) for i in issues))
    settings = load_settings(env, client=ssm_client)
    settings.require("eval_state_machine_arn", "eval_results_bucket")
    name = load_agent_definition(root / "agent.yaml").name
    if s3 is None or sfn is None:
        import boto3

        s3 = s3 or boto3.client("s3", region_name=settings.region)
        sfn = sfn or boto3.client("stepfunctions", region_name=settings.region)

    run_id = uuid.uuid4().hex[:8]
    bucket = settings.eval_results_bucket
    input_key = f"inputs/{run_id}/project.tgz"
    report_key = f"results/{name}/{env}/{run_id}/report.json"
    s3.put_object(Bucket=bucket, Key=input_key, Body=bundle(root))
    execution = sfn.start_execution(
        stateMachineArn=settings.eval_state_machine_arn,
        name=f"{name}-{suite}-{run_id}",
        input=json.dumps(
            {
                "runId": run_id,
                "targetEnv": env,
                "suite": suite,
                "version": version,
                "inputKey": input_key,
                "reportKey": report_key,
            }
        ),
    )["executionArn"]
    out(f"{name} | {env} | suite {suite} | run {run_id} | execution {execution}")

    while True:
        state = sfn.describe_execution(executionArn=execution)
        if state["status"] != "RUNNING":
            break
        sleep(POLL_SECONDS)

    path = Path(report_path) if report_path else root / "reports" / f"{name}-{env}-{suite}-{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if state["status"] != "SUCCEEDED":
        why = state.get("cause") or state.get("error") or state["status"]
        report = {"outcome": "ERROR", "error": f"evaluation execution {state['status']}: {why}", "run_id": run_id}
    else:
        try:
            report = json.loads(s3.get_object(Bucket=bucket, Key=report_key)["Body"].read())
        except Exception as exc:
            report = {"outcome": "ERROR", "error": f"cannot read s3://{bucket}/{report_key}: {exc}", "run_id": run_id}
    path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    summarize(report, out)
    out(f"report: {path} (s3://{bucket}/{report_key})")
    result: Outcome = report.get("outcome", "ERROR")
    return result, path
