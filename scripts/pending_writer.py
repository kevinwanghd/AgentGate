#!/usr/bin/env python3
"""
pending_writer.py — Write scan findings to pending lessons

This module handles writing blocking findings from scan_risks.py to
.governance/pending-lessons/ as pending candidate governance rules.

Key design decisions:
- No on_block_finding_resolved hook (scan_risks.py is a one-time scanner)
- Idempotent writes: duplicate fingerprints merge/upsert
- Complete evidence: file, line, rule_id, scan_baseline, summary, raw_diff
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import uuid
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None

# Import fingerprint module
sys.path.insert(0, str(Path(__file__).parent))
import fingerprint as fp_module


PENDING_DIR = ".governance" / "pending-lessons"


def _now_iso() -> str:
    """Return current UTC time in ISO 8601 format."""
    return dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_repo_name() -> str:
    """Get the repository name from git."""
    import subprocess

    try:
        # Get remote origin URL
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        url = result.stdout.strip()
        # Extract repo name from URL
        if "/" in url:
            name = url.rstrip("/").split("/")[-1]
            if name.endswith(".git"):
                name = name[:-4]
            return name
    except Exception:
        pass

    # Fallback: use current directory name
    return Path.cwd().name


def _get_current_branch() -> str:
    """Get the current git branch/ref."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _get_diff_baseline() -> str:
    """Get the diff baseline (usually origin/main or main)."""
    import subprocess

    # Try common defaults
    for branch in ["origin/main", "origin/master", "main", "master"]:
        try:
            subprocess.run(
                ["git", "rev-parse", "--verify", branch],
                check=True,
                capture_output=True,
            )
            return branch
        except subprocess.CalledProcessError:
            continue
    return "HEAD"


def create_pending_lesson(
    violation: dict[str, Any],
    diff_text: str,
    diff_baseline: str | None = None,
    pending_dir: Path | str = PENDING_DIR,
) -> dict[str, Any]:
    """
    Create a pending lesson from a scan violation.

    Args:
        violation: The violation dict from scan_risks.scan()
        diff_text: The full diff text for context
        diff_baseline: The git ref used for scanning (e.g., "origin/main")
        pending_dir: Directory to store pending files

    Returns:
        The created pending lesson data
    """
    if yaml is None:
        raise RuntimeError("PyYAML required for pending lesson creation")

    pending_dir = Path(pending_dir)
    pending_dir.mkdir(parents=True, exist_ok=True)

    # Extract violation details
    file_path = violation.get("file", "")
    line_no = violation.get("line", 0)
    risk_type = violation.get("type", "").split("/")[0]  # Take first if multiple
    desc = violation.get("desc", "")
    snippet = _extract_snippet(diff_text, file_path, line_no)

    # Compute fingerprint
    fp = fp_module.compute_fingerprint(risk_type, snippet)

    # Check for existing pending with same fingerprint
    existing = _find_existing_pending(pending_dir, fp, risk_type)

    if existing:
        # Merge: update occurrence count and timestamps
        return _merge_pending(existing, violation, diff_baseline or _get_diff_baseline())

    # Create new pending lesson
    pending_id = str(uuid.uuid4())
    pending_data = {
        "id": pending_id,
        "pattern_type": risk_type,
        "source_repo": _get_repo_name(),
        "source_ref": _get_current_branch(),
        "fingerprint": fp,
        "detected_at": _now_iso(),
        "failure_context": {
            "file": file_path,
            "line": line_no,
            "snippet": snippet[:200],  # Max 200 chars
            "diff_baseline": diff_baseline or _get_diff_baseline(),
        },
        "evidence": {
            "rule_id": risk_type,
            "scan_summary": desc,
            "raw_diff_fragment": _extract_diff_context(diff_text, file_path, line_no)[:500],
        },
        "regression": f"{desc} — 未加注解的代码模式可能在未来被复制",
        "status": "pending",
    }

    # Write to file
    filename = fp_module.fingerprint_file_name(risk_type, snippet)
    file_path_out = pending_dir / filename

    # Add occurrence tracking for this first detection
    pending_data["_occurrence_count"] = 1
    pending_data["_first_detection"] = pending_data["detected_at"]
    pending_data["_repos_seen"] = [pending_data["source_repo"]]

    # Write YAML (without internal fields)
    _write_pending_yaml(file_path_out, pending_data)

    return pending_data


