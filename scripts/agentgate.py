#!/usr/bin/env python3
"""AgentGate unified CLI.

This wrapper gives AI agents a stable entrypoint instead of asking every
agent to remember project-specific scripts.

Primary workflow:
    python governance/scripts/agentgate.py mr prepare --why "..."

The implementation delegates to the existing scripts so the policy stays in
one place.
"""
from __future__ import annotations

import sys


def _delegate(module_name: str, args: list[str]) -> int:
    previous_argv = sys.argv
    try:
        sys.argv = [previous_argv[0], *args]
        module = __import__(module_name)
        return int(module.main())
    finally:
        sys.argv = previous_argv


def _usage() -> str:
    return """AgentGate CLI

Usage:
  agentgate.py mr prepare [create_mr.py args...]
  agentgate.py mr verify [create_mr.py args...]
  agentgate.py mr create [create_mr.py args...]
  agentgate.py mr validate-open [gitlab_mr_compat.py args...]
  agentgate.py pr prepare [create_mr.py args...]
  agentgate.py pr verify [create_mr.py args...]
  agentgate.py pr create [create_mr.py args...]

Examples:
  python governance/scripts/agentgate.py mr prepare --why "修复广告生命周期超时状态"
  python governance/scripts/agentgate.py mr verify --diff-base origin/master
  python governance/scripts/agentgate.py mr create --why "修复广告生命周期超时状态"
  python governance/scripts/agentgate.py mr validate-open --diff-base origin/master
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args in (["-h"], ["--help"]):
        print(_usage())
        return 0

    if len(args) >= 2 and args[0] in ("mr", "pr") and args[1] == "create":
        return _delegate("create_mr", args[2:])

    if len(args) >= 2 and args[0] in ("mr", "pr") and args[1] == "prepare":
        return _delegate("create_mr", ["--prepare", *args[2:]])

    if len(args) >= 2 and args[0] in ("mr", "pr") and args[1] == "verify":
        return _delegate("create_mr", ["--verify-manifest", *args[2:]])

    if len(args) >= 2 and args[0] == "mr" and args[1] == "validate-open":
        return _delegate("gitlab_mr_compat", args[2:])

    sys.stderr.write(_usage())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
