"""Point an environment's dedicated endpoint at a Harness version: `agentkit promote --env prod`.

Deploying only creates a Harness version; evaluation runs against temporary endpoints. This is the release step that
makes a version the one callers get. Running it with an older `--version` is the rollback. Versions are local to an
environment, so what is promoted is the content that was deployed there, never a version number from another one.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentkit import harness as hs
from agentkit.schemas.agent_definition import load_agent_definition
from agentkit.settings import load_settings

ENDPOINT = "live"
TIMEOUT = 600


class PromoteError(RuntimeError):
    """The endpoint could not be moved."""


def check_report(report: dict[str, Any], agent: str, env: str, harness: hs.Harness) -> None:
    """The evaluation named by `--report` must be a pass of this agent's exact Harness version in this environment."""
    found = (report.get("agent"), report.get("env"), str(report.get("harness_version")))
    if found != (agent, env, harness.version):
        raise PromoteError(
            f"the report is for {found[0]} in {found[1]} at version {found[2]}, not {agent} in {env} at version "
            f"{harness.version}"
        )
    if report.get("outcome") != "PASS":
        raise PromoteError(f"the report's outcome is {report.get('outcome')}, only PASS can be promoted")


def _get(control: Any, harness_id: str) -> dict[str, Any] | None:
    try:
        return control.get_harness_endpoint(harnessId=harness_id, endpointName=ENDPOINT)["endpoint"]
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") == "ResourceNotFoundException":
            return None
        raise


def promote_project(
    root: str | Path,
    env: str,
    *,
    version: str = "latest",
    report: str | Path | None = None,
    manifest_path: str | Path | None = None,
    ssm_client: Any | None = None,
    control: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Make `version` (default: the latest) the one served by the `live` endpoint of the agent in `env`."""
    root = Path(root)
    definition = load_agent_definition(root / "agent.yaml")
    settings = load_settings(env, definition.project, client=ssm_client)
    if control is None:
        import boto3

        control = boto3.client("bedrock-agentcore-control", region_name=settings.region)
    harness = hs.resolve_harness(
        control, f"{settings.resource_prefix}_{definition.name}", version, settings.account, settings.region
    )

    evidence = None
    if report:
        data = json.loads(Path(report).read_text())
        check_report(data, definition.name, env, harness)
        evidence = {"file": Path(report).name, "outcome": data["outcome"], "run_at": data.get("run_at")}
    else:
        out("warning: no --report given, promoting without evidence of an evaluation")

    path = Path(manifest_path) if manifest_path else root / "reports" / f"release-{definition.name}-{env}.json"
    current = _get(control, harness.id)
    previous = str(current["liveVersion"]) if current and current.get("liveVersion") else None
    if current is None:
        control.create_harness_endpoint(harnessId=harness.id, endpointName=ENDPOINT, targetVersion=harness.version)
    elif previous != harness.version:
        control.update_harness_endpoint(harnessId=harness.id, endpointName=ENDPOINT, targetVersion=harness.version)
    unchanged = previous == harness.version
    out(f"{harness.name}: '{ENDPOINT}' {previous or 'does not exist'} -> {harness.version}")

    deadline = time.monotonic() + TIMEOUT
    while True:
        endpoint = _get(control, harness.id) or {}
        status = endpoint.get("status", "MISSING")
        if status == "READY" and str(endpoint.get("liveVersion")) == harness.version:
            break
        if status.endswith("FAILED") or time.monotonic() > deadline:
            raise PromoteError(
                f"endpoint '{ENDPOINT}' is {status} (live {endpoint.get('liveVersion')}, target {harness.version}): "
                f"{endpoint.get('failureReason')}"
            )
        sleep(5)

    if unchanged and path.exists():
        # Nothing moved: keep the manifest of the release that did, with its previous version.
        out(f"'{ENDPOINT}' already serves version {harness.version}; manifest {path} unchanged")
        return json.loads(path.read_text())

    manifest = {
        "agent": definition.name,
        "env": env,
        "harness": harness.name,
        "harness_id": harness.id,
        "endpoint": ENDPOINT,
        "version": harness.version,
        "previous_version": None if unchanged else previous,
        "commit": os.environ.get("GITHUB_SHA", "local"),
        "evaluation": evidence,
        "promoted_at": datetime.now(UTC).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    out(f"'{ENDPOINT}' now serves version {harness.version}; manifest {path}")
    return manifest
