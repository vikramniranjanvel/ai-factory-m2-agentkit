"""The `agentkit` command line."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from agentkit.assemble import AssembleError
from agentkit.deploy import DeployError, deploy_project
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
