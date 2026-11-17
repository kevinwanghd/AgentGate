#!/usr/bin/env python3
"""Record a human-dismissed AI-review false positive into the allowlist.

After a maintainer overrides a finding (e.g. an --admin merge), run this so the
same false positive does not block the next PR. The allowlist file lives in the
consumer repo and is itself protected: only a human may add entries, never the
review script or a model.

Usage:
    python scripts/dismiss_finding.py \
        --allowlist ops/review-allowlist.yml \
        --file "lib/review_strategies.py" \
        --keyword "编排" \
        --reason "STRATEGIES 为既有数据驱动策略表，新增条目属扩展非新增编排" \
        --by "@kevinwanghd" --pr 103
"""
from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowlist", required=True)
    parser.add_argument("--file", default="", help="fnmatch pattern for the finding's file")
    parser.add_argument("--keyword", default="", help="substring that must appear in the finding")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--by", required=True, help="who dismissed it")
    parser.add_argument("--pr", type=int, default=None)
    args = parser.parse_args(argv)

    if not args.file and not args.keyword:
        print("至少给 --file 或 --keyword 之一，避免白名单匹配一切", file=sys.stderr)
        return 2
    if yaml is None:
        print("需要 PyYAML", file=sys.stderr)
        return 2

    path = Path(args.allowlist)
    entries = []
    if path.is_file():
        entries = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if not isinstance(entries, list):
            print("白名单根节点必须是列表", file=sys.stderr)
            return 2

    entry = {
        "file": args.file,
        "keyword": args.keyword,
        "reason": args.reason,
        "dismissed_by": args.by,
        "date": datetime.date.today().isoformat(),
    }
    if args.pr is not None:
        entry["pr"] = args.pr
    entries.append(entry)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(entries, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"已记录白名单条目 -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())