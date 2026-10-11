"""The deployed agent as AgentCore sees it: find the Harness version under test, pin a temporary endpoint to it,
invoke it. Only endpoints named `eval_*` are ever created or deleted here."""

from __future__ import annotations

import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

ENDPOINT_PREFIX = "eval_"  # endpoint names allow letters, digits and underscores


class HarnessError(RuntimeError):
    """The Harness could not be found, pinned or invoked."""


@dataclass(frozen=True)
class Harness:
    """One exact Harness version."""

    id: str
    arn: str
    name: str
    version: str
    runtime_arn: str | None
    account: str
    region: str


def resolve_harness(control: Any, name: str, version: str, account: str, region: str) -> Harness:
    """Find the Harness called `name` (`<resource_prefix>_<agent name>`) and read `version` ('latest' or a number)."""
    summary, token = None, None
    while summary is None:
        page = control.list_harnesses(**({"nextToken": token} if token else {}))
        summary = next((h for h in page.get("harnesses", []) if h.get("harnessName") == name), None)
        token = page.get("nextToken")
        if summary is None and not token:
            raise HarnessError(f"no Harness named '{name}' in {account}/{region}. Deploy it first.")
    wanted = str(summary["harnessVersion"]) if version == "latest" else version
    if not wanted.isdigit():
        raise HarnessError(f"--version must be a number or 'latest', got '{version}'")
    try:
        harness = control.get_harness(harnessId=summary["harnessId"], harnessVersion=wanted)["harness"]
    except Exception as exc:
        raise HarnessError(f"cannot read Harness {summary['harnessId']} version {wanted}: {exc}") from exc
    if harness.get("status") != "READY":
        raise HarnessError(f"Harness version {wanted} is {harness.get('status')}, not READY")
    runtime = harness.get("environment", {}).get("agentCoreRuntimeEnvironment", {}).get("agentRuntimeArn")
    return Harness(
        id=harness["harnessId"],
        arn=harness["arn"],
        name=name,
        version=str(harness["harnessVersion"]),
        runtime_arn=runtime,
        account=account,
        region=region,
    )


def _status(control: Any, harness: Harness, name: str) -> str | None:
    try:
        return control.get_harness_endpoint(harnessId=harness.id, endpointName=name)["endpoint"]["status"]
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") == "ResourceNotFoundException":
            return None
        raise


def _wait(fetch: Any, done: Any, what: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        status = fetch()
        if done(status):
            return
        if status and status.endswith("_FAILED"):
            raise HarnessError(f"{what} ended in {status}")
        if time.monotonic() > deadline:
            raise HarnessError(f"timed out waiting for {what} (last status {status})")
        time.sleep(5)


def delete_endpoint(control: Any, harness: Harness, name: str) -> None:
    """Delete an `eval_` endpoint and wait for it to go. A failure is a warning: the next run sweeps leftovers."""
    if not name.startswith(ENDPOINT_PREFIX):
        raise HarnessError(f"refusing to delete endpoint '{name}': only {ENDPOINT_PREFIX}* endpoints are removed")
    try:
        control.delete_harness_endpoint(harnessId=harness.id, endpointName=name)
        _wait(lambda: _status(control, harness, name), lambda s: s is None, f"deleting {name}", 600)
    except Exception as exc:
        print(f"warning: could not delete endpoint {name}: {exc}", file=sys.stderr)


def sweep_stale(control: Any, harness: Harness, max_age_minutes: int = 60) -> None:
    """Delete `eval_` endpoints left by killed or cancelled runs (no run lasts this long)."""
    cutoff = datetime.now(UTC) - timedelta(minutes=max_age_minutes)
    for e in control.list_harness_endpoints(harnessId=harness.id).get("endpoints", []):
        if e["endpointName"].startswith(ENDPOINT_PREFIX) and e["createdAt"] < cutoff:
            print(f"removing stale endpoint {e['endpointName']}", file=sys.stderr)
            delete_endpoint(control, harness, e["endpointName"])


def create_endpoint(control: Any, harness: Harness) -> str:
    """Create an endpoint pinned to the version under test and wait until it is READY. Returns its name."""
    sweep_stale(control, harness)
    name = f"{ENDPOINT_PREFIX}{uuid.uuid4().hex[:8]}"
    try:
        control.create_harness_endpoint(harnessId=harness.id, endpointName=name, targetVersion=harness.version)
    except Exception as exc:
        raise HarnessError(f"could not create evaluation endpoint: {exc}") from exc
    try:
        _wait(lambda: _status(control, harness, name), lambda s: s == "READY", f"endpoint {name}", 300)
    except HarnessError:
        delete_endpoint(control, harness, name)
        raise
    return name


def _invoke_once(client: Any, harness: Harness, qualifier: str, prompt: str, session_id: str) -> str:
    try:
        reply = client.invoke_harness(
            harnessArn=harness.arn,
            qualifier=qualifier,
            runtimeSessionId=session_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
        )
        text = []
        for event in reply["stream"]:
            if "contentBlockDelta" in event:
                text.append(event["contentBlockDelta"].get("delta", {}).get("text", ""))
            for kind in ("internalServerException", "validationException", "runtimeClientError"):
                if kind in event:
                    raise HarnessError(f"stream error ({kind}): {event[kind].get('message')}")
        return "".join(text)
    except HarnessError:
        raise
    except Exception as exc:
        raise HarnessError(f"invoke failed: {exc}") from exc


def invoke(client: Any, harness: Harness, qualifier: str, prompt: str, session_id: str) -> str:
    """One user message, one reply. Retried once: the runtime occasionally answers 502 for a single call."""
    try:
        return _invoke_once(client, harness, qualifier, prompt, session_id)
    except HarnessError as exc:
        print(f"retrying once after: {str(exc)[:120]}", file=sys.stderr)
        time.sleep(5)
        return _invoke_once(client, harness, qualifier, prompt, session_id)
