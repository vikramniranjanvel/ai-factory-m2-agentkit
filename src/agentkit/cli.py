"""The `agentkit` command line."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentkit", description="AgentKit CLI for the AI Factory.")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="check agent.yaml, evals/evaluation.yaml and the datasets")
    validate.add_argument("path", nargs="?", default=".", help="project directory (default: current directory)")
    validate.set_defaults(func=cmd_validate)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
