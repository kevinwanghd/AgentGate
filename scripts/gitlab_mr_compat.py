#!/usr/bin/env python3
"""Validate MR descriptions in modern and legacy GitLab pipelines.

Authoritative resolution order:

1. GitLab-provided ``CI_MERGE_REQUEST_DESCRIPTION`` (actual MR verified).
2. GitLab project API for legacy branch pipelines (actual MR verified).

A version-controlled branch manifest may bind a description to the branch diff,
but it can never substitute for the actual GitLab MR description. Legacy branch
pipelines fail closed when the API or an authorized read token is unavailable.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.parse
from typing import Callable

import create_mr
import validate_mr
from governance_common import ConfigError


DEFAULT_RESULT = "governance-mr-validate-result.json"
DEFAULT_MANIFEST_PATH = ".agentgate/mr-description.md"
READ_TOKEN_ENV = "AGENTGATE_GITLAB_READ_TOKEN"
TOKEN_ENV_NAMES = (READ_TOKEN_ENV, *create_mr.GITLAB_TOKEN_ENV_NAMES)


@dataclass(frozen=True)
class DescriptionResolution:
    """Resolved description plus the authority represented by its source."""

    text: str | None
    source: str
    actual_mr_verified: bool
    diff_base: str | None = None
    reason: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def evidence(self) -> dict[str, object]:
        payload: dict[str, object] = {
            **self.metadata,
            "source": self.source,
            "actual_mr_verified": self.actual_mr_verified,
        }
        if self.reason:
            payload["reason"] = self.reason
        if self.text is not None:
            payload["description_sha256"] = hashlib.sha256(
                self.text.encode("utf-8")
            ).hexdigest()
        return payload


class DescriptionSourceError(RuntimeError):
    """A configured description source exists but cannot be trusted."""

    def __init__(
        self,
        message: str,
        *,
        source: str = "unavailable",
        actual_mr_verified: bool = False,
        metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.source = source
        self.actual_mr_verified = actual_mr_verified
        self.metadata = metadata or {}

    def evidence(self) -> dict[str, object]:
        return {
            **self.metadata,
            "source": self.source,
            "actual_mr_verified": self.actual_mr_verified,
            "reason": str(self),
        }


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


def _require_api_config(args: argparse.Namespace) -> tuple[str, str, str]:
    base_url = _derive_gitlab_url(args)
    project_id = (
        args.gitlab_project_id
        or _env("AGENTGATE_GITLAB_PROJECT_ID", "CI_PROJECT_ID")
    )
    token = _env(*TOKEN_ENV_NAMES)
    missing = []
    if not base_url:
        missing.append("gitlab url")
    if not project_id:
        missing.append("project id")
    if not token:
        missing.append("GitLab token env")
    if missing:
        raise DescriptionSourceError(
            "missing GitLab read settings: " + ", ".join(missing),
            source="gitlab-api",
        )
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
        raise DescriptionSourceError(
            "GitLab merge_requests endpoint returned a non-list payload"
        )
    if not data:
        return None
    if not target_branch and len(data) > 1:
        raise DescriptionSourceError(
            f"multiple opened MRs found for source branch {source_branch}; "
            "the target branch is ambiguous",
            source="gitlab-api",
        )
    data.sort(
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )
    return data[0]


def _manifest_changed(path: str, diff_base: str | None) -> bool:
    if not diff_base:
        raise DescriptionSourceError(
            "--diff-base is required to verify the branch description manifest"
        )
    proc = subprocess.run(
        ["git", "diff", "--quiet", f"{diff_base}...HEAD", "--", path],
        check=False,
    )
    if proc.returncode == 0:
        return False
    if proc.returncode == 1:
        return True
    raise DescriptionSourceError(
        f"cannot compare description manifest against {diff_base}"
    )


def _normalize_description(value: str) -> str:
    return value.replace("\r\n", "\n").strip()


def _ensure_target_ref(target_branch: str) -> str:
    diff_base = f"origin/{target_branch}"
    verify = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", diff_base],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if verify.returncode != 0:
        fetch = subprocess.run(
            [
                "git",
                "fetch",
                "-q",
                "origin",
                f"+refs/heads/{target_branch}:refs/remotes/origin/{target_branch}",
            ],
            check=False,
        )
        if fetch.returncode != 0:
            raise DescriptionSourceError(
                f"cannot fetch actual MR target branch {target_branch}",
                source="gitlab-api",
                actual_mr_verified=True,
            )
    return diff_base


def resolve_description(
    args: argparse.Namespace,
    source_branch: str,
    api_lookup: Callable[..., dict | None] = _find_open_mr,
) -> DescriptionResolution:
    """Resolve one description without exposing source-selection complexity."""

    if "CI_MERGE_REQUEST_DESCRIPTION" in os.environ:
        ci_description = os.environ.get("CI_MERGE_REQUEST_DESCRIPTION", "")
        return DescriptionResolution(
            text=ci_description,
            source="gitlab-ci",
            actual_mr_verified=True,
        )

    try:
        base_url, project_id, token = _require_api_config(args)
        mr = api_lookup(
            base_url,
            project_id,
            token,
            source_branch,
            _env("CI_MERGE_REQUEST_TARGET_BRANCH_NAME"),
        )
    except Exception as exc:
        return DescriptionResolution(
            text=None,
            source="gitlab-api",
            actual_mr_verified=False,
            reason=f"GitLab API lookup unavailable: {exc}",
        )
    if not mr:
        return DescriptionResolution(
            text=None,
            source="gitlab-api",
            actual_mr_verified=False,
            reason=f"no opened MR for source branch {source_branch}",
        )
    actual_description = str(mr.get("description") or "")
    actual_target = str(mr.get("target_branch") or "")
    if not actual_target:
        raise DescriptionSourceError(
            "GitLab MR payload is missing target_branch",
            source="gitlab-api",
            actual_mr_verified=True,
            metadata={"iid": mr.get("iid"), "web_url": mr.get("web_url")},
        )
    actual_diff_base = _ensure_target_ref(actual_target)
    metadata: dict[str, object] = {
        "iid": mr.get("iid"),
        "web_url": mr.get("web_url"),
        "source_branch": source_branch,
        "target_branch": actual_target,
        "diff_base": actual_diff_base,
    }

    manifest = Path(args.manifest_path)
    if manifest.is_file():
        if not _manifest_changed(args.manifest_path, actual_diff_base):
            raise DescriptionSourceError(
                f"description manifest {args.manifest_path} was not changed "
                f"relative to {actual_diff_base}; run `agentgate.py mr prepare`",
                source="gitlab-api",
                actual_mr_verified=True,
                metadata=metadata,
            )
        manifest_description = create_mr.strip_binding_header(
            manifest.read_text(encoding="utf-8-sig")
        )
        if _normalize_description(manifest_description) != _normalize_description(
            actual_description
        ):
            raise DescriptionSourceError(
                "actual GitLab MR description does not match the branch manifest",
                source="gitlab-api",
                actual_mr_verified=True,
                metadata={
                    **metadata,
                    "manifest_path": str(manifest).replace("\\", "/"),
                },
            )
        metadata["manifest_path"] = str(manifest).replace("\\", "/")
        metadata["manifest_matches_actual"] = True

    return DescriptionResolution(
        text=actual_description,
        source="gitlab-api",
        actual_mr_verified=True,
        diff_base=actual_diff_base,
        metadata=metadata,
    )


def validate_description(
    text: str, config_path: str | None, diff_base: str | None
) -> list[str]:
    # 显式要求配置路径，不静默用 DEFAULT_CONFIG
    if config_path is None or not os.path.isfile(config_path):
        raise ValueError(f"config path required and must exist: {config_path}")
    cfg = validate_mr.load_config(config_path, explicit=True)
    return validate_mr.validate(text, cfg, diff_base)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a GitLab MR description with legacy branch support"
    )
    parser.add_argument("--config", help="governance.config.yml path")
    parser.add_argument("--diff-base", help="diff base for large-change detection")
    parser.add_argument(
        "--target-branch",
        default=os.environ.get("CI_DEFAULT_BRANCH") or "master",
    )
    parser.add_argument(
        "--source-branch",
        default=_env("CI_COMMIT_REF_NAME", "CI_COMMIT_BRANCH"),
    )
    parser.add_argument("--output", default=DEFAULT_RESULT)
    parser.add_argument(
        "--manifest-path",
        default=os.environ.get("AGENTGATE_MR_DESCRIPTION_FILE")
        or DEFAULT_MANIFEST_PATH,
        help="version-controlled description manifest for legacy branch pipelines",
    )
    parser.add_argument(
        "--allow-stale-manifest",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--allow-missing-description",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--allow-api-fallback",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--gitlab-url", help="GitLab URL for branch-pipeline MR lookup")
    parser.add_argument("--gitlab-project-id", help="GitLab project ID or path for MR lookup")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    source_branch = args.source_branch
    if not source_branch:
        reason = "cannot determine source branch"
        _write_result(args.output, "fail", reason=reason)
        sys.stderr.write(f"[gitlab-mr-compat] {reason}\n")
        return 1

    if source_branch == args.target_branch:
        reason = "source branch is the target branch; no MR description is expected"
        _write_result(
            args.output,
            "skip",
            reason=reason,
            source_branch=source_branch,
            actual_mr_verified=False,
        )
        sys.stderr.write(f"[gitlab-mr-compat] {reason}; status=skip\n")
        return 0

    try:
        resolution = resolve_description(args, source_branch)
        if resolution.text is None:
            status = "fail"
            _write_result(args.output, status, **resolution.evidence())
            sys.stderr.write(
                f"[gitlab-mr-compat] {resolution.reason}; status={status}\n"
            )
            return 0 if status == "skip" else 1
        problems = validate_description(
            resolution.text,
            args.config,
            resolution.diff_base or args.diff_base,
        )
    except ConfigError as exc:
        _write_result(args.output, "fail", reason=f"config error: {exc}")
        sys.stderr.write(f"[gitlab-mr-compat] config error: {exc}\n")
        return 2
    except DescriptionSourceError as exc:
        _write_result(args.output, "fail", **exc.evidence())
        sys.stderr.write(f"[gitlab-mr-compat] {exc}; status=fail\n")
        return 1
    except Exception as exc:
        _write_result(args.output, "fail", reason=str(exc))
        sys.stderr.write(f"[gitlab-mr-compat] ERROR: {exc}\n")
        return 1

    evidence = resolution.evidence()
    if problems:
        _write_result(args.output, "fail", problems=problems, **evidence)
        sys.stderr.write("[gitlab-mr-compat] FAIL\n")
        for item in problems:
            sys.stderr.write(f"  - {item}\n")
        return 1

    _write_result(args.output, "pass", **evidence)
    sys.stderr.write("[gitlab-mr-compat] PASS (actual MR)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