def _extract_snippet(diff_text: str, file_path: str, line_no: int) -> str:
    """Extract the code snippet from diff at the given file and line."""
    import re

    # Find the file in diff
    pattern = rf'^\+\+\+ [ab]/{re.escape(file_path)}\n'
    match = re.search(pattern, diff_text, re.MULTILINE)

    if not match:
        return f"{file_path}:{line_no}"

    start = match.end()

    # Find the hunk header for this line
    hunk_pattern = r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@'
    hunk_matches = list(re.finditer(hunk_pattern, diff_text[start:], re.MULTILINE))

    for i, hm in enumerate(hunk_matches):
        hunk_start = start + hm.start()
        hunk_end = start + hunk_matches[i + 1].start() if i + 1 < len(hunk_matches) else len(diff_text)

        hunk_line = int(hm.group(1))
        hunk_lines = int(hm.group(2) or 1)

        if hunk_line <= line_no <= hunk_line + hunk_lines:
            # This hunk contains our line
            hunk_text = diff_text[hunk_start:hunk_end]
            # Find the actual line
            current_line = hunk_line
            for line in hunk_text.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    if current_line == line_no:
                        return line[1:].strip()
                    current_line += 1
                elif line.startswith("-"):
                    pass
                elif line.startswith("@@") or line.startswith(" "):
                    current_line += 1

    return f"{file_path}:{line_no}"


def _extract_diff_context(diff_text: str, file_path: str, line_no: int) -> str:
    """Extract diff context around the violation for evidence."""
    import re

    # Find the file in diff
    pattern = rf'^\+\+\+ [ab]/{re.escape(file_path)}\n'
    match = re.search(pattern, diff_text, re.MULTILINE)

    if not match:
        return f"{file_path}:{line_no}"

    start = match.end()

    # Find hunk containing this line
    hunk_pattern = r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@'
    hunk_matches = list(re.finditer(hunk_pattern, diff_text[start:], re.MULTILINE))

    for i, hm in enumerate(hunk_matches):
        hunk_start = start + hm.start()
        hunk_end = start + hunk_matches[i + 1].start() if i + 1 < len(hunk_matches) else len(diff_text)

        hunk_line = int(hm.group(1))
        hunk_lines = int(hm.group(2) or 1)

        if hunk_line <= line_no <= hunk_line + hunk_lines:
            hunk_text = diff_text[hunk_start:hunk_end]
            # Return first 500 chars of hunk
            return hunk_text[:500]

    return f"{file_path}:{line_no}"


def _find_existing_pending(pending_dir: Path, fingerprint: str, pattern_type: str) -> Path | None:
    """Find existing pending file with same fingerprint."""
    if not pending_dir.exists():
        return None

    # Files are named: <pattern_type>-<fingerprint>.yml
    for file in pending_dir.glob("*.yml"):
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8")) if yaml else {}
            if data and data.get("fingerprint") == fingerprint:
                return file
        except Exception:
            continue

    return None


def _merge_pending(existing_path: Path, new_violation: dict, diff_baseline: str) -> dict:
    """Merge new violation into existing pending lesson."""
    if yaml is None:
        raise RuntimeError("PyYAML required for pending lesson merge")

    existing = yaml.safe_load(existing_path.read_text(encoding="utf-8"))

    # Update occurrence tracking
    existing["_occurrence_count"] = existing.get("_occurrence_count", 1) + 1
    existing["detected_at"] = _now_iso()

    # Track new repo if not seen before
    new_repo = _get_repo_name()
    repos = existing.get("_repos_seen", [])
    if new_repo not in repos:
        repos.append(new_repo)
        existing["_repos_seen"] = repos

    # If fingerprint is same but we have more evidence, keep the latest
    # (fingerprint already matches, so no need to update)

    _write_pending_yaml(existing_path, existing)
    return existing


def _write_pending_yaml(path: Path, data: dict) -> None:
    """Write pending lesson to YAML file."""
    # Remove internal tracking fields before writing
    to_write = {k: v for k, v in data.items() if not k.startswith("_")}

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            to_write,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )


def write_pending_from_violations(
    violations: list[dict[str, Any]],
    diff_text: str,
    diff_baseline: str | None = None,
    pending_dir: Path | str = PENDING_DIR,
) -> list[dict[str, Any]]:
    """
    Write all blocking violations as pending lessons.

    Args:
        violations: List of violation dicts from scan_risks.scan()
        diff_text: The full diff text
        diff_baseline: Git ref used for scanning
        pending_dir: Directory for pending files

    Returns:
        List of created/updated pending lesson data
    """
    pending_dir = Path(pending_dir)
    pending_dir.mkdir(parents=True, exist_ok=True)

    results = []
    seen_fingerprints: set[str] = set()

    for v in violations:
        # Only write blocking violations as pending
        if v.get("mode") == "warn":
            continue

        try:
            pending = create_pending_lesson(
                v,
                diff_text,
                diff_baseline,
                pending_dir,
            )
            # Avoid duplicate fingerprints in same run
            if pending["fingerprint"] not in seen_fingerprints:
                seen_fingerprints.add(pending["fingerprint"])
                results.append(pending)
        except Exception as e:
            sys.stderr.write(f"[pending-writer] failed to write pending: {e}\n")

    return results
