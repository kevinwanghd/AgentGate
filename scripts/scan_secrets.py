#!/usr/bin/env python3
"""Scan added diff lines for common high-confidence secret formats.

This deliberately uses only the Python standard library. Governance jobs must
not install scanners, pull images, or contact a package registry at runtime.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


_PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN [A-Z0-9 ]+ PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b")),
    ("bearer-token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{24,}")),
    (
        "credential-assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|client[_-]?secret|password|passwd|private[_-]?key|secret|token)"
            r"\s*[:=]\s*[\"'][^\"'\r\n]{12,}[\"']"
        ),
    ),
)


def added_lines(diff: str) -> list[tuple[str, int, str]]:
    """Return (path, line number, text) for added, non-header diff lines."""
    result: list[tuple[str, int, str]] = []
    path = "<unknown>"
    line_no = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ b/"):
            path = raw[6:]
            continue
        if raw.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,\d+)?", raw)
            line_no = int(match.group(1)) if match else 0
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            result.append((path, line_no, raw[1:]))
            line_no += 1
        elif not raw.startswith("-") and line_no:
            line_no += 1
    return result


def scan_diff(diff: str) -> list[tuple[str, int, str, str]]:
    findings: list[tuple[str, int, str, str]] = []
    for path, line_no, text in added_lines(diff):
        for name, pattern in _PATTERNS:
            if pattern.search(text):
                findings.append((path, line_no, name, text.strip()))
    return findings


def read_diff(args: argparse.Namespace) -> str:
    if args.diff_file:
        return Path(args.diff_file).read_text(encoding="utf-8", errors="replace")
    if not args.diff_base:
        raise ValueError("one of --diff-base or --diff-file is required")
    completed = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--unified=0", args.diff_base, "HEAD", "--"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--diff-base")
    group.add_argument("--diff-file")
    args = parser.parse_args(argv)
    try:
        findings = scan_diff(read_diff(args))
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"[secret-scan] ERROR: {exc}", file=sys.stderr)
        return 2
    if findings:
        for path, line_no, name, text in findings:
            print(f"[secret-scan] {path}:{line_no}: {name}: {text}")
        return 1
    print("[secret-scan] no high-confidence secrets in added lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
