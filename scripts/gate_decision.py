#!/usr/bin/env python3
"""Compute a signed-by-context GateResult for CI-driven auto merge.

This module deliberately makes no platform API calls.  CI adapters provide the
current commit, policy and check evidence, then a separate Merge Bot consumes
the resulting decision.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from governance_common import ConfigError, load_config


DEFAULT_CONFIG: dict[str, Any] = {
    "auto_merge": {
        "enabled": True,
        "strategy": "squash",
        "delete_branch_after_merge": True,
        "require_up_to_date_branch": True,
        "require_all_required_checks": True,
        "required_checks": [],
        "protected_paths": [
            "governance.config.yml",
            ".github/workflows/**",
            ".gitlab-ci.yml",
            "ci/**",
            "governance/**",
            "CODEOWNERS",
            "scripts/scan_risks.py",
            "scripts/check_tested.py",
            "scripts/validate_mr.py",
            "scripts/gate_decision.py",
        ],
    }
}


def _changed_paths(diff_base: str, head: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{diff_base}...{head}", "--"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return [line.replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def _is_protected(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def build_gate_result(
    *,
    source_sha: str,
    target_sha: str,
    policy_sha: str,
    changed_paths: list[str],
    checks: dict[str, str],
    config: dict[str, Any],
    valid_approvals: int = 0,
) -> dict[str, Any]:
    auto = config.get("auto_merge", {})
    protected = [str(item) for item in auto.get("protected_paths", [])]
    critical_paths = [path for path in changed_paths if _is_protected(path, protected)]
    risk_level = "critical" if critical_paths else "medium"
    reasons: list[str] = []
    if critical_paths:
        reasons.append("protected_paths_changed")

    required = [str(item) for item in auto.get("required_checks", [])]
    if not required:
        required = sorted(checks)
    missing = [name for name in required if name not in checks]
    failed = [name for name in required if checks.get(name) != "pass"]
    if missing:
        reasons.append("required_check_missing")
    if failed:
        reasons.append("required_check_failed")

    required_approvals = 2 if risk_level == "critical" else 0
    if risk_level == "critical":
        reasons.append("critical_risk_requires_human_approval")
    if valid_approvals < required_approvals:
        reasons.append("approval_missing")

    checks_pass = not missing and not failed
    pass_result = checks_pass and not critical_paths and valid_approvals >= required_approvals
    enabled = bool(auto.get("enabled", True))
    if not enabled:
        reasons.append("auto_merge_disabled")
    if not enabled:
        result = "WAITING_APPROVAL"
        action = "WAIT"
    elif not pass_result:
        result = "WAITING_APPROVAL" if critical_paths and checks_pass else "FAIL"
        action = "WAIT" if critical_paths or "approval_missing" in reasons else "BLOCK"
    else:
        result = "PASS"
        action = "AUTO_MERGE"

    return {
        "schema_version": "v2",
        "result": result,
        "merge_action": action,
        "source_sha": source_sha,
        "target_sha": target_sha,
        "policy_sha": policy_sha,
        "risk_level": risk_level,
        "changed_paths": changed_paths,
        "required_checks": [
            {"name": name, "status": checks.get(name, "missing")} for name in required
        ],
        "approvals": {"required": required_approvals, "valid": valid_approvals},
        "blocking_reasons": sorted(set(reasons)),
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "decided_by": "gate-controller",
        "merge": {
            "strategy": auto.get("strategy", "squash"),
            "delete_branch_after_merge": bool(auto.get("delete_branch_after_merge", True)),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="计算 AgentGate GateResult v2")
    parser.add_argument("--evidence", required=True, help="CI evidence JSON 文件")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--target-sha", required=True)
    parser.add_argument("--policy-sha", required=True)
    parser.add_argument("--diff-base", required=True)
    parser.add_argument("--config", default=None)
    parser.add_argument("--output", required=True)
    parser.add_argument("--valid-approvals", type=int, default=0)
    args = parser.parse_args()

    try:
        config = load_config(args.config, DEFAULT_CONFIG, ("auto_merge",))
        # utf-8-sig 同时兼容 Linux CI 的 UTF-8 和 Windows/PowerShell 写出的 BOM。
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8-sig"))
        checks = evidence.get("checks", {})
        if not isinstance(checks, dict):
            raise ValueError("evidence.checks 必须是 mapping")
        changed = _changed_paths(args.diff_base, args.source_sha)
        gate = build_gate_result(
            source_sha=args.source_sha,
            target_sha=args.target_sha,
            policy_sha=args.policy_sha,
            changed_paths=changed,
            checks={str(k): str(v) for k, v in checks.items()},
            config=config,
            valid_approvals=args.valid_approvals,
        )
        Path(args.output).write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(gate, ensure_ascii=False))
        return 0 if gate["result"] == "PASS" else 1
    except (ConfigError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"[gate-decision] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
