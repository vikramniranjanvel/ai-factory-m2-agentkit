"""The `agentkit` command line."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from agentkit.assemble import AssembleError
from agentkit.compiler import CompileError
from agentkit.deploy import DeployError, deploy_project
from agentkit.evaluate import EvaluateError, cleanup_run, evaluate_project
from agentkit.harness import HarnessError
from agentkit.init import InitError, init_project
from agentkit.promote import PromoteError, promote_project
from agentkit.remote import evaluate_remote
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
    except (DeployError, SettingsError, AssembleError, CompileError) as exc:
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
        if args.remote:
            result, _ = evaluate_remote(args.path, args.env, args.suite, version=args.version, report_path=args.report)
        else:
            result, _ = evaluate_project(
                args.path,
                args.env,
                args.suite,
                version=args.version,
                run_id=args.run_id,
                report_path=args.report,
                commit=git_commit(args.path),
            )
    except (EvaluateError, HarnessError, SettingsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return {"PASS": 0, "FAIL": 1, "ERROR": 2}[result]


def cmd_cleanup_endpoint(args: argparse.Namespace) -> int:
    try:
        removed = cleanup_run(args.path, args.env, args.run_id)
    except (EvaluateError, HarnessError, SettingsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"removed endpoint eval_{args.run_id}" if removed else f"no endpoint eval_{args.run_id}")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    try:
        promote_project(args.path, args.env, version=args.version, report=args.report)
    except (PromoteError, HarnessError, SettingsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    try:
        target = init_project(
            args.name,
            args.directory,
            description=args.description,
            provider=args.provider,
            model_id=args.model_id,
            credential=args.credential,
            repo=args.repo,
        )
    except InitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"created {target}")
    print("next:")
    print(f"  cd {target}")
    print("  edit agent.yaml, prompts/system.md and evals/, then: agentkit validate")
    print("  scripts/setup.sh        # GitHub repo and environments, registry entry, model keys")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentkit", description="AgentKit CLI for the AI Factory.")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="check agent.yaml, evals/evaluation.yaml and the datasets")
    validate.add_argument("path", nargs="?", default=".", help="project directory (default: current directory)")
    validate.set_defaults(func=cmd_validate)
    init = sub.add_parser("init", help="create a new agent project from the template")
    init.add_argument("name", help="agent name: letters and digits, for example supportTriage")
    init.add_argument("directory", nargs="?", help="where to create it (default: ./<name in kebab-case>)")
    init.add_argument("--description", help="one line about what the agent does")
    init.add_argument("--provider", default="open_ai", choices=["bedrock", "open_ai", "gemini", "lite_llm"])
    init.add_argument("--model-id", default="gpt-6-luna", help="model id for the provider")
    init.add_argument("--credential", default="openai-key", help="name of the model API key in SSM (not for bedrock)")
    init.add_argument("--repo", help="GitHub repository name (default: ai-factory-m2-<name in kebab-case>)")
    init.set_defaults(func=cmd_init)
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
    evaluate.add_argument(
        "--remote",
        action="store_true",
        help="run in the environment's evaluation infrastructure and wait for the report",
    )
    evaluate.add_argument("--run-id", help="name the temporary endpoint eval_<run id> (the evaluation infra sets this)")
    evaluate.set_defaults(func=cmd_evaluate)

    cleanup = sub.add_parser("cleanup-endpoint", help="delete the temporary endpoint of an evaluation run")
    cleanup.add_argument("path", nargs="?", default=".", help="project directory (default: current directory)")
    cleanup.add_argument("--env", required=True, help="environment name, for example dev")
    cleanup.add_argument("--run-id", required=True)
    cleanup.set_defaults(func=cmd_cleanup_endpoint)

    promote = sub.add_parser(
        "promote", help="make a Harness version the one the environment's dedicated endpoint serves (also rollback)"
    )
    promote.add_argument("path", nargs="?", default=".", help="project directory (default: current directory)")
    promote.add_argument("--env", required=True, help="environment name, for example prod")
    promote.add_argument(
        "--version", default="latest", help="Harness version number, or 'latest'; an older one rolls back"
    )
    promote.add_argument("--report", help="evaluation report that must be a PASS of exactly this version")
    promote.set_defaults(func=cmd_promote)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
