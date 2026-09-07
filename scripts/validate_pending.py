#!/usr/bin/env python3
"""
validate_pending.py — Pending lessons schema validator

Validates pending lesson files in .governance/pending-lessons/ against the schema.
This is SEPARATE from validate_lessons.py which validates lessons/v1 files.

Usage:
    python scripts/validate_pending.py --root .
    python scripts/validate_pending.py --root . --check-duplicates
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("[validate-pending] missing pyyaml; install pyyaml\n")
    sys.exit(1)


# Required fields for pending lessons
REQUIRED_FIELDS = (
    "id",
    "pattern_type",
    "source_repo",
    "source_ref",
    "fingerprint",
    "detected_at",
    "failure_context",
    "evidence",
    "regression",
    "status",
)

# Valid status values
VALID_STATUSES = ("pending", "confirmed", "rejected", "promoted")

# Fields that are required to be mappings (nested dicts)
NESTED_FIELDS = ("failure_context", "evidence")

# Fields required in failure_context
FAILURE_CONTEXT_FIELDS = ("file", "line", "snippet", "diff_baseline")

# Fields required in evidence
EVIDENCE_FIELDS = ("rule_id", "scan_summary", "raw_diff_fragment")

# Fingerprint format: 16 hex characters (first 8 bytes of SHA-256)
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{16}$")

# ISO 8601 datetime pattern (simplified)
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?$")


def _fail(errors: list[str], message: str) -> None:
    errors.append(message)


def validate_pending_file(path: Path, errors: list[str], check_duplicates: bool = False) -> int:
    """
    Validate a single pending lesson file.
    Returns 1 if valid, 0 if invalid.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        _fail(errors, f"{path}: YAML parse error: {e}")
        return 0

    if not isinstance(data, dict):
        _fail(errors, f"{path}: pending file must be a mapping")
        return 0

    # Check required fields
    for field in REQUIRED_FIELDS:
        if field not in data or data[field] in (None, "", []):
            _fail(errors, f"{path}: missing required field '{field}'")

    # Validate status
    status = data.get("status", "")
    if status and status not in VALID_STATUSES:
        _fail(errors, f"{path}: status must be one of {VALID_STATUSES}, got '{status}'")

    # Validate fingerprint format
    fingerprint = data.get("fingerprint", "")
    if fingerprint and not FINGERPRINT_RE.match(fingerprint):
        _fail(errors, f"{path}: fingerprint must be 16 hex chars (got '{fingerprint}')")

    # Validate datetime format
    detected_at = data.get("detected_at", "")
    if detected_at and not DATETIME_RE.match(detected_at):
        _fail(errors, f"{path}: detected_at must be ISO 8601 (got '{detected_at}')")

    # Validate nested fields
    for nested in NESTED_FIELDS:
        if nested in data and not isinstance(data[nested], dict):
            _fail(errors, f"{path}: {nested} must be a mapping")

    # Validate failure_context sub-fields
    fc = data.get("failure_context")
    if isinstance(fc, dict):
        for subfield in FAILURE_CONTEXT_FIELDS:
            if subfield not in fc:
                _fail(errors, f"{path}: failure_context missing '{subfield}'")

    # Validate evidence sub-fields
    ev = data.get("evidence")
    if isinstance(ev, dict):
        for subfield in EVIDENCE_FIELDS:
            if subfield not in ev:
                _fail(errors, f"{path}: evidence missing '{subfield}'")

    return 1 if not errors else 0


def find_pending_files(root: Path, explicit: list[Path]) -> list[Path]:
    """Find pending lesson files."""
    if explicit:
        return explicit

    candidates = [
        root / ".governance" / "pending-lessons",
        root / "pending-lessons",  # fallback
    ]
    files: list[Path] = []
    for directory in candidates:
        if directory.is_dir():
            files.extend(directory.glob("*.yml"))
            files.extend(directory.glob("*.yaml"))
    return sorted(set(files))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate pending lesson schema")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument(
        "--check-duplicates",
        action="store_true",
        help="check for duplicate IDs across files",
    )
    parser.add_argument("paths", nargs="*", help="explicit pending YAML files")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    files = find_pending_files(root, [Path(p) for p in args.paths])

    if not files:
        print("[validate-pending] no pending files found")
        return 1

    errors: list[str] = []
    valid_count = 0
    seen_ids: dict[str, Path] = {}

    for path in files:
        file_errors = []
        if validate_pending_file(path, file_errors, args.check_duplicates):
            valid_count += 1

        if file_errors:
            errors.extend(file_errors)

        # Check for duplicate IDs
        if args.check_duplicates:
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "id" in data:
                    pid = data["id"]
                    if pid in seen_ids:
                        _fail(
                            errors,
                            f"Duplicate ID '{pid}': {seen_ids[pid]} and {path}",
                        )
                    else:
                        seen_ids[pid] = path
            except Exception:
                pass  # Already reported above

    if errors:
        print("[validate-pending] FAIL")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"[validate-pending] PASS - {valid_count}/{len(files)} files valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
