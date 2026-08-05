#!/usr/bin/env python3
"""Validate hard lessons against executable governance invariants."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Callable

try:
    import yaml
except ImportError:
    sys.stderr.write("[validate-lessons] missing pyyaml; install pyyaml\n")
    sys.exit(1)


REQUIRED_FIELDS = ("id", "enforcement", "applies_to", "trigger", "risk", "fix", "regression")


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def _read_first(root: Path, *rels: str) -> str:
    for rel in rels:
        path = root / rel
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(", ".join(rels))


def _fail(errors: list[str], message: str) -> None:
    errors.append(message)


def check_gitlab_job_timeout_unsupported(root: Path, errors: list[str]) -> None:
    template = _read_first(root, "ci/governance-ci.yml", "governance/ci-snippet.yml")
    if re.search(r"(?m)^\s+timeout:", template):
        _fail(errors, "gitlab_legacy.job_timeout_unsupported: ci/governance-ci.yml contains job-level timeout")


def check_gitlab_modern_schema_unsupported(root: Path, errors: list[str]) -> None:
    template = _read_first(root, "ci/governance-ci.yml", "governance/ci-snippet.yml")
    for needle in ("rules:", "needs:", "dotenv:"):
        if needle in template:
            _fail(errors, f"gitlab_legacy.modern_schema_unsupported: ci/governance-ci.yml contains {needle}")


def check_gitlab_optional_language_image_pull(root: Path, errors: list[str]) -> None:
    template = _read_first(root, "ci/governance-ci.yml", "governance/ci-snippet.yml")
    forbidden = ("python:3.11", "python:3.11-slim", "apt-get", "pip install -q pyyaml")
    for needle in forbidden:
        if needle in template:
            _fail(errors, f"gitlab_legacy.optional_language_image_pull: ci/governance-ci.yml contains {needle}")


def check_gitlab_governance_core_required_checks(root: Path, errors: list[str]) -> None:
    installer = root / "install.sh"
    policy = _read_first(root, "install.sh", "governance.config.yml")
    default_block = (
        "required_checks:\n"
        "    - risk-scan\n"
        "    - secret-scan\n"
        "    - mr-validate\n"
        "    - test-check"
    )
    if default_block not in policy:
        _fail(errors, "gitlab_legacy.governance_core_required_checks: default checks changed")
    if installer.exists():
        forbidden_block = default_block + "\n    - go-test"
        if forbidden_block in policy:
            _fail(
                errors,
                "gitlab_legacy.governance_core_required_checks: "
                "installer requires go-test by default",
            )


def check_gitlab_secret_history_hard_block(root: Path, errors: list[str]) -> None:
    template = _read_first(root, "ci/governance-ci.yml", "governance/ci-snippet.yml")
    if not all(needle in template for needle in ("gitleaks detect", "--log-opts=", "..HEAD")):
        _fail(
            errors,
            "gitlab_legacy.secret_history_hard_block: "
            "gitleaks must scan the target-branch-to-HEAD commit range",
        )


def check_gitlab_actual_mr_description_authoritative(root: Path, errors: list[str]) -> None:
    compat = _read_first(
        root,
        "scripts/gitlab_mr_compat.py",
        "governance/scripts/gitlab_mr_compat.py",
    )
    tests_path = root / "tests" / "test_regressions.py"
    tests = tests_path.read_text(encoding="utf-8") if tests_path.exists() else ""
    required = {
        'source="gitlab-api"': compat,
        "actual_mr_verified=True": compat,
        "actual GitLab MR description does not match the branch manifest": compat,
    }
    if tests:
        required[
            "test_branch_pipeline_rejects_empty_actual_mr_even_with_valid_manifest"
        ] = tests
        required[
            "test_deprecated_allow_missing_description_cannot_bypass_gate"
        ] = tests
    for needle, haystack in required.items():
        if needle not in haystack:
            _fail(
                errors,
                "gitlab_legacy.actual_mr_description_authoritative: "
                f"missing {needle}",
            )
    forbidden = (
        'source="repository-manifest"',
        'verification = "actual MR" if resolution.actual_mr_verified else',
    )
    for needle in forbidden:
        if needle in compat:
            _fail(
                errors,
                "gitlab_legacy.actual_mr_description_authoritative: "
                f"unsafe success path contains {needle}",
            )


def check_agent_instructions_preserve_repository_agents_md(root: Path, errors: list[str]) -> None:
    gate_decision = _read_first(root, "scripts/gate_decision.py", "governance/scripts/gate_decision.py")
    policy = _read_first(root, "install.sh", "governance.config.yml")
    protected_paths = (
        "AGENTS.md",
        "CLAUDE.md",
        ".hermes.md",
        ".github/copilot-instructions.md",
        ".cursor/rules/**",
    )
    required = {f'"{path}"': gate_decision for path in protected_paths}
    required.update({f"    - {path}": policy for path in protected_paths[:4]})
    required["    - .cursor/rules/**"] = policy
    for needle, haystack in required.items():
        if needle not in haystack:
            _fail(errors, f"agent_instructions.preserve_repository_agents_md: missing {needle}")
    repository_lessons = root / "governance" / "lessons" / "repository.yml"
    if not (root / "install.sh").exists() and not repository_lessons.exists():
        _fail(errors, "agent_instructions.preserve_repository_agents_md: missing governance/lessons/repository.yml")
    installer_path = root / "install.sh"
    if installer_path.exists():
        installer = installer_path.read_text(encoding="utf-8")
        tests = _read(root, "tests/test_regressions.py")
        source_required = {
            'append_governance_section "CLAUDE.md"': installer,
            'append_governance_section ".github/copilot-instructions.md"': installer,
            'append_governance_section ".cursor/rules/governance.mdc"': installer,
            'append_governance_section ".hermes.md"': installer,
            'append_governance_section "AGENTS.md"': installer,
            "leaving the file unchanged": installer,
            "create_repository_lessons_file": installer,
            "governance/lessons/repository.yml": installer,
            "test_installer_preserves_existing_agents_md": tests,
            "test_installer_never_rewrites_an_existing_agents_md": tests,
        }
        for needle, haystack in source_required.items():
            if needle not in haystack:
                _fail(errors, f"agent_instructions.preserve_repository_agents_md: missing {needle}")
        if "upsert_governance_section" in installer:
            _fail(errors, "agent_instructions.preserve_repository_agents_md: installer must be append-only")
        for rel in (
            "agent-instructions/AGENTS.md",
            "agent-instructions/CLAUDE.md",
            "agent-instructions/hermes-instructions.md",
            "agent-instructions/copilot-instructions.md",
            "agent-instructions/cursor-rules.mdc",
        ):
            template = _read(root, rel)
            if "governance/lessons/*.yml" not in template:
                _fail(errors, f"agent_instructions.preserve_repository_agents_md: {rel} does not require reading lessons")
            if "governance/lessons/repository.yml" not in template:
                _fail(errors, f"agent_instructions.preserve_repository_agents_md: {rel} does not mention repository lessons")


def check_agentgate_sensitive_pr_requires_risk_rollback(root: Path, errors: list[str]) -> None:
    validate_mr = _read_first(root, "scripts/validate_mr.py", "governance/scripts/validate_mr.py")
    policy = _read_first(root, "governance.config.yml", "governance/governance.config.yml")
    required = {
        "detect_large_change": validate_mr,
        "风险与回滚": validate_mr,
        "大变更需填 ## 风险与回滚": validate_mr,
    }
    for needle, haystack in required.items():
        if needle not in haystack:
            _fail(errors, f"agentgate_operations.sensitive_pr_requires_risk_rollback: missing {needle}")
    if ".github/" not in policy and ".github/workflows" not in policy:
        _fail(
            errors,
            "agentgate_operations.sensitive_pr_requires_risk_rollback: workflow paths are not sensitive",
        )


LESSON_CHECKS: dict[str, Callable[[Path, list[str]], None]] = {
    "gitlab_legacy.job_timeout_unsupported": check_gitlab_job_timeout_unsupported,
    "gitlab_legacy.modern_schema_unsupported": check_gitlab_modern_schema_unsupported,
    "gitlab_legacy.optional_language_image_pull": check_gitlab_optional_language_image_pull,
    "gitlab_legacy.governance_core_required_checks": check_gitlab_governance_core_required_checks,
    "gitlab_legacy.secret_history_hard_block": check_gitlab_secret_history_hard_block,
    "gitlab_legacy.actual_mr_description_authoritative": check_gitlab_actual_mr_description_authoritative,
    "agent_instructions.preserve_repository_agents_md": check_agent_instructions_preserve_repository_agents_md,
    "agentgate_operations.sensitive_pr_requires_risk_rollback": check_agentgate_sensitive_pr_requires_risk_rollback,
}


def _lesson_files(root: Path, explicit: list[str]) -> list[Path]:
    if explicit:
        return [Path(item) for item in explicit]
    candidates = [root / "lessons", root / "governance" / "lessons"]
    files: list[Path] = []
    for directory in candidates:
        if directory.is_dir():
            files.extend(directory.glob("*.yml"))
            files.extend(directory.glob("*.yaml"))
    return sorted(set(files))


def validate_file(path: Path, root: Path, errors: list[str]) -> int:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        _fail(errors, f"{path}: lesson file must be a mapping")
        return 0
    if data.get("version") != "agentgate.io/lessons/v1":
        _fail(errors, f"{path}: version must be agentgate.io/lessons/v1")
    lessons = data.get("lessons")
    if not isinstance(lessons, list):
        _fail(errors, f"{path}: lessons must be a list")
        return 0
    if not lessons:
        if data.get("scope") == "repository":
            return 0
        _fail(errors, f"{path}: lessons must be a non-empty list")
        return 0

    count = 0
    for index, lesson in enumerate(lessons):
        count += 1
        if not isinstance(lesson, dict):
            _fail(errors, f"{path}: lesson #{index + 1} must be a mapping")
            continue
        lesson_id = str(lesson.get("id", ""))
        for field in REQUIRED_FIELDS:
            if field not in lesson or lesson.get(field) in (None, "", []):
                _fail(errors, f"{path}: {lesson_id or f'lesson #{index + 1}'} missing {field}")
        enforcement = lesson.get("enforcement")
        if enforcement not in ("hard", "soft"):
            _fail(errors, f"{path}: {lesson_id} enforcement must be hard or soft")
        if enforcement == "hard":
            check = LESSON_CHECKS.get(lesson_id)
            if check is None:
                _fail(errors, f"{path}: hard lesson {lesson_id} has no executable check")
            else:
                check(root, errors)
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("paths", nargs="*", help="explicit lesson YAML files")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    files = _lesson_files(root, args.paths)
    if not files:
        print("[validate-lessons] no lesson files found")
        return 1

    errors: list[str] = []
    count = 0
    for path in files:
        count += validate_file(path, root, errors)

    if errors:
        print("[validate-lessons] FAIL")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"[validate-lessons] PASS - {count} lessons have executable guardrails")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
