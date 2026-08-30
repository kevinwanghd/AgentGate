#!/usr/bin/env python3
"""
lessons_review.py — Pending Lessons Review 工具

管理 pending lessons 的审核流程：列出、审核、确认、拒绝、应用。

用法:
    python scripts/lessons_review.py list
    python scripts/lessons_review.py review <fingerprint>
    python scripts/lessons_review.py confirm <fingerprint> --classification <type> [--target <path>]
    python scripts/lessons_review.py reject <fingerprint> --reason "<reason>"
    python scripts/lessons_review.py apply [--dry-run]
    python scripts/lessons_review.py stats

状态流转:
    pending → confirmed → promoted
                ↓
              rejected
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Optional

PENDING_DIR = Path(__file__).parent.parent / ".governance" / "pending-lessons"
STATUSES = {"pending", "confirmed", "rejected", "promoted"}
CLASSIFICATIONS = {"code-pattern", "process-lesson"}
ENFORCEMENTS = {"hard", "soft"}


def _load_pending(fingerprint_or_id: str) -> tuple[Optional[dict], Optional[Path]]:
    """按 fingerprint 或 id 查找 pending 文件。"""
    if not PENDING_DIR.exists():
        return None, None

    # 精确匹配 id
    for path in PENDING_DIR.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("id", "").endswith(fingerprint_or_id):
                return data, path
        except Exception:
            pass

    # 模糊匹配 fingerprint
    for path in PENDING_DIR.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if fingerprint_or_id in data.get("fingerprint", ""):
                return data, path
        except Exception:
            pass

    return None, None


def _save_pending(data: dict, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def cmd_list(args: argparse.Namespace) -> int:
    """列出所有 pending lessons。"""
    if not PENDING_DIR.exists():
        print("[lessons-review] pending lessons 目录不存在")
        return 0

    files = sorted(PENDING_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        print("[lessons-review] 暂无 pending lessons")
        return 0

    # 统计
    stats = {"pending": 0, "confirmed": 0, "rejected": 0, "promoted": 0}
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            stats[data.get("status", "pending")] += 1
        except Exception:
            stats["pending"] += 1

    print(f"=== Pending Lessons ({len(files)} 条) ===")
    print(f"  pending: {stats['pending']} | confirmed: {stats['confirmed']} | "
          f"rejected: {stats['rejected']} | promoted: {stats['promoted']}\n")

    for i, path in enumerate(files, 1):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            print(f"[{i}] {path.name} - 读取失败")
            continue

        fp = data.get("fingerprint", "?")[:8]
        status = data.get("status", "pending")
        pattern = data.get("pattern_type", "?")
        file_path = data.get("failure_context", {}).get("file", "?")
        line = data.get("failure_context", {}).get("line", "?")
        repo = data.get("source_repo", "?")
        detected = data.get("detected_at", "?")[:10]
        occ = data.get("occurrence_count", 1)

        status_icon = {"pending": "⏳", "confirmed": "✅", "rejected": "❌", "promoted": "🚀"}.get(status, "?")
        print(f"[{i}] {status_icon} {fp} | {pattern}")
        print(f"    文件: {file_path}:{line}")
        print(f"    来源: {repo} ({detected})")
        print(f"    命中: {occ} 次")
        if status != "pending":
            review = data.get("review", {})
            reviewer = review.get("reviewer", "?")
            decision = review.get("decision", "?")
            print(f"    审核: {reviewer} → {decision}")
            if status == "rejected":
                print(f"    原因: {review.get('reason', '')}")
        print()

    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """交互式审核单个 pending lesson。"""
    data, path = _load_pending(args.fingerprint)
    if data is None:
        sys.stderr.write(f"[lessons-review] 找不到 fingerprint/id: {args.fingerprint}\n")
        return 1

    print(f"\n=== Pending Lesson Review ===")
    print(f"ID: {data.get('id')}")
    print(f"Fingerprint: {data.get('fingerprint')}")
    print(f"状态: {data.get('status')}")
    print(f"风险类型: {data.get('pattern_type')}")
    print(f"来源: {data.get('source_repo')} @ {data.get('source_ref')}")
    print(f"发现时间: {data.get('detected_at')}")
    print(f"发现次数: {data.get('occurrence_count', 1)}")
    print()
    print(f"--- 失败上下文 ---")
    fc = data.get("failure_context", {})
    print(f"文件: {fc.get('file')} (line {fc.get('line')})")
    print(f"语言: {fc.get('language')}")
    print(f"代码片段:")
    for line in (fc.get("code_snippet", "") or "").splitlines():
        print(f"  {line}")
    print(f"模式描述: {fc.get('pattern_description')}")
    print()
    print(f"--- 回归建议 ---")
    print(f"  {data.get('regression')}")
    print()

    if data.get("status") != "pending":
        print("--- 审核记录 ---")
        review = data.get("review", {})
        print(f"审核人: {review.get('reviewer')}")
        print(f"审核时间: {review.get('reviewed_at')}")
        print(f"决策: {review.get('decision')}")
        if review.get("reason"):
            print(f"原因: {review.get('reason')}")
        if review.get("classification"):
            print(f"分类: {review.get('classification')}")
        if review.get("target_path"):
            print(f"目标路径: {review.get('target_path')}")
        print()
        return 0

    print("--- 操作 ---")
    print("  [c]onfirm  确认有效")
    print("  [r]eject   拒绝（误报）")
    print("  [q]uit     退出")
    choice = input("选择操作: ").strip().lower()

    if choice == "q":
        return 0
    elif choice == "c":
        print("\n分类:")
        print("  [1] code-pattern (可正则化的代码风险)")
        print("  [2] process-lesson (流程教训)")
        cls_choice = input("选择分类 [1/2]: ").strip()
        classification = "code-pattern" if cls_choice == "1" else "process-lesson"

        print("\nenforcement:")
        print("  [1] soft (推荐，自动生成内容默认 soft)")
        print("  [2] hard (需人工编写可执行检查)")
        enf_choice = input("选择 enforcement [1/2]: ").strip()
        enforcement = "soft" if enf_choice == "1" else "hard"

        reviewer = input("审核人: ").strip() or "anonymous"
        target_path = ""
        suggested_regex = ""

        if classification == "code-pattern":
            target_path = input("目标路径 (如 patterns/python.yml): ").strip() or f"patterns/{fc.get('language', 'unknown')}.yml"
            suggested_regex = input("建议正则 (可选): ").strip()
        else:
            target_path = input("目标路径 (如 lessons/process.yml): ").strip() or "lessons/process.yml"

        # 更新 pending
        data["status"] = "confirmed"
        data["review"] = {
            "reviewer": reviewer,
            "reviewed_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
            "decision": "confirmed",
            "classification": classification,
            "target_path": target_path,
            "enforcement": enforcement,
            "suggested_regex": suggested_regex,
        }
        _save_pending(data, path)
        print(f"\n[lessons-review] 已确认: {path.name}")
        print(f"  → 分类: {classification}")
        print(f"  → 目标: {target_path}")
        print(f"  → enforcement: {enforcement}")
        return 0

    elif choice == "r":
        reason = input("拒绝原因: ").strip()
        if not reason:
            sys.stderr.write("[lessons-review] 拒绝必须提供原因\n")
            return 1
        reviewer = input("审核人: ").strip() or "anonymous"

        data["status"] = "rejected"
        data["review"] = {
            "reviewer": reviewer,
            "reviewed_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
            "decision": "rejected",
            "reason": reason,
        }
        _save_pending(data, path)
        print(f"\n[lessons-review] 已拒绝: {path.name}")
        print(f"  原因: {reason}")
        return 0

    else:
        sys.stderr.write("[lessons-review] 无效选择\n")
        return 1


def cmd_confirm(args: argparse.Namespace) -> int:
    """非交互式确认 pending lesson。"""
    data, path = _load_pending(args.fingerprint)
    if data is None:
        sys.stderr.write(f"[lessons-review] 找不到 fingerprint/id: {args.fingerprint}\n")
        return 1

    if data.get("status") != "pending":
        sys.stderr.write(f"[lessons-review] 当前状态为 {data.get('status')}，无法确认\n")
        return 1

    classification = args.classification
    if classification not in CLASSIFICATIONS:
        sys.stderr.write(f"[lessons-review] classification 必须是 {CLASSIFICATIONS} 之一\n")
        return 1

    enforcement = getattr(args, "enforcement", "soft")
    if enforcement not in ENFORCEMENTS:
        enforcement = "soft"

    reviewer = getattr(args, "reviewer", None) or "cli"
    target_path = args.target or ""

    fc = data.get("failure_context", {})
    if not target_path:
        if classification == "code-pattern":
            target_path = f"patterns/{fc.get('language', 'unknown')}.yml"
        else:
            target_path = "lessons/process.yml"

    data["status"] = "confirmed"
    data["review"] = {
        "reviewer": reviewer,
        "reviewed_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
        "decision": "confirmed",
        "classification": classification,
        "target_path": target_path,
        "enforcement": enforcement,
        "suggested_regex": getattr(args, "suggested_regex", "") or "",
    }
    _save_pending(data, path)
    print(f"[lessons-review] 已确认: {path.name}")
    print(f"  → 分类: {classification}")
    print(f"  → 目标: {target_path}")
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    """非交互式拒绝 pending lesson。"""
    data, path = _load_pending(args.fingerprint)
    if data is None:
        sys.stderr.write(f"[lessons-review] 找不到 fingerprint/id: {args.fingerprint}\n")
        return 1

    if data.get("status") != "pending":
        sys.stderr.write(f"[lessons-review] 当前状态为 {data.get('status')}，无法拒绝\n")
        return 1

    if not args.reason:
        sys.stderr.write("[lessons-review] 拒绝必须提供 --reason\n")
        return 1

    reviewer = getattr(args, "reviewer", None) or "cli"
    data["status"] = "rejected"
    data["review"] = {
        "reviewer": reviewer,
        "reviewed_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
        "decision": "rejected",
        "reason": args.reason,
    }
    _save_pending(data, path)
    print(f"[lessons-review] 已拒绝: {path.name}")
    print(f"  原因: {args.reason}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    """将 confirmed lessons 应用到 patterns/ 或 lessons/。"""
    if not PENDING_DIR.exists():
        print("[lessons-review] pending lessons 目录不存在")
        return 0

    files = list(PENDING_DIR.glob("*.json"))
    confirmed = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("status") == "confirmed":
                confirmed.append((data, path))
        except Exception:
            pass

    if not confirmed:
        print("[lessons-review] 暂无 confirmed lessons 需要应用")
        return 0

    print(f"[lessons-review] 发现 {len(confirmed)} 条 confirmed lessons待应用\n")

    for data, src_path in confirmed:
        review = data.get("review", {})
        classification = review.get("classification")
        target_path_str = review.get("target_path", "")
        enforcement = review.get("enforcement", "soft")

        fc = data.get("failure_context", {})
        pattern_type = data.get("pattern_type", "")
        desc = fc.get("pattern_description", "")
        code_snippet = fc.get("code_snippet", "")
        language = fc.get("language", "unknown")

        if classification == "code-pattern":
            # 写入 patterns/
            target = Path(__file__).parent.parent / target_path_str
            print(f"  → 应用 code-pattern: {target}")
            if args.dry_run:
                print(f"      [dry-run] 跳过实际写入")
                continue

            # 追加到 patterns yaml 文件
            _apply_code_pattern(target, pattern_type, desc, code_snippet, language, enforcement, review, data)

        else:
            # 写入 lessons/
            target = Path(__file__).parent.parent / target_path_str
            print(f"  → 应用 process-lesson: {target}")
            if args.dry_run:
                print(f"      [dry-run] 跳过实际写入")
                continue

            _apply_process_lesson(target, pattern_type, desc, enforcement, review, data)

        # 更新状态为 promoted
        data["status"] = "promoted"
        data["review"]["applied_at"] = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()
        _save_pending(data, src_path)
        print(f"      ✓ 已标记为 promoted: {src_path.name}")

    print(f"\n[lessons-review] 完成")
    return 0


def _apply_code_pattern(
    target: Path,
    pattern_type: str,
    desc: str,
    code_snippet: str,
    language: str,
    enforcement: str,
    review: dict,
    data: dict,
) -> None:
    """将 code-pattern 应用到 patterns/ yaml 文件。"""
    try:
        import yaml
    except ImportError:
        sys.stderr.write("[lessons-review] 需要 pyyaml 库来写入 patterns\n")
        return

    target.parent.mkdir(parents=True, exist_ok=True)

    # 读取现有 patterns
    existing = []
    if target.exists():
        try:
            with open(target, encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
        except Exception:
            existing = {}

    patterns = existing.get("patterns", [])
    if not isinstance(patterns, list):
        patterns = []
        existing["patterns"] = patterns

    # 构建新 pattern
    fp = data.get("fingerprint", "")[:8]
    new_pattern = {
        "type": pattern_type,
        "desc": desc,
        "regex": review.get("suggested_regex") or f"# TODO: 请补充 regex for {pattern_type}",
        "mode": "block" if enforcement == "hard" else "warn",
        "source": f"pending-lessons/{data.get('id')}",
        "language": language,
    }

    patterns.append(new_pattern)
    existing["patterns"] = patterns

    with open(target, "w", encoding="utf-8") as f:
        yaml.dump(existing, f, allow_unicode=True, sort_keys=False)

    print(f"      ✓ 已追加到 {target}")


def _apply_process_lesson(
    target: Path,
    pattern_type: str,
    desc: str,
    enforcement: str,
    review: dict,
    data: dict,
) -> None:
    """将 process-lesson 应用到 lessons/ yaml 文件。"""
    try:
        import yaml
    except ImportError:
        sys.stderr.write("[lessons-review] 需要 pyyaml 库来写入 lessons\n")
        return

    target.parent.mkdir(parents=True, exist_ok=True)

    # 读取现有 lessons
    existing = {}
    if target.exists():
        try:
            with open(target, encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
        except Exception:
            existing = {}

    if not existing:
        existing = {
            "version": "agentgate.io/lessons/v1",
            "scope": "agent-instructions",
            "lessons": [],
        }

    lessons = existing.get("lessons", [])
    if not isinstance(lessons, list):
        lessons = []
        existing["lessons"] = lessons

    # 构建新 lesson（soft enforcement）
    fp = data.get("fingerprint", "")[:8]
    fc = data.get("failure_context", {})
    new_lesson = {
        "id": f"governance.{pattern_type.replace('-', '_')}_{fp}",
        "enforcement": enforcement,
        "applies_to": [f"**/{Path(fc.get('file', '')).name}"],
        "trigger": desc,
        "risk": f"{pattern_type}: {desc}",
        "fix": data.get("regression", ""),
        "regression": data.get("regression", ""),
        "source": f"pending-lessons/{data.get('id')}",
    }

    lessons.append(new_lesson)
    existing["lessons"] = lessons

    with open(target, "w", encoding="utf-8") as f:
        yaml.dump(existing, f, allow_unicode=True, sort_keys=False)

    print(f"      ✓ 已追加到 {target}")


def cmd_stats(args: argparse.Namespace) -> int:
    """显示 pending lessons 统计信息。"""
    if not PENDING_DIR.exists():
        print("[lessons-review] pending lessons 目录不存在")
        return 0

    files = list(PENDING_DIR.glob("*.json"))
    if not files:
        print("[lessons-review] 暂无 pending lessons")
        return 0

    stats = {
        "total": len(files),
        "pending": 0, "confirmed": 0, "rejected": 0, "promoted": 0,
        "by_repo": {},
        "by_type": {},
        "total_occurrences": 0,
    }

    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        status = data.get("status", "pending")
        repo = data.get("source_repo", "unknown")
        ptype = data.get("pattern_type", "unknown")
        occ = data.get("occurrence_count", 1)

        stats[status] += 1
        stats["total_occurrences"] += occ
        stats["by_repo"][repo] = stats["by_repo"].get(repo, 0) + 1
        stats["by_type"][ptype] = stats["by_type"].get(ptype, 0) + 1

    print("=== Pending Lessons 统计 ===")
    print(f"总数: {stats['total']}")
    print(f"累计命中: {stats['total_occurrences']}")
    print()
    print(f"状态分布:")
    print(f"  pending:   {stats['pending']:3d}")
    print(f"  confirmed: {stats['confirmed']:3d}")
    print(f"  rejected:  {stats['rejected']:3d}")
    print(f"  promoted:  {stats['promoted']:3d}")
    print()

    # 废弃率（rejected / (confirmed + rejected)）
    confirmed = stats["confirmed"] + stats["promoted"]
    rejected = stats["rejected"]
    total_decided = confirmed + rejected
    if total_decided > 0:
        abandonment_rate = rejected / total_decided
        print(f"废弃率: {abandonment_rate:.1%} ({rejected}/{total_decided})")
    else:
        print(f"废弃率: N/A (暂无审核决策)")

    print()
    print(f"按仓库:")
    for repo, count in sorted(stats["by_repo"].items(), key=lambda x: -x[1]):
        print(f"  {repo}: {count}")
    print()
    print(f"按风险类型:")
    for ptype, count in sorted(stats["by_type"].items(), key=lambda x: -x[1]):
        print(f"  {ptype}: {count}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pending Lessons Review 工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # list
    subparsers.add_parser("list", help="列出所有 pending lessons")

    # review
    review_parser = subparsers.add_parser("review", help="交互式审核 pending lesson")
    review_parser.add_argument("fingerprint", help="fingerprint 或 id")

    # confirm
    confirm_parser = subparsers.add_parser("confirm", help="确认 pending lesson")
    confirm_parser.add_argument("fingerprint", help="fingerprint 或 id")
    confirm_parser.add_argument("--classification", required=True,
                               choices=["code-pattern", "process-lesson"],
                               help="分类")
    confirm_parser.add_argument("--target", help="目标文件路径")
    confirm_parser.add_argument("--enforcement", default="soft",
                               choices=["hard", "soft"],
                               help="enforcement 级别")
    confirm_parser.add_argument("--reviewer", help="审核人")
    confirm_parser.add_argument("--suggested-regex", help="建议的正则表达式")

    # reject
    reject_parser = subparsers.add_parser("reject", help="拒绝 pending lesson")
    reject_parser.add_argument("fingerprint", help="fingerprint 或 id")
    reject_parser.add_argument("--reason", required=True, help="拒绝原因")
    reject_parser.add_argument("--reviewer", help="审核人")

    # apply
    apply_parser = subparsers.add_parser("apply", help="应用 confirmed lessons 到 patterns/ 或 lessons/")
    apply_parser.add_argument("--dry-run", action="store_true", help="仅预览，不实际写入")

    # stats
    subparsers.add_parser("stats", help="显示统计信息")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    commands = {
        "list": cmd_list,
        "review": cmd_review,
        "confirm": cmd_confirm,
        "reject": cmd_reject,
        "apply": cmd_apply,
        "stats": cmd_stats,
    }

    return commands.get(args.command, lambda _: parser.print_help() or 1)(args)


if __name__ == "__main__":
    raise SystemExit(main())
