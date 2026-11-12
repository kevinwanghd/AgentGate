#!/usr/bin/env python3  # risk:untested reason:"Schema 校验 CLI 兼容壳，校验逻辑在 validate_pending.py" owner:@kevinwanghd reviewed:2026-09-29
"""
pending_lessons_schema.py — Pending Lessons Schema 校验器 (兼容入口)

pending lessons 统一为 YAML 格式 (见 .governance/pending-lessons/SCHEMA.md),
校验逻辑在 validate_pending.py。本文件保留旧 CLI, 供已安装的 GitLab CI 继续调用。

用法:
    python scripts/pending_lessons_schema.py
    python scripts/pending_lessons_schema.py --path .governance/pending-lessons
    python scripts/pending_lessons_schema.py --strict  # 严格模式：校验失败返回非零退出码
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_pending  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pending Lessons Schema 校验器")
    parser.add_argument(
        "--path",
        default=".governance/pending-lessons",
        help="pending lessons 目录路径（默认: .governance/pending-lessons）"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="严格模式：校验失败返回非零退出码"
    )
    args = parser.parse_args(argv)

    pending_dir = Path(args.path).resolve()
    files = sorted(pending_dir.glob("*.yml")) + sorted(pending_dir.glob("*.yaml")) if pending_dir.is_dir() else []
    if not files:
        print(f"[pending-lessons-schema] 无待校验文件: {pending_dir} (尚未产生 pending lessons 属正常)")
        return 0

    rc = validate_pending.main(["--check-duplicates", *(str(p) for p in files)])
    return rc if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
