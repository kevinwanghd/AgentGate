#!/usr/bin/env python3
"""
aggregate_pending.py — Cross-repository fingerprint aggregation

Aggregates pending lessons by fingerprint across multiple repositories.
Provides queries for:
- Occurrence count per fingerprint
- Unique repository count
- Last detection time
- Status aggregation
- Conflict resolution

Usage:
    python scripts/aggregate_pending.py --root .
    python scripts/aggregate_pending.py --root . --fingerprint a1b2c3d4e5f6g7h8
    python scripts/aggregate_pending.py --root . --status pending
    python scripts/aggregate_pending.py --root . --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None


PENDING_DIR = Path(".governance/pending-lessons")


class AggregatedFingerprint:
    """Aggregated view of a fingerprint across multiple pending lessons."""

    def __init__(self, fingerprint: str):
        self.fingerprint = fingerprint
        self.pending_entries: list[dict] = []
        self.occurrence_count = 0
        self.repos_seen: set[str] = set()
        self.refs_seen: set[str] = set()
        self.first_detected: str | None = None
        self.last_detected: str | None = None
        self.pattern_type: str | None = None
        self.status: str = "pending"
        self.latest_decision: str | None = None
        self.latest_reviewer: str | None = None
        self.latest_reviewed_at: str | None = None

    def add(self, data: dict) -> None:
        """Add a pending entry to this fingerprint group."""
        self.pending_entries.append(data)
        self.occurrence_count += 1
        self.repos_seen.add(data.get("source_repo", ""))
        self.refs_seen.add(data.get("source_ref", ""))

        detected = data.get("detected_at", "")
        if detected:
            if self.first_detected is None or detected < self.first_detected:
                self.first_detected = detected
            if self.last_detected is None or detected > self.last_detected:
                self.last_detected = detected

        if self.pattern_type is None:
            self.pattern_type = data.get("pattern_type")

        # Track the most recent review decision
        reviewed_at = data.get("reviewed_at", "")
        if reviewed_at:
            if self.latest_reviewed_at is None or reviewed_at > self.latest_reviewed_at:
                self.latest_reviewed_at = reviewed_at
                self.latest_decision = data.get("status")
                self.latest_reviewer = data.get("reviewer")

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON output."""
        return {
            "fingerprint": self.fingerprint,
            "pattern_type": self.pattern_type,
            "occurrence_count": self.occurrence_count,
            "repo_count": len(self.repos_seen),
            "repos": sorted(self.repos_seen),
            "refs": sorted(self.refs_seen),
            "first_detected": self.first_detected,
            "last_detected": self.last_detected,
            "status": self.latest_decision or self.status,
            "reviewer": self.latest_reviewer,
            "reviewed_at": self.latest_reviewed_at,
        }


def load_pending_files(roots: list[Path]) -> list[tuple[Path, dict]]:
    """
    Load pending files from multiple repository roots.

    Returns list of (file_path_relative_to_root, data) tuples.
    """
    results = []

    for root in roots:
        pending = root / PENDING_DIR
        if not pending.exists():
            continue

        for file in pending.glob("*.yml"):
            try:
                data = yaml.safe_load(file.read_text(encoding="utf-8")) if yaml else {}
                if data:
                    results.append((file, data))
            except Exception:
                continue

    return results


def aggregate_dicts_by_fingerprint(pending_dicts: list[dict]) -> dict[str, AggregatedFingerprint]:
    """Aggregate pending dicts by fingerprint (without file paths)."""
    aggregations: dict[str, AggregatedFingerprint] = {}

    for data in pending_dicts:
        fp = data.get("fingerprint", "")
        if not fp:
            continue

        if fp not in aggregations:
            aggregations[fp] = AggregatedFingerprint(fp)

        aggregations[fp].add(data)

    return aggregations


def aggregate_by_fingerprint(pending_files: list[tuple[Path, dict]]) -> dict[str, AggregatedFingerprint]:
    """Aggregate pending files by fingerprint."""
    aggregations: dict[str, AggregatedFingerprint] = {}

    for file, data in pending_files:
        fp = data.get("fingerprint", "")
        if not fp:
            continue

        if fp not in aggregations:
            aggregations[fp] = AggregatedFingerprint(fp)

        aggregations[fp].add(data)

    return aggregations


def filter_by_status(
    aggregations: dict[str, AggregatedFingerprint],
    status: str | None,
) -> dict[str, AggregatedFingerprint]:
    """Filter aggregations by status."""
    if not status:
        return aggregations

    return {
        fp: agg
        for fp, agg in aggregations.items()
        if agg.latest_decision == status or (not agg.latest_decision and status == "pending")
    }


def filter_by_fingerprint(
    aggregations: dict[str, AggregatedFingerprint],
    fingerprint: str | None,
) -> dict[str, AggregatedFingerprint]:
    """Filter to a specific fingerprint."""
    if not fingerprint:
        return aggregations

    if fingerprint in aggregations:
        return {fingerprint: aggregations[fingerprint]}

    # Also try partial match
    return {
        fp: agg
        for fp, agg in aggregations.items()
        if fp.startswith(fingerprint)
    }


