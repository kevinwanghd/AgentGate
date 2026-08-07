#!/usr/bin/env python3
"""Compute a signed-by-context GateResult for CI-driven auto merge.

This module deliberately makes no platform API calls.  CI adapters provide the
current commit, policy and check evidence, then a separate Merge Bot consumes
the resulting decision.

// risk:untested reason:"covered by tests/test_regressions.py::GateDecisionTests, CI runs pytest against diff" owner:@kevin reviewed:2026-07-23
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

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None


DEFAULT_CONFIG: dict[str, Any] = {
    "auto_merge": {
        "enabled": True,
        "strategy": "squash",
        "delete_branch_after_merge": True,
        "require_up_to_date_branch": True,
        "require_all_required_checks": True,
        "required_checks": [],
        "required_checks_by_risk": {
            "low": ["risk-scan", "secret-scan", "mr-validate"],
            "medium": ["risk-scan", "secret-scan", "mr-validate", "test-check"],
            "high": ["risk-scan", "secret-scan", "mr-validate", "test-check", "selftest"],
            "critical": ["risk-scan", "secret-scan", "mr-validate", "test-check", "selftest"],
        },
        # critical 风险时需要的人工审批数（默认 1）
        "high_approvals": 0,
        "critical_approvals": 1,
        "risk_paths": {
            "low": ["docs/**", "*.md"],
            "high": ["**/auth/**", "**/payment/**", "**/deploy/**"],
            "critical": [],
        },
        # 保护分支: 仅对直推（pipeline_kind="push"）流水线生效，命中时禁止 bot 自动合并。
        # MR 流水线合入受保护分支是正常路径，不触发阻断；真正的"禁止直推"由平台侧
        # 保护分支设置兜底（GitLab Protected Branches / GitHub Branch Protection）。
        "protected_branches": [
            "master",
            "main",
            "release/*",
        ],
        "protected_paths": [
            "AGENTS.md",
            "CLAUDE.md",
            ".hermes.md",
            ".github/copilot-instructions.md",
            ".cursor/rules/**",
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


def load_policy_from_target_branch(target_ref: str, config_path: str) -> dict[str, Any]:
    """Load policy from the trusted target ref, never from the PR worktree."""
    path = Path(config_path)
    if not target_ref or path.is_absolute() or ".." in path.parts:
        raise ConfigError("target policy requires a safe repository-relative config path")
    result = subprocess.run(
        ["git", "show", f"{target_ref}:{path.as_posix()}"],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    if yaml is None:
        raise ConfigError("PyYAML is required to load target policy")
    policy = yaml.safe_load(result.stdout) or {}
    if not isinstance(policy, dict):
        raise ConfigError("target policy must be a mapping")
    return policy


def _is_protected(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _max_risk(left: str, right: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    return left if order[left] >= order[right] else right


def _classify_risk(changed_paths: list[str], auto: dict[str, Any], critical_paths: list[str]) -> str:
    if critical_paths:
        return "critical"
    risk = "low"
    risk_paths = auto.get("risk_paths", {})
    if not isinstance(risk_paths, dict):
        risk_paths = {}
    low_patterns = [str(item) for item in risk_paths.get("low", [])]
    high_patterns = [str(item) for item in risk_paths.get("high", [])]
    critical_patterns = [str(item) for item in risk_paths.get("critical", [])]
    for path in changed_paths:
        if _is_protected(path, critical_patterns):
            risk = _max_risk(risk, "critical")
        elif _is_protected(path, high_patterns):
            risk = _max_risk(risk, "high")
        elif _is_protected(path, low_patterns):
            risk = _max_risk(risk, "low")
        else:
            risk = _max_risk(risk, "medium")
    return risk


def _required_checks_for_risk(auto: dict[str, Any], risk_level: str, checks: dict[str, str]) -> list[str]:
    by_risk = auto.get("required_checks_by_risk")
    if isinstance(by_risk, dict):
        plan = by_risk.get(risk_level)
        if isinstance(plan, list):
            return [str(item) for item in plan]
    required = [str(item) for item in auto.get("required_checks", [])]
    return required or sorted(checks)


def build_gate_result(
    *,
    source_sha: str,
    target_sha: str,
    policy_sha: str,
    changed_paths: list[str],
    checks: dict[str, str],
    config: dict[str, Any],
    valid_approvals: int = 0,
    target_branch: str = "",
    pipeline_kind: str = "mr",
) -> dict[str, Any]:
    auto = config.get("auto_merge", {})
    protected = [str(item) for item in auto.get("protected_paths", [])]
    critical_paths = [path for path in changed_paths if _is_protected(path, protected)]
    protected_branches = [str(item) for item in auto.get("protected_branches", [])]
    is_protected_branch = bool(target_branch) and any(
        fnmatch.fnmatch(target_branch, pattern) for pattern in protected_branches
    )
    # 只有直推（push）流水线命中保护分支才需要门禁拦截；MR 合入受保护分支是正常路径。
    is_direct_push_on_protected = is_protected_branch and pipeline_kind == "push"
    risk_level = _classify_risk(changed_paths, auto, critical_paths)
    reasons: list[str] = []
    if critical_paths:
        reasons.append("protected_paths_changed")
    if is_direct_push_on_protected:
        reasons.append("protected_branch_direct_push")

    required = _required_checks_for_risk(auto, risk_level, checks)
    missing = [name for name in required if name not in checks]
    failed = [name for name in required if name in checks and checks.get(name) != "pass"]
    if missing:
        reasons.append("required_check_missing")
    if failed:
        reasons.append("required_check_failed")

    if risk_level == "critical":
        required_approvals = int(auto.get("critical_approvals", 1))
    elif risk_level == "high":
        required_approvals = int(auto.get("high_approvals", 0))
    else:
        required_approvals = 0
    if risk_level == "critical":
        reasons.append("critical_risk_requires_human_approval")
    if valid_approvals < required_approvals:
        reasons.append("approval_missing")

    # 先计算 result（ERROR > FAIL > WAITING_APPROVAL > PASS），再决定 action
    checks_pass = not missing and not failed
    pass_result = checks_pass and not critical_paths and not is_direct_push_on_protected and valid_approvals >= required_approvals

    # 决定 result（与 auto_merge.enabled 无关）
    if missing:
        result = "ERROR"  # required check 缺失属基础设施错误
        action = "BLOCK"
    elif is_direct_push_on_protected:
        # 受保护分支必须通过 MR 才能合并，直推流水线上禁止 bot 自动合并
        result = "FAIL"
        action = "BLOCK"
        reasons.append("protected_branch_requires_mr")
    elif not pass_result:
        result = "WAITING_APPROVAL" if critical_paths and checks_pass else "FAIL"
        action = "WAIT" if critical_paths or "approval_missing" in reasons else "BLOCK"
    else:
        result = "PASS"
        # auto_merge.enabled 只影响 action，不影响 result
        enabled = bool(auto.get("enabled", True))
        if not enabled:
            reasons.append("auto_merge_disabled")
            action = "MANUAL_MERGE"
        else:
            action = "AUTO_MERGE"

    return {
        "schema_version": "v2",
        "result": result,
        "merge_action": action,
        "pipeline_kind": pipeline_kind,
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
    parser.add_argument("--target-branch", default="", help="目标分支名；配合 --pipeline-kind push 做保护分支检查")
    parser.add_argument(
        "--pipeline-kind",
        choices=("mr", "push"),
        default="mr",
        help="流水线类型：mr=MR/PR 流水线（默认）；push=直推流水线，命中保护分支时阻断 bot 自动合并",
    )
    parser.add_argument("--target-ref", help="trusted target ref used to load policy")
    args = parser.parse_args()

    try:
        config = (
            load_policy_from_target_branch(args.target_ref, "governance.config.yml")
            if args.target_ref
            else load_config(args.config, DEFAULT_CONFIG, ("auto_merge",))
        )
        # utf-8-sig 同时兼容 Linux CI 的 UTF-8 和 Windows/PowerShell 写出的 BOM。
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8-sig"))
        for key, expected in (
            ("source_sha", args.source_sha),
            ("target_sha", args.target_sha),
            ("policy_sha", args.policy_sha),
        ):
            actual = evidence.get(key)
            if actual is not None and str(actual) != expected:
                raise ValueError(f"evidence.{key} 与当前 CI 上下文不一致")
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
            target_branch=args.target_branch,
            pipeline_kind=args.pipeline_kind,
        )
        Path(args.output).write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(gate, ensure_ascii=False))
        return 0 if gate["result"] == "PASS" else 1
    except (ConfigError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"[gate-decision] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
