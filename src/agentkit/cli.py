"""The `agentkit` command line."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from agentkit.assemble import AssembleError
from agentkit.deploy import DeployError, deploy_project
from agentkit.evaluate import EvaluateError, evaluate_project
from agentkit.harness import HarnessError
from agentkit.settings import SettingsError
from agentkit.validate import validate_project


def cmd_validate(args: argparse.Namespace) -> int:
    checked, issues = validate_project(args.path)
    for issue in issues:
        print(issue, file=sys.stderr)
    if issues:
        files = len({i.file for i in issues})
        print(f"\n{len(issues)} problem(s) in {files} file(s)", file=sys.stderr)
        return 1
    print(f"OK: {', '.join(checked)}")
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    try:
        deploy_project(args.path, args.env, use_role=not args.no_assume_role, keep=args.keep)
    except (DeployError, SettingsError, AssembleError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"deployed to {args.env}")
    return 0


def git_commit(path: str) -> str:
    import subprocess

    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=path, capture_output=True, text=True, check=True
        )
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=path, capture_output=True, text=True, check=True)
        return sha.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def cmd_evaluate(args: argparse.Namespace) -> int:
    try:
        result, _ = evaluate_project(
            args.path, args.env, args.suite, version=args.version, report_path=args.report, commit=git_commit(args.path)
        )
    except (EvaluateError, HarnessError, SettingsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return {"PASS": 0, "FAIL": 1, "ERROR": 2}[result]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentkit", description="AgentKit CLI for the AI Factory.")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="check agent.yaml, evals/evaluation.yaml and the datasets")
    validate.add_argument("path", nargs="?", default=".", help="project directory (default: current directory)")
    validate.set_defaults(func=cmd_validate)
    deploy = sub.add_parser("deploy", help="deploy the agent to an environment")
    deploy.add_argument("path", nargs="?", default=".", help="project directory (default: current directory)")
    deploy.add_argument("--env", required=True, help="environment name, for example dev")
    deploy.add_argument(
        "--no-assume-role", action="store_true", help="use the current AWS credentials instead of the deploy role"
    )
    deploy.add_argument("--keep", action="store_true", help="keep the temporary project (it holds the API key)")
    deploy.set_defaults(func=cmd_deploy)

    evaluate = sub.add_parser(
        "evaluate", help="run an evaluation suite against the deployed agent (exit 0 pass, 1 fail, 2 error)"
    )
    evaluate.add_argument("path", nargs="?", default=".", help="project directory (default: current directory)")
    evaluate.add_argument("--env", required=True, help="environment name, for example dev")
    evaluate.add_argument("--suite", default="smoke", help="suite name from evals/evaluation.yaml (default: smoke)")
    evaluate.add_argument("--version", default="latest", help="Harness version number, or 'latest'")
    evaluate.add_argument("--report", help="report path (default: reports/<agent>-<env>-<suite>-<time>.json)")
    evaluate.set_defaults(func=cmd_evaluate)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
