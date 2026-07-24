#!/usr/bin/env python3
"""Risk-aware merge decision for AgentGate P2/P3.

Consumes an Evidence Bundle v2 and optional approvals, then produces a single
decision: PASS, FAIL, ERROR, or WAITING_APPROVAL.

// risk:untested reason:"covered by RiskMergeDecisionTests" owner:@kevin reviewed:2026-07-24
"""
from __future__ import annotations

import argparse
import json
import fnmatch
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import evidence_bundle


SCHEMA_VERSION = "agentgate.io/risk-decision/v1"
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _load_yaml(path: str) -> dict[str, Any]:
    return evidence_bundle.load_yaml(path)


def _max_risk(left: str, right: str) -> str:
    return left if RISK_ORDER[left] >= RISK_ORDER[right] else right


def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def classify_risk(changed_paths: list[str], profile: dict[str, Any], declared: str = "low") -> str:
    risk = declared if declared in RISK_ORDER else "low"
    risk_paths = profile.get("risk_paths", {})
    if not isinstance(risk_paths, dict):
        risk_paths = {}

    for path in changed_paths:
        normalized = path.replace("\\", "/")
        if _matches(normalized, [str(item) for item in risk_paths.get("critical", [])]):
            risk = _max_risk(risk, "critical")
        elif _matches(normalized, [str(item) for item in risk_paths.get("high", [])]):
            risk = _max_risk(risk, "high")
        elif normalized.endswith((".md", ".txt")) or normalized.startswith("docs/"):
            risk = _max_risk(risk, "low")
        else:
            risk = _max_risk(risk, "medium")
    return risk


def valid_approvals(
    approvals: list[dict[str, Any]],
    *,
    source_sha: str,
    author: str,
) -> list[dict[str, Any]]:
    seen = set()
    valid = []
    for item in approvals:
        if not isinstance(item, dict):
            continue
        approver = str(item.get("approver") or item.get("user") or "")
        if not approver or approver == author or approver in seen:
            continue
        if str(item.get("source_sha") or "") != source_sha:
            continue
        seen.add(approver)
        valid.append(item)
    return valid


def _approval_requirement(risk: str) -> int:
    if risk == "high":
        return 1
    if risk == "critical":
        return 2
    return 0


def _checks_to_mapping(bundle: dict[str, Any]) -> dict[str, str]:
    checks = {}
    for item in bundle.get("checks", []):
        if isinstance(item, dict):
            checks[str(item.get("id"))] = str(item.get("status"))
    return checks


def _required_checks(plan: dict[str, Any] | None, bundle: dict[str, Any]) -> list[str]:
    if plan and isinstance(plan.get("checks"), list):
        return [str(item.get("id")) for item in plan["checks"] if isinstance(item, dict)]
    return sorted(_checks_to_mapping(bundle))


def build_decision(
    *,
    bundle: dict[str, Any],
    profile: dict[str, Any],
    changed_paths: list[str],
    declared_risk: str = "low",
    approvals: list[dict[str, Any]] | None = None,
    author: str = "",
    plan: dict[str, Any] | None = None,
    expected: dict[str, str] | None = None,
) -> dict[str, Any]:
    approvals = approvals or []
    expected = expected or {}
    problems = evidence_bundle.verify_bundle(bundle, expected)
    checks = _checks_to_mapping(bundle)
    required = _required_checks(plan, bundle)
    missing = [name for name in required if name not in checks]
    failed = [name for name in required if checks.get(name) == "fail"]
    errored = [name for name in required if checks.get(name) == "error"]
    non_pass = [name for name in required if checks.get(name) != "pass"]

    risk = classify_risk(changed_paths, profile, declared_risk)
    required_approvals = _approval_requirement(risk)
    valid = valid_approvals(approvals, source_sha=str(bundle.get("source_sha")), author=author)
    reasons = []

    if problems:
        reasons.extend(problems)
    if missing:
        reasons.append("required_check_missing")
    if failed:
        reasons.append("required_check_failed")
    if errored:
        reasons.append("required_check_error")
    if len(valid) < required_approvals:
        reasons.append("approval_missing")

    if problems or errored:
        status = "ERROR"
        action = "BLOCK"
    elif missing or failed or non_pass:
        status = "FAIL"
        action = "BLOCK"
    elif risk == "critical":
        status = "WAITING_APPROVAL" if len(valid) < required_approvals else "PASS"
        action = "WAIT" if len(valid) < required_approvals else "MANUAL_MERGE"
    elif len(valid) < required_approvals:
        status = "WAITING_APPROVAL"
        action = "WAIT"
    else:
        status = "PASS"
        action = "AUTO_MERGE"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "merge_action": action,
        "risk": risk,
        "source_sha": bundle.get("source_sha"),
        "target_sha": bundle.get("target_sha"),
        "merge_sha": bundle.get("merge_sha"),
        "policy_digest": bundle.get("policy_digest"),
        "profile_digest": bundle.get("profile_digest"),
        "required_checks": [
            {"name": name, "status": checks.get(name, "missing")} for name in required
        ],
        "approvals": {
            "required": required_approvals,
            "valid": len(valid),
            "approvers": [item.get("approver") or item.get("user") for item in valid],
        },
        "blocking_reasons": sorted(set(reasons)),
        "changed_paths": changed_paths,
        "decided_at": _now(),
        "decided_by": "risk-merge-decision",
    }


def append_audit(path: str, decision: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(decision, ensure_ascii=False, sort_keys=True) + "\n")


def _write(path: str | None, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path:
        Path(path).write_text(text, encoding="utf-8")
    print(text, end="")


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentGate risk-aware merge decision")
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--changed-paths", required=True)
    parser.add_argument("--declared-risk", choices=sorted(RISK_ORDER), default="low")
    parser.add_argument("--approvals", help="approval JSON list")
    parser.add_argument("--author", default="")
    parser.add_argument("--plan", help="Evidence Plan JSON")
    parser.add_argument("--source-sha", default="")
    parser.add_argument("--target-sha", default="")
    parser.add_argument("--merge-sha", default="")
    parser.add_argument("--policy-digest", default="")
    parser.add_argument("--profile-digest", default="")
    parser.add_argument("--audit-log")
    parser.add_argument("--output")
    args = parser.parse_args()

    try:
        bundle = _load_json(args.bundle)
        profile = _load_yaml(args.profile)
        changed = _load_json(args.changed_paths)
        if not isinstance(changed, list):
            raise RuntimeError("changed paths must be a JSON list")
        approvals = _load_json(args.approvals) if args.approvals else []
        if not isinstance(approvals, list):
            raise RuntimeError("approvals must be a JSON list")
        plan = _load_json(args.plan) if args.plan else None
        decision = build_decision(
            bundle=bundle,
            profile=profile,
            changed_paths=[str(item) for item in changed],
            declared_risk=args.declared_risk,
            approvals=approvals,
            author=args.author,
            plan=plan,
            expected={
                "source_sha": args.source_sha,
                "target_sha": args.target_sha,
                "merge_sha": args.merge_sha,
                "policy_digest": args.policy_digest,
                "profile_digest": args.profile_digest,
            },
        )
        if args.audit_log:
            append_audit(args.audit_log, decision)
        _write(args.output, decision)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[risk-merge-decision] ERROR: {exc}", file=sys.stderr)
        return 2
    if decision["status"] == "PASS":
        return 0
    if decision["status"] == "WAITING_APPROVAL":
        return 1
    return 2 if decision["status"] == "ERROR" else 1


if __name__ == "__main__":
    raise SystemExit(main())
