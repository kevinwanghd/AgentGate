#!/usr/bin/env python3
"""Minimal AgentGate Controller for GitLab CE 11.4 P0 readiness.

The controller runs outside source-branch CI. It verifies Bot/API access,
target branch protection and target-branch policy digest, then can create or
update a merge request through GitLab API v4.

// risk:untested reason:"covered by tests/test_regressions.py::GitLabControllerTests" owner:@kevin reviewed:2026-07-24
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import create_mr


DEFAULT_POLICY_PATH = "governance.config.yml"


def _project_path(project_id: str) -> str:
    return urllib.parse.quote(str(project_id), safe="")


def _branch_path(branch: str) -> str:
    return urllib.parse.quote(str(branch), safe="")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _api_args(args) -> tuple[str, str, str]:
    return create_mr._require_gitlab_api_args(args)


def _api(method: str, args, path: str, payload: dict | None = None, query: dict | None = None):
    base_url, _project_id, token = _api_args(args)
    return create_mr._gitlab_api_request(method, base_url, token, path, payload, query)


def _check(name: str, status: str, message: str, details: dict | None = None) -> dict:
    result = {
        "name": name,
        "status": status,
        "message": message,
    }
    if details:
        result["details"] = details
    return result


def _project(args) -> dict:
    _base_url, project_id, _token = _api_args(args)
    return _api("GET", args, f"/projects/{_project_path(project_id)}")


def _current_user(args) -> dict:
    return _api("GET", args, "/user")


def _branch(args, branch: str) -> dict:
    _base_url, project_id, _token = _api_args(args)
    return _api(
        "GET",
        args,
        f"/projects/{_project_path(project_id)}/repository/branches/{_branch_path(branch)}",
    )


def _protected_branch(args, branch: str) -> dict:
    _base_url, project_id, _token = _api_args(args)
    return _api(
        "GET",
        args,
        f"/projects/{_project_path(project_id)}/protected_branches/{_branch_path(branch)}",
    )


def _target_policy(args) -> tuple[str, str]:
    _base_url, project_id, _token = _api_args(args)
    policy_path = getattr(args, "policy_path", DEFAULT_POLICY_PATH) or DEFAULT_POLICY_PATH
    raw_path = urllib.parse.quote(policy_path, safe="")
    payload = _api(
        "GET",
        args,
        f"/projects/{_project_path(project_id)}/repository/files/{raw_path}",
        query={"ref": args.target_branch},
    )
    if not isinstance(payload, dict):
        raise RuntimeError("GitLab policy file API returned non-object payload")
    encoded = payload.get("content")
    if not isinstance(encoded, str):
        raise RuntimeError(f"GitLab policy file API response missing content: {policy_path}")
    content = base64.b64decode(encoded).decode("utf-8", errors="replace")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"sha256:{digest}", content


def _commit_sha(branch_payload: dict) -> str:
    commit = branch_payload.get("commit", {})
    if isinstance(commit, dict):
        return str(commit.get("id") or "")
    return ""


def _push_access_is_restricted(protected_payload: dict) -> tuple[bool, str]:
    if protected_payload.get("developers_can_push") is True:
        return False, "developers_can_push=true"
    push_levels = protected_payload.get("push_access_levels")
    if isinstance(push_levels, list) and push_levels:
        levels = [item.get("access_level") for item in push_levels if isinstance(item, dict)]
        if any(level not in (0, None) for level in levels):
            return False, f"push_access_levels={levels}"
        return True, f"push_access_levels={levels}"
    return True, "protected branch exists; old GitLab response has no push access details"


def build_readiness(args, require_source: bool = False) -> dict[str, Any]:
    checks = []
    project_payload: dict[str, Any] = {}
    user_payload: dict[str, Any] = {}
    target_payload: dict[str, Any] = {}
    source_payload: dict[str, Any] = {}
    policy_digest = ""

    try:
        project_payload = _project(args)
        project_name = project_payload.get("path_with_namespace") or project_payload.get("name")
        checks.append(_check("gitlab_project_access", "pass", f"project accessible: {project_name}"))
    except RuntimeError as exc:
        checks.append(_check("gitlab_project_access", "fail", str(exc)))

    try:
        user_payload = _current_user(args)
        username = user_payload.get("username") or user_payload.get("name") or "unknown"
        checks.append(_check("gitlab_bot_identity", "pass", f"token belongs to: {username}"))
    except RuntimeError as exc:
        checks.append(_check("gitlab_bot_identity", "fail", str(exc)))

    try:
        target_payload = _branch(args, args.target_branch)
        checks.append(
            _check(
                "target_branch_exists",
                "pass",
                f"target {args.target_branch} exists",
                {"target_sha": _commit_sha(target_payload)},
            )
        )
    except RuntimeError as exc:
        checks.append(_check("target_branch_exists", "fail", str(exc)))

    if require_source or getattr(args, "source_branch", None):
        source = args.source_branch or create_mr.current_branch()
        try:
            source_payload = _branch(args, source)
            checks.append(
                _check(
                    "source_branch_exists",
                    "pass",
                    f"source {source} exists",
                    {"source_sha": _commit_sha(source_payload)},
                )
            )
        except RuntimeError as exc:
            checks.append(_check("source_branch_exists", "fail", str(exc)))

    try:
        protected_payload = _protected_branch(args, args.target_branch)
        restricted, detail = _push_access_is_restricted(protected_payload)
        checks.append(
            _check(
                "target_branch_protected",
                "pass" if restricted else "fail",
                f"target branch protection verified: {detail}",
            )
        )
    except RuntimeError as exc:
        checks.append(_check("target_branch_protected", "fail", str(exc)))

    try:
        policy_digest, _content = _target_policy(args)
        checks.append(
            _check(
                "target_policy_digest",
                "pass",
                "policy loaded from target branch",
                {"policy_path": args.policy_path, "policy_digest": policy_digest},
            )
        )
    except RuntimeError as exc:
        checks.append(_check("target_policy_digest", "fail", str(exc)))

    status = "pass" if all(item["status"] == "pass" for item in checks) else "fail"
    return {
        "schema_version": "agentgate.io/gitlab-controller-readiness/v1",
        "status": status,
        "checked_at": _now(),
        "gitlab_url": args.gitlab_url or os.environ.get("AGENTGATE_GITLAB_URL"),
        "project_id": args.gitlab_project_id or os.environ.get("AGENTGATE_GITLAB_PROJECT_ID"),
        "target_branch": args.target_branch,
        "source_branch": getattr(args, "source_branch", None),
        "target_sha": _commit_sha(target_payload),
        "source_sha": _commit_sha(source_payload),
        "policy_source": "target_branch",
        "policy_path": args.policy_path,
        "policy_digest": policy_digest,
        "checks": checks,
    }


def _write_json(path: str | None, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path:
        Path(path).write_text(text, encoding="utf-8")
    print(text, end="")


def preflight(args) -> int:
    result = build_readiness(args)
    _write_json(args.output, result)
    return 0 if result["status"] == "pass" else 1


def _create_mr_args(args) -> SimpleNamespace:
    return SimpleNamespace(
        why=args.why,
        requirement_id=args.requirement_id,
        what=args.what,
        tested=args.tested,
        risks=args.risks,
        excludes=args.excludes,
        link=args.link,
        title=args.title,
        target_branch=args.target_branch,
        config=args.config,
        evidence=args.evidence,
        meta_style=args.meta_style,
    )


def submit(args) -> int:
    readiness = build_readiness(args, require_source=True)
    if readiness["status"] != "pass":
        _write_json(args.output, readiness)
        return 1

    try:
        cfg = create_mr.load_config(args.config)
        description = create_mr.build_description(_create_mr_args(args), cfg)
        title = create_mr.infer_title(_create_mr_args(args), args.target_branch)
        rc = create_mr.submit_gitlab_api(title, description, args.target_branch, args)
    except (RuntimeError, create_mr.ConfigError) as exc:
        readiness["status"] = "fail"
        readiness["checks"].append(_check("merge_request_submit", "fail", str(exc)))
        _write_json(args.output, readiness)
        return 1

    readiness["checks"].append(
        _check(
            "merge_request_submit",
            "pass" if rc == 0 else "fail",
            "merge request created or updated" if rc == 0 else "merge request submit failed",
        )
    )
    readiness["status"] = "pass" if rc == 0 else "fail"
    _write_json(args.output, readiness)
    return rc


def _add_gitlab_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--gitlab-url", help="GitLab 地址, 也可用 AGENTGATE_GITLAB_URL")
    parser.add_argument(
        "--gitlab-project-id",
        help="GitLab project id 或 URL 编码路径, 也可用 AGENTGATE_GITLAB_PROJECT_ID",
    )
    parser.add_argument(
        "--gitlab-token",
        help="GitLab Bot Personal Access Token, 也可用 AGENTGATE_GITLAB_TOKEN",
    )
    parser.add_argument("--target-branch", default="master")
    parser.add_argument("--policy-path", default=DEFAULT_POLICY_PATH)
    parser.add_argument("--output", help="写入 JSON 结果文件")


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentGate GitLab 11.4 Controller")
    sub = parser.add_subparsers(dest="command", required=True)

    preflight_parser = sub.add_parser("preflight", help="检查 GitLab 11.4 P0 前置条件")
    _add_gitlab_args(preflight_parser)
    preflight_parser.set_defaults(func=preflight)

    submit_parser = sub.add_parser("submit", help="检查 P0 前置条件后创建/更新 MR")
    _add_gitlab_args(submit_parser)
    submit_parser.add_argument("--source-branch", help="源分支, 默认当前分支")
    submit_parser.add_argument("--why", required=True, help="MR 背景")
    submit_parser.add_argument("--requirement-id")
    submit_parser.add_argument("--what")
    submit_parser.add_argument("--tested")
    submit_parser.add_argument("--risks")
    submit_parser.add_argument("--excludes")
    submit_parser.add_argument("--link", action="append")
    submit_parser.add_argument("--title")
    submit_parser.add_argument("--config")
    submit_parser.add_argument("--evidence", default=create_mr.EVIDENCE_PATH)
    submit_parser.add_argument(
        "--meta-style",
        choices=["details", "section", "comment"],
        default="details",
    )
    submit_parser.add_argument("--remove-source-branch", action="store_true")
    submit_parser.set_defaults(func=submit)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
