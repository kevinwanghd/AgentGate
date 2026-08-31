#!/usr/bin/env python3
"""
lessons_review.py — Pending lessons review workflow

Implements the review flow for pending candidate governance rules:
- list: Show pending lessons with aggregation
- review: Interactive review of a specific pending lesson
- confirm: Mark as confirmed candidate
- reject: Mark as rejected with reason
- apply: Promote to patterns/ or lessons/

Usage:
    python scripts/lessons_review.py list
    python scripts/lessons_review.py review <id>
    python scripts/lessons_review.py confirm <id> --classification pattern --enforcement soft
    python scripts/lessons_review.py reject <id> --reason "Not a governance issue"
    python scripts/lessons_review.py apply <id> --to patterns/python.yml
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    sys.stderr.write("[lessons-review] missing pyyaml; install pyyaml\n")
    sys.exit(1)

# Add scripts dir to path for imports
sys.path.insert(0, str(Path(__file__).parent))
import fingerprint as fp_module


PENDING_DIR = Path(".governance/pending-lessons")
PATTERNS_DIR = Path("patterns")
LESSONS_DIR = Path("lessons")


# Classification options
CLASSIFICATIONS = ("pattern", "process", "none")

# Enforcement options
ENFORCEMENTS = ("soft", "hard")


def _load_pending(pending_id: str) -> tuple[Path | None, dict]:
    """Load pending lesson by ID. Returns (path, data)."""
    if not PENDING_DIR.exists():
        return None, {}

    for file in PENDING_DIR.glob("*.yml"):
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8"))
            if data and data.get("id") == pending_id:
                return file, data
        except Exception:
            continue

    return None, {}


def _load_all_pending() -> list[tuple[Path, dict]]:
    """Load all pending lessons."""
    results = []
    if not PENDING_DIR.exists():
        return results

    for file in PENDING_DIR.glob("*.yml"):
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8"))
            if data:
                results.append((file, data))
        except Exception:
            continue

    return sorted(results, key=lambda x: x[1].get("detected_at", ""))


def _now_iso() -> str:
    """Return current UTC time in ISO 8601 format."""
    return dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_list(args: argparse.Namespace) -> int:
    """List pending lessons with aggregation by fingerprint."""
    all_pending = _load_all_pending()

    if not all_pending:
        print("[lessons-review] no pending lessons found")
        return 0

    # Group by fingerprint for aggregation
    by_fingerprint: dict[str, list[tuple[Path, dict]]] = {}
    for path, data in all_pending:
        fp = data.get("fingerprint", "")
        if fp not in by_fingerprint:
            by_fingerprint[fp] = []
        by_fingerprint[fp].append((path, data))

    print(f"[lessons-review] pending lessons: {len(all_pending)}")
    print(f"[lessons-review] unique fingerprints: {len(by_fingerprint)}\n")

    for fp, entries in sorted(by_fingerprint.items(), key=lambda x: x[1][0][1].get("detected_at", "")):
        first = entries[0][1]
        status = first.get("status", "pending")
        pattern_type = first.get("pattern_type", "unknown")
        repos = list(set(e[1].get("source_repo", "") for e in entries))
        count = len(entries)

        print(f"Fingerprint: {fp}")
        print(f"  Pattern Type: {pattern_type}")
        print(f"  Status: {status}")
        print(f"  Occurrences: {count} (repos: {', '.join(repos)})")
        print(f"  Last Detected: {first.get('detected_at', 'unknown')}")
        print(f"  Sample File: {first.get('failure_context', {}).get('file', 'N/A')}")
        print(f"  Sample Snippet: {first.get('failure_context', {}).get('snippet', 'N/A')[:80]}")

        # Show all IDs in this fingerprint group
        ids = [e[1].get("id", "")[:8] for e in entries]
        print(f"  IDs: {', '.join(ids)}")
        print()

    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """Review a specific pending lesson by ID."""
    path, data = _load_pending(args.id)

    if not path or not data:
        print(f"[lessons-review] pending lesson '{args.id}' not found")
        return 1

    print("=" * 60)
    print("PENDING LESSON REVIEW")
    print("=" * 60)
    print(f"ID: {data.get('id')}")
    print(f"Pattern Type: {data.get('pattern_type')}")
    print(f"Status: {data.get('status')}")
    print(f"Fingerprint: {data.get('fingerprint')}")
    print(f"Source Repo: {data.get('source_repo')}")
    print(f"Source Ref: {data.get('source_ref')}")
    print(f"Detected At: {data.get('detected_at')}")

    fc = data.get("failure_context", {})
    print(f"\nFailure Context:")
    print(f"  File: {fc.get('file')}")
    print(f"  Line: {fc.get('line')}")
    print(f"  Snippet: {fc.get('snippet')}")
    print(f"  Diff Baseline: {fc.get('diff_baseline')}")

    ev = data.get("evidence", {})
    print(f"\nEvidence:")
    print(f"  Rule ID: {ev.get('rule_id')}")
    print(f"  Scan Summary: {ev.get('scan_summary')}")
    print(f"  Raw Diff: {ev.get('raw_diff_fragment', '')[:200]}...")

    print(f"\nRegression: {data.get('regression')}")

    if data.get("reviewer"):
        print(f"\nReview Info:")
        print(f"  Reviewer: {data.get('reviewer')}")
        print(f"  Reviewed At: {data.get('reviewed_at')}")
        print(f"  Decision Reason: {data.get('decision_reason')}")
        print(f"  Classification: {data.get('classification')}")
        print(f"  Enforcement: {data.get('enforcement')}")

    if data.get("promoted_to"):
        print(f"  Promoted To: {data.get('promoted_to')}")

    print("=" * 60)
    print(f"\nActions: confirm, reject, apply")
    return 0


def cmd_confirm(args: argparse.Namespace) -> int:
    """Confirm a pending lesson as a candidate rule."""
    path, data = _load_pending(args.id)

    if not path or not data:
        print(f"[lessons-review] pending lesson '{args.id}' not found")
        return 1

    if data.get("status") not in ("pending", "confirmed"):
        print(f"[lessons-review] cannot confirm - current status is '{data.get('status')}'")
        return 1

    # Validate arguments
    if not args.classification:
        print("[lessons-review] --classification required (pattern|process|none)")
        return 1

    if args.classification not in CLASSIFICATIONS:
        print(f"[lessons-review] --classification must be one of {CLASSIFICATIONS}")
        return 1

    enforcement = args.enforcement or "soft"
    if enforcement not in ENFORCEMENTS:
        print(f"[lessons-review] --enforcement must be one of {ENFORCEMENTS}")
        return 1

    # For hard enforcement, require owner
    if enforcement == "hard" and not args.owner:
        print("[lessons-review] --owner required for hard enforcement")
        return 1

    # Update the pending file
    data["status"] = "confirmed"
    data["reviewer"] = args.reviewer or _get_reviewer_from_git()
    data["reviewed_at"] = _now_iso()
    data["decision_reason"] = args.reason or "Confirmed as candidate rule"
    data["classification"] = args.classification
    data["enforcement"] = enforcement
    if args.owner:
        data["owner"] = args.owner

    _write_pending(path, data)

    print(f"[lessons-review] confirmed: {args.id}")
    print(f"  Classification: {args.classification}")
    print(f"  Enforcement: {enforcement}")
    if args.owner:
        print(f"  Owner: {args.owner}")

    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    """Reject a pending lesson with reason."""
    path, data = _load_pending(args.id)

    if not path or not data:
        print(f"[lessons-review] pending lesson '{args.id}' not found")
        return 1

    if data.get("status") == "rejected":
        print(f"[lessons-review] already rejected")
        return 0

    if not args.reason:
        print("[lessons-review] --reason required for rejection")
        return 1

    # Update the pending file
    data["status"] = "rejected"
    data["reviewer"] = args.reviewer or _get_reviewer_from_git()
    data["reviewed_at"] = _now_iso()
    data["decision_reason"] = args.reason

    _write_pending(path, data)

    print(f"[lessons-review] rejected: {args.id}")
    print(f"  Reason: {args.reason}")

    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    """Apply a confirmed lesson to patterns/ or lessons/."""
    path, data = _load_pending(args.id)

    if not path or not data:
        print(f"[lessons-review] pending lesson '{args.id}' not found")
        return 1

    if data.get("status") != "confirmed":
        print(f"[lessons-review] must be confirmed before apply (current: {data.get('status')})")
        return 1

    classification = data.get("classification", "pattern")
    enforcement = data.get("enforcement", "soft")

    if classification == "none":
        print("[lessons-review] classification 'none' cannot be applied")
        return 1

    # Determine target file
    if args.to:
        target = Path(args.to)
    elif classification == "pattern":
        # Infer language from file extension in snippet
        snippet_file = data.get("failure_context", {}).get("file", "")
        ext = Path(snippet_file).suffix
        lang_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "javascript",
            ".java": "java",
            ".go": "go",
            ".cs": "csharp",
            ".dart": "dart",
        }
        lang = lang_map.get(ext, "python")
        target = PATTERNS_DIR / f"{lang}.yml"
    else:
        target = LESSONS_DIR / f"custom-{data.get('pattern_type', 'rule')}.yml"

    target.parent.mkdir(parents=True, exist_ok=True)

    # Create the pattern/lesson entry
    entry = _create_entry(data)

    # Append to target file
    _append_to_yaml_file(target, entry)

    # Update pending status
    data["status"] = "promoted"
    data["promoted_to"] = str(target)
    _write_pending(path, data)

    print(f"[lessons-review] applied: {args.id} -> {target}")
    print(f"  Enforcement: {enforcement}")

    return 0


def _create_entry(data: dict) -> dict:
    """Create a pattern/lesson entry from pending data."""
    pattern_type = data.get("pattern_type", "unknown")
    enforcement = data.get("enforcement", "soft")

    if enforcement == "hard":
        # For hard enforcement, include executable check fields
        return {
            "type": pattern_type,
            "desc": data.get("evidence", {}).get("scan_summary", ""),
            "regex": _snippet_to_regex(data.get("failure_context", {}).get("snippet", "")),
            "mode": "block" if enforcement == "hard" else "warn",
            "owner": data.get("owner", "@agentgate"),
        }
    else:
        return {
            "type": pattern_type,
            "desc": data.get("evidence", {}).get("scan_summary", ""),
            "regex": _snippet_to_regex(data.get("failure_context", {}).get("snippet", "")),
            "mode": "warn",
        }


def _snippet_to_regex(snippet: str) -> str:
    """Convert a code snippet to a regex pattern (simplified)."""
    if not snippet:
        return ".*"

    # Simple normalization: escape special regex chars
    escaped = re.escape(snippet)
    # Collapse whitespace
    escaped = re.sub(r'\\ +', r'\\s+', escaped)
    return escaped


def _write_pending(path: Path, data: dict) -> None:
    """Write pending lesson to file."""
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )


def _append_to_yaml_file(path: Path, entry: dict) -> None:
    """Append entry to a YAML file, creating if necessary."""
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if "patterns" not in data:
            data["patterns"] = []
        data["patterns"].append(entry)
    else:
        # Create new file with header
        data = {
            "# Auto-generated from pending lessons" if "patterns" in path.name else "# Custom lessons": "",
            "patterns" if "patterns" in path.name else "lessons": [entry],
        }

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)


def _get_reviewer_from_git() -> str:
    """Get the current git user as reviewer."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pending lessons review workflow")
    subparsers = parser.add_subparsers(dest="command", help="commands")

    # list command
    subparsers.add_parser("list", help="List pending lessons with aggregation")

    # review command
    review_parser = subparsers.add_parser("review", help="Review a specific pending lesson")
    review_parser.add_argument("id", help="Pending lesson ID (or fingerprint)")

    # confirm command
    confirm_parser = subparsers.add_parser("confirm", help="Confirm a pending lesson")
    confirm_parser.add_argument("id", help="Pending lesson ID")
    confirm_parser.add_argument("--classification", choices=CLASSIFICATIONS, required=True,
                               help="Classification: pattern (regex-able) or process (workflow)")
    confirm_parser.add_argument("--enforcement", choices=ENFORCEMENTS, default="soft",
                               help="Enforcement level (default: soft)")
    confirm_parser.add_argument("--owner", help="Owner for hard enforcement rules")
    confirm_parser.add_argument("--reason", help="Confirmation reason")
    confirm_parser.add_argument("--reviewer", help="Reviewer username")

    # reject command
    reject_parser = subparsers.add_parser("reject", help="Reject a pending lesson")
    reject_parser.add_argument("id", help="Pending lesson ID")
    reject_parser.add_argument("--reason", required=True, help="Rejection reason")
    reject_parser.add_argument("--reviewer", help="Reviewer username")

    # apply command
    apply_parser = subparsers.add_parser("apply", help="Apply a confirmed lesson")
    apply_parser.add_argument("id", help="Pending lesson ID")
    apply_parser.add_argument("--to", help="Target file path (auto-detected if omitted)")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "list": cmd_list,
        "review": cmd_review,
        "confirm": cmd_confirm,
        "reject": cmd_reject,
        "apply": cmd_apply,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
