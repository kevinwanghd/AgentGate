#!/usr/bin/env python3
"""GitLab 11.x compatible MR description validator.

GitLab CE 11.4 can show a branch pipeline as the merge request pipeline. In
that mode CI_MERGE_REQUEST_DESCRIPTION is often unavailable, so the normal
`only: merge_requests` validation never runs. This script runs safely in a
branch pipeline:

1. Detect the source branch.
2. Query GitLab API v4 for an opened MR from that branch.
3. Validate the MR description with validate_mr.py.
4. Emit a small JSON status file for GateResult.

It intentionally supports old GitLab API behavior and only depends on the
standard library plus AgentGate scripts.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse

import create_mr
import validate_mr
from governance_common import ConfigError


DEFAULT_RESULT = "governance-mr-validate-result.json"


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _derive_gitlab_url(args: argparse.Namespace) -> str | None:
    explicit = args.gitlab_url or _env("AGENTGATE_GITLAB_URL", "CI_SERVER_URL")
    if explicit:
        return explicit.rstrip("/")
    api_v4 = os.environ.get("CI_API_V4_URL")
    if api_v4:
        return api_v4.rstrip("/").removesuffix("/api/v4")
    project_url = os.environ.get("CI_PROJECT_URL")
    if project_url:
        parsed = urllib.parse.urlparse(project_url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return None


def _write_result(path: str, status: str, **extra: object) -> None:
    payload = {"status": status, **extra}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        f.write("\n")


def _require_api_args(args: argparse.Namespace) -> tuple[str, str, str]:
    base_url = _derive_gitlab_url(args)
    project_id = (
        args.gitlab_project_id
        or _env("AGENTGATE_GITLAB_PROJECT_ID", "CI_PROJECT_ID")
    )
    token = (
        args.gitlab_token
        or _env(
            "AGENTGATE_GITLAB_TOKEN",
            "GOVERNANCE_MR_VALIDATE_TOKEN",
            "GOVERNANCE_MERGE_BOT_TOKEN",
            "PRIVATE_TOKEN",
        )
    )
    missing = []
    if not base_url:
        missing.append("gitlab url")
    if not project_id:
        missing.append("project id")
    if not token:
        missing.append("token")
    if missing:
        raise RuntimeError("missing GitLab API settings: " + ", ".join(missing))
    return str(base_url), str(project_id), str(token)


def _find_open_mr(
    base_url: str,
    project_id: str,
    token: str,
    source_branch: str,
    target_branch: str | None,
) -> dict | None:
    query: dict[str, object] = {
        "state": "opened",
        "source_branch": source_branch,
        "per_page": 20,
    }
    if target_branch:
        query["target_branch"] = target_branch
    project_path = urllib.parse.quote(str(project_id), safe="")
    data = create_mr._gitlab_api_request(
        "GET",
        base_url,
        token,
        f"/projects/{project_path}/merge_requests",
        query=query,
    )
    if not isinstance(data, list):
        raise RuntimeError("GitLab merge_requests API returned non-list payload")
    if not data:
        return None
    # Prefer the most recently updated MR when old GitLab returns several.
    data.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return data[0]


def _description_from_ci() -> tuple[str | None, dict[str, object]]:
    desc = os.environ.get("CI_MERGE_REQUEST_DESCRIPTION")
    if desc:
        return desc, {"source": "ci-env"}
    return None, {}


def validate_description(text: str, config_path: str | None, diff_base: str | None) -> list[str]:
    cfg = validate_mr.load_config(config_path)
    return validate_mr.validate(text, cfg, diff_base)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate open GitLab MR description from branch pipelines"
    )
    parser.add_argument("--config", help="governance.config.yml path")
    parser.add_argument("--diff-base", help="diff base for large-change detection")
    parser.add_argument("--target-branch", default=os.environ.get("CI_DEFAULT_BRANCH") or "master")
    parser.add_argument("--source-branch", default=_env("CI_COMMIT_REF_NAME", "CI_COMMIT_BRANCH"))
    parser.add_argument("--output", default=DEFAULT_RESULT)
    parser.add_argument("--gitlab-url", help="GitLab URL")
    parser.add_argument("--gitlab-project-id", help="GitLab project id or URL-encoded path")
    parser.add_argument("--gitlab-token", help="GitLab private token")
    parser.add_argument(
        "--fail-if-no-mr",
        action="store_true",
        help="Fail instead of skip when no open MR exists for the branch",
    )
    parser.add_argument(
        "--fail-if-no-token",
        action="store_true",
        help="Fail instead of skip when API token is missing",
    )
    args = parser.parse_args()

    source_branch = args.source_branch
    if not source_branch:
        msg = "cannot determine source branch"
        _write_result(args.output, "fail", reason=msg)
        sys.stderr.write(f"[gitlab-mr-compat] {msg}\n")
        return 1

    try:
        description, meta = _description_from_ci()
        if not description:
            try:
                base_url, project_id, token = _require_api_args(args)
            except RuntimeError as exc:
                status = "fail" if args.fail_if_no_token else "skip"
                _write_result(args.output, status, reason=str(exc), source_branch=source_branch)
                sys.stderr.write(f"[gitlab-mr-compat] {exc}; status={status}\n")
                return 1 if status == "fail" else 0

            mr = _find_open_mr(base_url, project_id, token, source_branch, args.target_branch)
            if not mr:
                status = "fail" if args.fail_if_no_mr else "skip"
                reason = f"no opened MR for source branch {source_branch}"
                _write_result(args.output, status, reason=reason, source_branch=source_branch)
                sys.stderr.write(f"[gitlab-mr-compat] {reason}; status={status}\n")
                return 1 if status == "fail" else 0
            description = str(mr.get("description") or "")
            meta = {
                "source": "gitlab-api",
                "iid": mr.get("iid"),
                "web_url": mr.get("web_url"),
                "source_branch": source_branch,
                "target_branch": mr.get("target_branch"),
            }

        problems = validate_description(description, args.config, args.diff_base)
    except ConfigError as exc:
        _write_result(args.output, "fail", reason=f"config error: {exc}")
        sys.stderr.write(f"[gitlab-mr-compat] config error: {exc}\n")
        return 2
    except Exception as exc:
        _write_result(args.output, "fail", reason=str(exc), source_branch=source_branch)
        sys.stderr.write(f"[gitlab-mr-compat] ERROR: {exc}\n")
        return 1

    if problems:
        _write_result(args.output, "fail", problems=problems, **meta)
        sys.stderr.write("[gitlab-mr-compat] FAIL\n")
        for item in problems:
            sys.stderr.write(f"  - {item}\n")
        return 1

    _write_result(args.output, "pass", **meta)
    sys.stderr.write("[gitlab-mr-compat] PASS\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
