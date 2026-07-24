#!/usr/bin/env python3
"""Create and verify AgentGate Evidence Bundle v2.

This script is intentionally platform-neutral. A GitHub workflow, GitLab 11.4
Controller, or trusted runner can all call it after creating a synthetic merge
workspace.

// risk:untested reason:"covered by tests/test_regressions.py::EvidenceBundleTests" owner:@kevin reviewed:2026-07-24
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None


SCHEMA_VERSION = "agentgate.io/evidence/v2"
PLAN_SCHEMA_VERSION = "agentgate.io/evidence-plan/v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def file_digest(path: str) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def git_output(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def rev_parse(ref: str) -> str:
    return git_output(["rev-parse", ref])


def changed_paths(diff_base: str, head: str) -> list[str]:
    output = git_output(["diff", "--name-only", f"{diff_base}...{head}", "--"])
    return [line.replace("\\", "/") for line in output.splitlines() if line.strip()]


def synthetic_merge_sha(target_ref: str, source_ref: str) -> str:
    """Create a deterministic local merge commit object without updating refs.

    The caller should run this inside a trusted clone. Conflicts or missing refs
    surface as Git errors and must fail closed in the caller.
    """
    tree = git_output(["merge-tree", "--write-tree", target_ref, source_ref])
    message = f"AgentGate synthetic merge\n\nTarget: {target_ref}\nSource: {source_ref}\n"
    proc = subprocess.run(
        ["git", "commit-tree", tree, "-p", target_ref, "-p", source_ref],
        input=message,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout.strip()


def load_yaml(path: str) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required to read profiles")
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"profile must be a mapping: {path}")
    return data


def _checks_for_risk(profile: dict[str, Any], risk: str) -> list[dict[str, Any]]:
    checks = profile.get("checks", {})
    plans = profile.get("plans", {})
    if not isinstance(checks, dict) or not isinstance(plans, dict):
        raise RuntimeError("profile requires checks and plans mappings")
    plan = plans.get(risk)
    if plan is None:
        raise RuntimeError(f"profile missing plan for risk: {risk}")
    if not isinstance(plan, list):
        raise RuntimeError(f"profile plan must be a list: {risk}")

    selected = []
    for check_id in plan:
        check = checks.get(str(check_id))
        if not isinstance(check, dict):
            raise RuntimeError(f"profile references unknown check: {check_id}")
        merged = {"id": str(check_id), **check}
        selected.append(merged)
    return selected


def build_plan(args) -> dict[str, Any]:
    profile = load_yaml(args.profile)
    source_sha = args.source_sha or rev_parse(args.source_ref)
    target_sha = args.target_sha or rev_parse(args.target_ref)
    merge_sha = args.merge_sha
    if not merge_sha and args.create_synthetic_merge:
        merge_sha = synthetic_merge_sha(target_sha, source_sha)
    if not merge_sha:
        merge_sha = source_sha

    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "created_at": _now(),
        "repository": args.repository,
        "source_sha": source_sha,
        "target_sha": target_sha,
        "merge_sha": merge_sha,
        "policy_digest": args.policy_digest or file_digest(args.policy),
        "profile_digest": file_digest(args.profile),
        "risk": args.risk,
        "changed_paths": changed_paths(args.target_ref, args.source_ref)
        if args.include_changed_paths else [],
        "checks": _checks_for_risk(profile, args.risk),
    }


def _load_check_results(path: str) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        checks = data.get("checks", [])
        if isinstance(checks, dict):
            return [
                {"id": str(name), "status": str(status)}
                for name, status in sorted(checks.items())
            ]
        if isinstance(checks, list):
            return checks
    if isinstance(data, list):
        return data
    raise RuntimeError("check results must be a list or an object with checks")


def _normalize_check(item: dict[str, Any]) -> dict[str, Any]:
    check_id = str(item.get("id") or item.get("name") or "")
    if not check_id:
        raise RuntimeError(f"check missing id/name: {item}")
    status = str(item.get("status") or "").lower()
    if status not in {"pass", "fail", "error", "skipped"}:
        raise RuntimeError(f"check has invalid status: {check_id}={status}")
    normalized = {
        "id": check_id,
        "type": str(item.get("type") or check_id),
        "status": status,
    }
    for key in ("command", "command_id", "exit_code", "duration_seconds", "report"):
        if key in item:
            normalized[key] = item[key]
    return normalized


def build_bundle(args) -> dict[str, Any]:
    checks = [_normalize_check(item) for item in _load_check_results(args.checks)]
    return {
        "schema_version": SCHEMA_VERSION,
        "execution_id": args.execution_id,
        "repository": args.repository,
        "source_sha": args.source_sha,
        "target_sha": args.target_sha,
        "merge_sha": args.merge_sha,
        "policy_digest": args.policy_digest,
        "profile_digest": args.profile_digest,
        "runner_image_digest": args.runner_image_digest,
        "started_at": args.started_at,
        "finished_at": args.finished_at or _now(),
        "checks": checks,
    }


def verify_bundle(bundle: dict[str, Any], expected: dict[str, str]) -> list[str]:
    problems = []
    if bundle.get("schema_version") != SCHEMA_VERSION:
        problems.append("schema_version_mismatch")
    for key in ("source_sha", "target_sha", "merge_sha", "policy_digest", "profile_digest"):
        if not bundle.get(key):
            problems.append(f"{key}_missing")
        expected_value = expected.get(key)
        if expected_value and bundle.get(key) != expected_value:
            problems.append(f"{key}_mismatch")
    checks = bundle.get("checks")
    if not isinstance(checks, list) or not checks:
        problems.append("checks_missing")
    else:
        for item in checks:
            if not isinstance(item, dict) or item.get("status") not in {"pass", "fail", "error", "skipped"}:
                problems.append("check_invalid")
                break
    return sorted(set(problems))


def _write(path: str | None, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path:
        Path(path).write_text(text, encoding="utf-8")
    print(text, end="")


def cmd_plan(args) -> int:
    try:
        _write(args.output, build_plan(args))
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[evidence-bundle] ERROR: {exc}", file=sys.stderr)
        return 2


def cmd_bundle(args) -> int:
    try:
        _write(args.output, build_bundle(args))
        return 0
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[evidence-bundle] ERROR: {exc}", file=sys.stderr)
        return 2


def cmd_verify(args) -> int:
    try:
        bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8-sig"))
        problems = verify_bundle(
            bundle,
            {
                "source_sha": args.source_sha,
                "target_sha": args.target_sha,
                "merge_sha": args.merge_sha,
                "policy_digest": args.policy_digest,
                "profile_digest": args.profile_digest,
            },
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[evidence-bundle] ERROR: {exc}", file=sys.stderr)
        return 2
    if problems:
        print(json.dumps({"result": "FAIL", "problems": problems}, ensure_ascii=False))
        return 1
    print(json.dumps({"result": "PASS", "problems": []}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentGate Evidence Bundle v2")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="build an evidence plan from a profile")
    plan.add_argument("--repository", required=True)
    plan.add_argument("--profile", required=True)
    plan.add_argument("--policy", required=True)
    plan.add_argument("--risk", choices=["low", "medium", "high", "critical"], default="medium")
    plan.add_argument("--source-ref", default="HEAD")
    plan.add_argument("--target-ref", default="origin/main")
    plan.add_argument("--source-sha")
    plan.add_argument("--target-sha")
    plan.add_argument("--merge-sha")
    plan.add_argument("--policy-digest")
    plan.add_argument("--create-synthetic-merge", action="store_true")
    plan.add_argument("--include-changed-paths", action="store_true")
    plan.add_argument("--output")
    plan.set_defaults(func=cmd_plan)

    bundle = sub.add_parser("bundle", help="build an evidence bundle from check results")
    bundle.add_argument("--execution-id", required=True)
    bundle.add_argument("--repository", required=True)
    bundle.add_argument("--source-sha", required=True)
    bundle.add_argument("--target-sha", required=True)
    bundle.add_argument("--merge-sha", required=True)
    bundle.add_argument("--policy-digest", required=True)
    bundle.add_argument("--profile-digest", required=True)
    bundle.add_argument("--runner-image-digest", required=True)
    bundle.add_argument("--started-at", default="")
    bundle.add_argument("--finished-at", default="")
    bundle.add_argument("--checks", required=True)
    bundle.add_argument("--output")
    bundle.set_defaults(func=cmd_bundle)

    verify = sub.add_parser("verify", help="verify evidence bundle bindings")
    verify.add_argument("--bundle", required=True)
    verify.add_argument("--source-sha", default="")
    verify.add_argument("--target-sha", default="")
    verify.add_argument("--merge-sha", default="")
    verify.add_argument("--policy-digest", default="")
    verify.add_argument("--profile-digest", default="")
    verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
