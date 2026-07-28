#!/usr/bin/env python3
"""AgentGate unified CLI.

This wrapper gives AI agents a stable entrypoint instead of asking every
agent to remember project-specific scripts.

Primary workflow:
    python governance/scripts/agentgate.py mr create --why "..."

The implementation delegates to the existing scripts so the policy stays in
one place.
"""
from __future__ import annotations

import sys


def _usage() -> str:
    return """AgentGate CLI

Usage:
  agentgate.py mr create [create_mr.py args...]
  agentgate.py mr validate-open [gitlab_mr_compat.py args...]

Examples:
  python governance/scripts/agentgate.py mr create --why "修复广告生命周期超时状态"
  python governance/scripts/agentgate.py mr validate-open --diff-base origin/master
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args in (["-h"], ["--help"]):
        print(_usage())
        return 0

    if len(args) >= 2 and args[0] == "mr" and args[1] == "create":
        import create_mr

        sys.argv = [sys.argv[0], *args[2:]]
        return create_mr.main()

    if len(args) >= 2 and args[0] == "mr" and args[1] == "validate-open":
        import gitlab_mr_compat

        sys.argv = [sys.argv[0], *args[2:]]
        return gitlab_mr_compat.main()

    sys.stderr.write(_usage())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