def resolve_conflicts(
    aggregations: dict[str, AggregatedFingerprint],
    strategy: str = "latest",
) -> list[dict]:
    """
    Resolve conflicts when the same fingerprint has different decisions.

    Strategies:
    - latest: Use the most recent review decision
    - confirmed: Prefer confirmed over rejected
    - promoted: Prefer promoted, then confirmed, then pending
    """
    conflicts = []

    for fp, agg in aggregations.items():
        statuses = set(e.get("status") for e in agg.pending_entries)

        if len(statuses) > 1:
            conflict = {
                "fingerprint": fp,
                "pattern_type": agg.pattern_type,
                "statuses": sorted(statuses),
                "entries": len(agg.pending_entries),
                "repos": sorted(agg.repos_seen),
            }

            if strategy == "latest":
                conflict["resolved_status"] = agg.latest_decision
                conflict["resolved_by"] = agg.latest_reviewer
            elif strategy == "confirmed":
                if "confirmed" in statuses:
                    conflict["resolved_status"] = "confirmed"
                else:
                    conflict["resolved_status"] = "pending"
            elif strategy == "promoted":
                if "promoted" in statuses:
                    conflict["resolved_status"] = "promoted"
                elif "confirmed" in statuses:
                    conflict["resolved_status"] = "confirmed"
                elif "rejected" in statuses:
                    conflict["resolved_status"] = "rejected"
                else:
                    conflict["resolved_status"] = "pending"

            conflicts.append(conflict)

    return conflicts


def print_aggregation(
    aggregations: dict[str, AggregatedFingerprint],
    verbose: bool = False,
) -> None:
    """Print aggregation summary to stdout."""
    print(f"[aggregate-pending] Found {len(aggregations)} unique fingerprints\n")

    for fp in sorted(aggregations.keys()):
        agg = aggregations[fp]
        status = agg.latest_decision or "pending"

        print(f"Fingerprint: {fp}")
        print(f"  Pattern Type: {agg.pattern_type or 'unknown'}")
        print(f"  Status: {status}")
        print(f"  Occurrences: {agg.occurrence_count}")
        print(f"  Repositories: {len(agg.repos_seen)} ({', '.join(sorted(agg.repos_seen))})")
        print(f"  First Detected: {agg.first_detected or 'N/A'}")
        print(f"  Last Detected: {agg.last_detected or 'N/A'}")

        if verbose and agg.pending_entries:
            print(f"  Entries:")
            for entry in agg.pending_entries:
                print(f"    - ID: {entry.get('id', 'N/A')[:8]}...")
                print(f"      Repo: {entry.get('source_repo')}")
                print(f"      Ref: {entry.get('source_ref')}")
                print(f"      File: {entry.get('failure_context', {}).get('file', 'N/A')}")

        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate pending lessons by fingerprint")
    parser.add_argument("--root", default=".", help="repository root(s), comma-separated")
    parser.add_argument("--fingerprint", help="Filter to specific fingerprint")
    parser.add_argument("--status", choices=["pending", "confirmed", "rejected", "promoted"],
                      help="Filter by status")
    parser.add_argument("--conflicts", action="store_true", help="Show conflicts")
    parser.add_argument("--conflict-strategy", choices=["latest", "confirmed", "promoted"],
                      default="latest", help="Conflict resolution strategy")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args(argv)

    if yaml is None:
        sys.stderr.write("[aggregate-pending] missing pyyaml\n")
        return 1

    # Parse roots
    roots = [Path(r) for r in args.root.split(",")]

    # Load and aggregate
    pending_files = load_pending_files(roots)
    aggregations = aggregate_by_fingerprint(pending_files)

    # Apply filters
    if args.fingerprint:
        aggregations = filter_by_fingerprint(aggregations, args.fingerprint)

    if args.status:
        aggregations = filter_by_status(aggregations, args.status)

    if not aggregations:
        print("[aggregate-pending] no matching fingerprints found")
        return 0

    # Show conflicts if requested
    if args.conflicts:
        conflicts = resolve_conflicts(aggregations, args.conflict_strategy)
        if conflicts:
            print(f"[aggregate-pending] Found {len(conflicts)} conflicts:\n")
            for c in conflicts:
                print(f"Fingerprint: {c['fingerprint']}")
                print(f"  Statuses: {', '.join(c['statuses'])}")
                print(f"  Resolved: {c.get('resolved_status', 'N/A')}")
                print()

    # Output
    if args.json:
        result = {
            "fingerprints": [agg.to_dict() for agg in aggregations.values()],
        }
        if args.conflicts:
            result["conflicts"] = resolve_conflicts(aggregations, args.conflict_strategy)
        print(json.dumps(result, indent=2))
    else:
        print_aggregation(aggregations, verbose=args.verbose)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
