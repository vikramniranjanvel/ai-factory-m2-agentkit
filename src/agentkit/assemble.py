"""Assemble the temporary native AgentCore project that `agentcore deploy` runs in.

Agent repositories hold content only. For each deploy AgentKit writes a throwaway native project:

    agentcore/agentcore.json   project manifest (credentials, harness location)
    agentcore/aws-targets.json one target, named after the environment
    agentcore/cdk/             the CDK app, copied from the installed @aws/agentcore package and patched (see below)
    app/<name>/                harness.json plus the prompt (system-prompt.md) and skills

No secrets are written here; the deploy step adds `.env.local` just before it runs the CLI.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from agentkit.compiler import compile_harness
from agentkit.schemas.agent_definition import AgentDefinition
from agentkit.settings import Settings

NATIVE_PACKAGE = "@aws/agentcore"
NATIVE_CLI_VERSION = "0.31.1"  # the one place the native CLI version is pinned; CI installs this version

# The library names a harness and its execution role `<project>_<harness>`, which must be unique per account.
# Passing the resource prefix as the project name gives `dev_<agent>`. The stack name is computed earlier from the
# project name in agentcore.json, so it stays `AgentCore-<agent>-<env>`.
PATCH_ANCHOR = "      spec,\n      mcpSpec,\n"
PATCH_TEMPLATE = (
    "      // AGENTKIT PATCH: name the harness and its role `<resource prefix>_<agent>` (unique per account).\n"
    "      spec: { ...spec, name: %s },\n"
    "      mcpSpec,\n"
)
TEMPLATE_RENAMES = {"gitignore.template": ".gitignore", "npmignore.template": ".npmignore"}
TEMPLATE_SKIP = {"test", "README.md"}


class AssembleError(RuntimeError):
    """The temporary project could not be assembled."""


def native_cli_root() -> Path:
    """Directory of the installed @aws/agentcore package, found from the `agentcore` executable."""
    exe = shutil.which("agentcore")
    if exe is None:
        raise AssembleError(
            f"the 'agentcore' CLI is not installed (npm install -g {NATIVE_PACKAGE}@{NATIVE_CLI_VERSION})"
        )
    for parent in Path(exe).resolve().parents:
        manifest = parent / "package.json"
        if manifest.exists() and json.loads(manifest.read_text()).get("name") == NATIVE_PACKAGE:
            return parent
    raise AssembleError(f"could not locate the {NATIVE_PACKAGE} package behind {exe}")


def _check_version(root: Path) -> None:
    version = json.loads((root / "package.json").read_text())["version"]
    if version != NATIVE_CLI_VERSION:
        raise AssembleError(
            f"{NATIVE_PACKAGE} {version} is installed but AgentKit is built for {NATIVE_CLI_VERSION}; "
            f"run: npm install -g {NATIVE_PACKAGE}@{NATIVE_CLI_VERSION}"
        )


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _copy_cdk(native_root: Path, dest: Path, resource_prefix: str) -> None:
    template = native_root / "dist" / "assets" / "cdk"
    if not template.is_dir():
        raise AssembleError(f"CDK template not found at {template}")
    for item in template.iterdir():
        if item.name in TEMPLATE_SKIP:
            continue
        target = dest / TEMPLATE_RENAMES.get(item.name, item.name)
        shutil.copytree(item, target) if item.is_dir() else shutil.copy2(item, target)
    entry = dest / "bin" / "cdk.ts"
    source = entry.read_text()
    if source.count(PATCH_ANCHOR) != 1:
        raise AssembleError(
            f"cannot patch {NATIVE_PACKAGE} CDK template: expected exactly one occurrence of the patch anchor in "
            "bin/cdk.ts (the template changed; update PATCH_ANCHOR)"
        )
    entry.write_text(source.replace(PATCH_ANCHOR, PATCH_TEMPLATE % json.dumps(resource_prefix)))


def assemble_project(
    definition: AgentDefinition,
    settings: Settings,
    env: str,
    root: str | Path,
    dest: str | Path,
    native_root: Path | None = None,
) -> Path:
    """Write the temporary native project for `definition` into the empty directory `dest` and return `dest`."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise AssembleError(f"{dest} is not empty")
    native_root = native_root or native_cli_root()
    _check_version(native_root)

    compiled = compile_harness(definition, settings, env, root)
    name = definition.name
    harness_dir = dest / "app" / name

    _write_json(harness_dir / "harness.json", compiled.spec)
    for copy in compiled.copies:
        target = harness_dir / copy.dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(copy.source, target) if copy.source.is_dir() else shutil.copy2(copy.source, target)

    _write_json(
        dest / "agentcore" / "agentcore.json",
        {
            "$schema": "https://schema.agentcore.aws.dev/v1/agentcore.json",
            "name": name,
            "version": 1,
            "managedBy": "CDK",
            "tags": {"agentcore:created-by": "agentkit", "agentcore:project-name": name},
            "runtimes": [],
            "memories": [],
            "knowledgeBases": [],
            "credentials": (
                [{"authorizerType": "ApiKeyCredentialProvider", "name": definition.model.credential}]
                if definition.model.credential
                else []
            ),
            "evaluators": [],
            "onlineEvalConfigs": [],
            "agentCoreGateways": [],
            "policyEngines": [],
            "configBundles": [],
            "abTests": [],
            "harnesses": [{"name": name, "path": f"app/{name}"}],
            "datasets": [],
            "capacityProviders": [],
            "payments": [],
        },
    )
    _write_json(
        dest / "agentcore" / "aws-targets.json",
        [{"name": env, "description": env, "account": settings.account, "region": settings.region}],
    )
    _copy_cdk(native_root, dest / "agentcore" / "cdk", settings.resource_prefix)
    return dest
