#!/usr/bin/env python3
"""
governance_aggregate.py — 跨仓库 fingerprint 聚合查询

按 fingerprint 聚合多个仓库的 pending lessons，显示：
- 出现次数
- 涉及仓库数
- 最近一次发现
- 已确认/拒绝/升级状态

用法:
    python scripts/governance_aggregate.py
    python scripts/governance_aggregate.py --fingerprint <hash>
    python scripts/governance_aggregate.py --top 10
    python scripts/governance_aggregate.py --repo deliverhq
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------- 聚合逻辑 ----------

def scan_pending_dirs(repo_paths: list[Path]) -> dict[str, list[dict]]:
    """
    扫描多个仓库的 pending lessons，按 fingerprint 分组。
    返回: {fingerprint: [pending_data, ...]}
    """
    aggregated: dict[str, list[dict]] = defaultdict(list)

    for repo_path in repo_paths:
        pending_dir = repo_path / ".governance" / "pending-lessons"
        if not pending_dir.exists():
            continue

        for json_file in pending_dir.glob("*.json"):
            try:
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)
                fp = data.get("fingerprint")
                if fp:
                    # 附加来源信息
                    data["_source_repo"] = repo_path.name
                    data["_source_file"] = str(json_file.relative_to(repo_path))
                    aggregated[fp].append(data)
            except Exception:
                pass

    return dict(aggregated)


def aggregate_fingerprint(fp: str, entries: list[dict]) -> dict:
    """
    聚合单个 fingerprint 的所有 entries。
    """
    if not entries:
        return {}

    first = entries[0]
    repos = set(e.get("_source_repo", "?") for e in entries)

    # 状态统计
    status_counts = {"pending": 0, "confirmed": 0, "rejected": 0, "promoted": 0}
    for e in entries:
        status_counts[e.get("status", "pending")] += 1

    # 时间范围
    detected_times = []
    for e in entries:
        ts = e.get("detected_at") or e.get("last_seen") or ""
        if ts:
            try:
                detected_times.append(datetime.fromisoformat(ts))
            except Exception:
                pass

    latest = max(detected_times) if detected_times else None
    earliest = min(detected_times) if detected_times else None

    # 合并 evidence_history
    all_evidence = []
    for e in entries:
        hist = e.get("evidence_history", [])
        all_evidence.extend(hist)

    return {
        "fingerprint": fp,
        "pattern_type": first.get("pattern_type", "?"),
        "description": first.get("failure_context", {}).get("pattern_description", "?"),
        "language": first.get("failure_context", {}).get("language", "?"),
        "repos": sorted(repos),
        "repo_count": len(repos),
        "total_occurrences": sum(e.get("occurrence_count", 1) for e in entries),
        "entry_count": len(entries),
        "status_counts": status_counts,
        "confirmed_count": status_counts["confirmed"] + status_counts["promoted"],
        "rejected_count": status_counts["rejected"],
        "latest_detection": latest.isoformat() if latest else None,
        "earliest_detection": earliest.isoformat() if earliest else None,
        "regression": first.get("regression", ""),
        "evidence_history": all_evidence[:20],  # 最多显示20条
    }


def print_fingerprint_summary(agg: dict) -> None:
    """打印单个 fingerprint 的聚合摘要。"""
    fp = agg["fingerprint"]
    pattern = agg["pattern_type"]
    desc = agg["description"][:50]
    repos = ", ".join(agg["repos"])
    total = agg["total_occurrences"]
    status = agg["status_counts"]

    print(f"\n{'='*60}")
    print(f"Fingerprint: {fp}")
    print(f"风险类型: {pattern}")
    print(f"语言: {agg['language']}")
    print(f"描述: {desc}")
    print(f"来源仓库: {repos} ({agg['repo_count']} 个)")
    print(f"累计命中: {total} 次 ({agg['entry_count']} 条记录)")
    print()
    print(f"状态分布:")
    print(f"  pending:   {status['pending']}")
    print(f"  confirmed: {status['confirmed']}")
    print(f"  rejected:  {status['rejected']}")
    print(f"  promoted: {status['promoted']}")
    print()
    if agg["earliest_detection"]:
        print(f"首次发现: {agg['earliest_detection'][:10]}")
    if agg["latest_detection"]:
        print(f"最近发现: {agg['latest_detection'][:10]}")

    # 废弃率
    total_decided = agg["confirmed_count"] + agg["rejected_count"]
    if total_decided > 0:
        rate = agg["rejected_count"] / total_decided
        print(f"废弃率: {rate:.1%} ({agg['rejected_count']}/{total_decided})")
    print()

    # 最近 evidence
    if agg["evidence_history"]:
        print(f"最近发现记录 ({len(agg['evidence_history'])} 条):")
        for ev in agg["evidence_history"][:5]:
            repo = ev.get("source_repo", "?")
            file = ev.get("file", "?")
            line = ev.get("line", "?")
            ref = ev.get("source_ref", "?")[:8]
            dt = (ev.get("detected_at") or "")[:10]
            print(f"  [{dt}] {repo} {file}:{line} @ {ref}")


def print_top_fingerprints(aggregated: dict[str, list[dict]], top_n: int = 10) -> None:
    """打印最常见的 fingerprint 排名。"""
    summaries = []
    for fp, entries in aggregated.items():
        agg = aggregate_fingerprint(fp, entries)
        if agg:
            summaries.append(agg)

    # 按累计命中数排序
    summaries.sort(key=lambda x: -x["total_occurrences"])

    print(f"\n{'='*60}")
    print(f"Top {top_n} Fingerprints (按累计命中数排序)")
    print(f"{'='*60}\n")

    for i, agg in enumerate(summaries[:top_n], 1):
        fp = agg["fingerprint"]
        pattern = agg["pattern_type"]
        repos = ", ".join(agg["repos"])
        total = agg["total_occurrences"]
        status = agg["status_counts"]
        latest = (agg["latest_detection"] or "")[:10]

        print(f"[{i:2d}] {fp}")
        print(f"     类型: {pattern}")
        print(f"     仓库: {repos}")
        print(f"     命中: {total} 次 | pending:{status['pending']} "
              f"confirmed:{status['confirmed']} rejected:{status['rejected']}")
        print(f"     最近: {latest}")
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="跨仓库 fingerprint 聚合查询")
    parser.add_argument(
        "--repo-path",
        action="append",
        dest="repo_paths",
        help="额外扫描的仓库路径（可多次指定）"
    )
    parser.add_argument(
        "--fingerprint",
        help="查询特定 fingerprint 的详情"
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="显示 top N 常见 fingerprint（默认: 10）"
    )
    parser.add_argument(
        "--repo",
        dest="filter_repo",
        help="按仓库过滤"
    )
    parser.add_argument(
        "--type",
        dest="filter_type",
        help="按风险类型过滤"
    )
    args = parser.parse_args(argv)

    # 确定要扫描的仓库路径
    script_dir = Path(__file__).parent.parent
    repo_paths = [script_dir]  # 始终包含当前仓库

    if args.repo_paths:
        for p in args.repo_paths:
            rp = Path(p).resolve()
            if rp.exists():
                repo_paths.append(rp)
            else:
                sys.stderr.write(f"[aggregate] 仓库路径不存在: {rp}\n")

    print(f"[aggregate] 扫描 {len(repo_paths)} 个仓库...")

    aggregated = scan_pending_dirs(repo_paths)
    print(f"[aggregate] 发现 {len(aggregated)} 个唯一 fingerprint")

    if not aggregated:
        print("[aggregate] 暂无 pending lessons 数据")
        return 0

    # 过滤
    if args.filter_repo or args.filter_type:
        filtered = {}
        for fp, entries in aggregated.items():
            keep = True
            if args.filter_repo:
                keep = any(e.get("_source_repo") == args.filter_repo for e in entries)
            if args.filter_type and keep:
                keep = any(e.get("pattern_type") == args.filter_type for e in entries)
            if keep:
                filtered[fp] = entries
        aggregated = filtered
        print(f"[aggregate] 过滤后: {len(aggregated)} 个 fingerprint")

    # 查询特定 fingerprint
    if args.fingerprint:
        entries = aggregated.get(args.fingerprint, [])
        if not entries:
            # 模糊匹配
            for fp, ents in aggregated.items():
                if args.fingerprint in fp:
                    entries = ents
                    break

        if entries:
            agg = aggregate_fingerprint(args.fingerprint, entries)
            print_fingerprint_summary(agg)
        else:
            sys.stderr.write(f"[aggregate] 找不到 fingerprint: {args.fingerprint}\n")
            return 1
        return 0

    # 显示 top N
    print_top_fingerprints(aggregated, args.top)

    # 总体统计
    total_entries = sum(len(v) for v in aggregated.values())
    total_occurrences = sum(
        sum(e.get("occurrence_count", 1) for e in entries)
        for entries in aggregated.values()
    )
    all_repos = set()
    for entries in aggregated.values():
        for e in entries:
            all_repos.add(e.get("_source_repo", "?"))

    print(f"\n{'='*60}")
    print(f"总体统计")
    print(f"{'='*60}")
    print(f"唯一 fingerprint: {len(aggregated)}")
    print(f"总记录数: {total_entries}")
    print(f"累计命中: {total_occurrences}")
    print(f"涉及仓库: {', '.join(sorted(all_repos))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
