#!/usr/bin/env python3
"""Validate MR descriptions in modern and legacy GitLab pipelines.

Resolution order:

1. GitLab-provided ``CI_MERGE_REQUEST_DESCRIPTION`` (actual MR verified).
2. A version-controlled branch manifest (policy validated, actual MR not verified).
3. GitLab project API, only when explicitly enabled with a dedicated read token.

The default path is safe for GitLab 11.x branch pipelines: no project API call
and no personal or merge credential is required.
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


@dataclass(frozen=True)
class DescriptionResolution:
    """Resolved description plus the authority represented by its source."""

    text: str | None
    source: str
    actual_mr_verified: bool
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
    token = _env(READ_TOKEN_ENV)
    missing = []
    if not base_url:
        missing.append("gitlab url")
    if not project_id:
        missing.append("project id")
    if not token:
        missing.append(f"dedicated read token ({READ_TOKEN_ENV})")
    if missing:
        raise DescriptionSourceError(
            "missing GitLab read settings: " + ", ".join(missing)
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

    manifest = Path(args.manifest_path)
    if manifest.is_file():
        if not args.allow_stale_manifest and not _manifest_changed(
            args.manifest_path, args.diff_base
        ):
            raise DescriptionSourceError(
                f"description manifest {args.manifest_path} was not changed "
                f"relative to {args.diff_base}; run `agentgate.py mr prepare`"
            )
        return DescriptionResolution(
            text=manifest.read_text(encoding="utf-8-sig"),
            source="repository-manifest",
            actual_mr_verified=False,
            metadata={"manifest_path": str(manifest).replace("\\", "/")},
        )

    if not args.allow_api_fallback:
        return DescriptionResolution(
            text=None,
            source="unavailable",
            actual_mr_verified=False,
            reason=(
                f"description manifest {args.manifest_path} is missing; "
                "GitLab API fallback is disabled"
            ),
        )

    try:
        base_url, project_id, token = _require_api_config(args)
        mr = api_lookup(
            base_url,
            project_id,
            token,
            source_branch,
            args.target_branch,
        )
    except Exception as exc:
        return DescriptionResolution(
            text=None,
            source="gitlab-api",
            actual_mr_verified=False,
            reason=f"GitLab API fallback unavailable: {exc}",
        )
    if not mr:
        return DescriptionResolution(
            text=None,
            source="gitlab-api",
            actual_mr_verified=False,
            reason=f"no opened MR for source branch {source_branch}",
        )
    return DescriptionResolution(
        text=str(mr.get("description") or ""),
        source="gitlab-api",
        actual_mr_verified=True,
        metadata={
            "iid": mr.get("iid"),
            "web_url": mr.get("web_url"),
            "source_branch": source_branch,
            "target_branch": mr.get("target_branch"),
        },
    )


def validate_description(
    text: str, config_path: str | None, diff_base: str | None
) -> list[str]:
    cfg = validate_mr.load_config(config_path)
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
        help="do not require the current branch to update the manifest",
    )
    parser.add_argument(
        "--allow-missing-description",
        action="store_true",
        help="emit skip instead of fail when no description can be resolved",
    )
    parser.add_argument(
        "--allow-api-fallback",
        action="store_true",
        help=f"allow GitLab project lookup using only {READ_TOKEN_ENV}",
    )
    parser.add_argument("--gitlab-url", help="GitLab URL for explicit API fallback")
    parser.add_argument("--gitlab-project-id", help="GitLab project ID or path")
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
            status = "skip" if args.allow_missing_description else "fail"
            _write_result(args.output, status, **resolution.evidence())
            sys.stderr.write(
                f"[gitlab-mr-compat] {resolution.reason}; status={status}\n"
            )
            return 0 if status == "skip" else 1
        problems = validate_description(
            resolution.text,
            args.config,
            args.diff_base,
        )
    except ConfigError as exc:
        _write_result(args.output, "fail", reason=f"config error: {exc}")
        sys.stderr.write(f"[gitlab-mr-compat] config error: {exc}\n")
        return 2
    except DescriptionSourceError as exc:
        _write_result(
            args.output,
            "fail",
            source="repository-manifest",
            actual_mr_verified=False,
            reason=str(exc),
        )
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
    verification = "actual MR" if resolution.actual_mr_verified else "branch manifest"
    sys.stderr.write(f"[gitlab-mr-compat] PASS ({verification})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
