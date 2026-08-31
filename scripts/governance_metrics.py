#!/usr/bin/env python3
"""
governance_metrics.py — Governance metrics calculation

Calculates governance metrics from pending lesson reviews:
- Confirmation rate
- Rejection rate  
- Obsolescence rate: rejected / (confirmed + rejected)
- Duplicate fingerprint rate
- Rule hit rate
- Review latency (discovery to review)

Metrics are calculated from .governance/pending-lessons/ data.

Usage:
    python scripts/governance_metrics.py --root .
    python scripts/governance_metrics.py --root . --json
    python scripts/governance_metrics.py --root . --threshold 0.8
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None


PENDING_DIR = Path(".governance/pending-lessons")


# Threshold configuration
THRESHOLDS = {
    "confirmation_rate": {"green": 0.7, "yellow": 0.5},  # Higher is better
    "rejection_rate": {"green": 0.3, "yellow": 0.5},      # Lower is better
    "obsolescence_rate": {"green": 0.3, "yellow": 0.5},   # Lower is better
    "duplicate_fingerprint_rate": {"green": 0.1, "yellow": 0.2},  # Lower is better
}


def load_pending_files(root: Path) -> list[dict]:
    """Load all pending lesson files from a repository."""
    pending_dir = root / PENDING_DIR
    if not pending_dir.exists():
        return []

    files = []
    for file in pending_dir.glob("*.yml"):
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8")) if yaml else {}
            if data:
                files.append(data)
        except Exception:
            continue

    return files


def aggregate_by_fingerprint(pending_files: list[dict]) -> dict[str, list[dict]]:
    """Group pending files by fingerprint."""
    by_fp: dict[str, list[dict]] = {}
    for data in pending_files:
        fp = data.get("fingerprint", "")
        if fp:
            by_fp.setdefault(fp, []).append(data)
    return by_fp


def calculate_confirmation_rate(pending_files: list[dict]) -> float | str:
    """Calculate confirmation rate: confirmed / (confirmed + rejected + pending)."""
    total = len(pending_files)
    if total == 0:
        return "N/A"

    confirmed = sum(1 for p in pending_files if p.get("status") == "confirmed")
    # For confirmation rate, we consider all non-rejected as potentially confirmable
    # Or just confirmed / total
    return round(confirmed / total, 4)


def calculate_rejection_rate(pending_files: list[dict]) -> float | str:
    """Calculate rejection rate: rejected / total."""
    total = len(pending_files)
    if total == 0:
        return "N/A"

    rejected = sum(1 for p in pending_files if p.get("status") == "rejected")
    return round(rejected / total, 4)


def calculate_obsolescence_rate(pending_files: list[dict]) -> float | str:
    """
    Calculate obsolescence rate: rejected / (confirmed + rejected).
    
    This is the废弃率 as specified in the requirements.
    When denominator is zero, returns "N/A".
    """
    confirmed = sum(1 for p in pending_files if p.get("status") == "confirmed")
    rejected = sum(1 for p in pending_files if p.get("status") == "rejected")

    denominator = confirmed + rejected
    if denominator == 0:
        return "N/A"

    return round(rejected / denominator, 4)


def calculate_duplicate_fingerprint_rate(pending_files: list[dict], by_fp: dict[str, list[dict]]) -> float | str:
    """Calculate duplicate fingerprint rate: unique_fps / total_files."""
    total = len(pending_files)
    if total == 0:
        return "N/A"

    unique_fps = len(by_fp)
    # Rate of deduplication = 1 - (unique / total)
    # Higher rate means more duplicates (worse)
    return round(1 - (unique_fps / total), 4)


def calculate_rule_hit_rate(pending_files: list[dict]) -> float | str:
    """Calculate rule hit rate: confirmed + rejected / total."""
    total = len(pending_files)
    if total == 0:
        return "N/A"

    reviewed = sum(1 for p in pending_files if p.get("status") in ("confirmed", "rejected"))
    return round(reviewed / total, 4)


def calculate_review_latency(pending_files: list[dict]) -> float | str:
    """Calculate average review latency in days (detected_at to reviewed_at)."""
    latencies = []

    for p in pending_files:
        if p.get("status") not in ("confirmed", "rejected"):
            continue

        detected = p.get("detected_at", "")
        reviewed = p.get("reviewed_at", "")

        if not detected or not reviewed:
            continue

        try:
            d1 = dt.datetime.fromisoformat(detected.rstrip("Z"))
            d2 = dt.datetime.fromisoformat(reviewed.rstrip("Z"))
            latency = (d2 - d1).total_seconds() / 86400  # days
            latencies.append(latency)
        except (ValueError, TypeError):
            continue

    if not latencies:
        return "N/A"

    return round(sum(latencies) / len(latencies), 2)


def calculate_cross_repo_rate(by_fp: dict[str, list[dict]]) -> float | str:
    """Calculate rate of fingerprints seen in multiple repos."""
    if not by_fp:
        return "N/A"

    multi_repo = sum(1 for entries in by_fp.values() 
                    if len(set(e.get("source_repo", "") for e in entries)) > 1)
    return round(multi_repo / len(by_fp), 4)


def get_status_color(value: float | str, metric: str) -> str:
    """Determine color based on threshold."""
    if value == "N/A" or not isinstance(value, float):
        return "N/A"

    thresholds = THRESHOLDS.get(metric, {})
    if not thresholds:
        return "N/A"

    # For metrics where higher is better (confirmation_rate, rule_hit_rate)
    if metric in ("confirmation_rate", "rule_hit_rate"):
        if value >= thresholds.get("green", 0.7):
            return "GREEN"
        elif value >= thresholds.get("yellow", 0.5):
            return "YELLOW"
        else:
            return "RED"
    else:
        # For metrics where lower is better
        if value <= thresholds.get("green", 0.3):
            return "GREEN"
        elif value <= thresholds.get("yellow", 0.5):
            return "YELLOW"
        else:
            return "RED"


def calculate_all_metrics(pending_files: list[dict]) -> dict[str, Any]:
    """Calculate all governance metrics."""
    by_fp = aggregate_by_fingerprint(pending_files)

    metrics = {
        "total_pending_lessons": len(pending_files),
        "unique_fingerprints": len(by_fp),
        "confirmation_rate": calculate_confirmation_rate(pending_files),
        "rejection_rate": calculate_rejection_rate(pending_files),
        "obsolescence_rate": calculate_obsolescence_rate(pending_files),
        "duplicate_fingerprint_rate": calculate_duplicate_fingerprint_rate(pending_files, by_fp),
        "rule_hit_rate": calculate_rule_hit_rate(pending_files),
        "average_review_latency_days": calculate_review_latency(pending_files),
        "cross_repo_fingerprint_rate": calculate_cross_repo_rate(by_fp),
    }

    # Add status colors
    metrics["_colors"] = {}
    for metric in ("confirmation_rate", "rejection_rate", "obsolescence_rate", 
                   "duplicate_fingerprint_rate", "rule_hit_rate"):
        value = metrics[metric]
        if isinstance(value, float):
            metrics["_colors"][metric] = get_status_color(value, metric)

    # Add breakdown counts
    metrics["_breakdown"] = {
        "pending": sum(1 for p in pending_files if p.get("status") == "pending"),
        "confirmed": sum(1 for p in pending_files if p.get("status") == "confirmed"),
        "rejected": sum(1 for p in pending_files if p.get("status") == "rejected"),
        "promoted": sum(1 for p in pending_files if p.get("status") == "promoted"),
    }

    return metrics


def print_metrics(metrics: dict, threshold: float | None = None) -> None:
    """Print metrics to stdout."""
    print("=" * 50)
    print("GOVERNANCE METRICS")
    print("=" * 50)
    print()

    breakdown = metrics.get("_breakdown", {})
    print(f"Total Pending Lessons: {metrics.get('total_pending_lessons', 0)}")
    print(f"  - pending: {breakdown.get('pending', 0)}")
    print(f"  - confirmed: {breakdown.get('confirmed', 0)}")
    print(f"  - rejected: {breakdown.get('rejected', 0)}")
    print(f"  - promoted: {breakdown.get('promoted', 0)}")
    print()

    print(f"Unique Fingerprints: {metrics.get('unique_fingerprints', 0)}")
    print()

    colors = metrics.get("_colors", {})

    def fmt_rate(name: str, value: float | str) -> str:
        color = colors.get(name, "N/A")
        val_str = f"{value:.1%}" if isinstance(value, float) else str(value)
        return f"{val_str} [{color}]"

    print("Rates:")
    cr = metrics.get("confirmation_rate", "N/A")
    rr = metrics.get("rejection_rate", "N/A")
    or_ = metrics.get("obsolescence_rate", "N/A")
    dr = metrics.get("duplicate_fingerprint_rate", "N/A")
    rh = metrics.get("rule_hit_rate", "N/A")

    print(f"  Confirmation Rate:     {fmt_rate('confirmation_rate', cr)}")
    print(f"  Rejection Rate:        {fmt_rate('rejection_rate', rr)}")
    print(f"  Obsolescence Rate:    {fmt_rate('obsolescence_rate', or_)} (rejected / (confirmed + rejected))")
    print(f"  Duplicate Fingerprint: {fmt_rate('duplicate_fingerprint_rate', dr)}")
    print(f"  Rule Hit Rate:        {fmt_rate('rule_hit_rate', rh)}")
    print()

    latency = metrics.get("average_review_latency_days", "N/A")
    if isinstance(latency, float):
        print(f"Average Review Latency: {latency:.1f} days")
    else:
        print(f"Average Review Latency: {latency}")

    cross_repo = metrics.get("cross_repo_fingerprint_rate", "N/A")
    if isinstance(cross_repo, float):
        print(f"Cross-Repo Fingerprint Rate: {cross_repo:.1%}")
    else:
        print(f"Cross-Repo Fingerprint Rate: {cross_repo}")

    print()
    print("=" * 50)

    # Threshold check
    if threshold is not None:
        print(f"\nThreshold check (min confirmation rate: {threshold:.0%})")
        cr = metrics.get("confirmation_rate", 0)
        if isinstance(cr, float) and cr >= threshold:
            print(f"  PASS: {cr:.1%} >= {threshold:.0%}")
        else:
            print(f"  WARN: Confirmation rate below threshold")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calculate governance metrics")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--threshold", type=float, help="Minimum confirmation rate threshold")
    args = parser.parse_args(argv)

    if yaml is None:
        sys.stderr.write("[governance-metrics] missing pyyaml\n")
        return 1

    root = Path(args.root).resolve()
    pending_files = load_pending_files(root)

    if not pending_files:
        print("[governance-metrics] no pending lessons found")
        if args.json:
            print(json.dumps({"total_pending_lessons": 0}))
        return 0

    metrics = calculate_all_metrics(pending_files)

    if args.json:
        # Remove internal fields for JSON output
        output = {k: v for k, v in metrics.items() if not k.startswith("_")}
        print(json.dumps(output, indent=2))
    else:
        print_metrics(metrics, threshold=args.threshold)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
