#!/usr/bin/env python3  # risk:untested reason:"Schema 校验 CLI 工具，无直接业务逻辑路径" owner:@kevinwanghd reviewed:2026-08-31
"""
pending_lessons_schema.py — Pending Lessons Schema 校验器

验证 .governance/pending-lessons/ 目录下的 JSON 文件是否符合 schema 规范。
此校验器与 validate_lessons.py 完全独立，不扫描 lessons/ 目录。

用法:
    python scripts/pending_lessons_schema.py
    python scripts/pending_lessons_schema.py --path .governance/pending-lessons
    python scripts/pending_lessons_schema.py --strict  # 严格模式：缺字段/非法状态/重复 id 均失败
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

VALID_STATUSES = {"pending", "confirmed", "rejected", "promoted"}
VALID_CLASSIFICATIONS = {"code-pattern", "process-lesson"}
VALID_ENFORCEMENTS = {"hard", "soft"}
REQUIRED_FIELDS = {
    "id", "pattern_type", "source_repo", "source_ref", "detected_at",
    "failure_context", "evidence", "regression", "status", "fingerprint",
    "discovered_by"
}
OPTIONAL_FIELDS = {"review"}


def _error(msg: str, errors: list[str]) -> None:
    errors.append(msg)


def validate_pending_file(path: Path, errors: list[str]) -> bool:
    """验证单个 pending JSON 文件。返回 True 表示通过，False 表示有错误。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        _error(f"{path}: JSON 解析失败 - {e}", errors)
        return False
    except OSError as e:
        _error(f"{path}: 文件读取失败 - {e}", errors)
        return False

    if not isinstance(data, dict):
        _error(f"{path}: 根节点必须是 object", errors)
        return False

    # 检查必填字段
    for field in REQUIRED_FIELDS:
        if field not in data:
            _error(f"{path}: 缺少必填字段 '{field}'", errors)

    # 检查非法额外字段（警告，非阻断）
    all_known = REQUIRED_FIELDS | OPTIONAL_FIELDS | {
        "failure_context", "evidence", "review",
        # failure_context 子字段
        "file", "line", "code_snippet", "language", "pattern_description",
        # evidence 子字段
        "diff_base", "scan_command", "scan_output_hash", "raw_violation",
        # review 子字段
        "reviewer", "reviewed_at", "decision", "reason", "classification",
        "target_path", "suggested_regex", "enforcement",
    }
    unknown = set(data.keys()) - all_known
    if unknown:
        print(f"[warn] {path}: 未知字段 {unknown}，将被忽略")

    # 校验 status 枚举
    status = data.get("status")
    if status and status not in VALID_STATUSES:
        _error(f"{path}: status '{status}' 非法，必须是 {VALID_STATUSES}", errors)

    # 校验 discovered_by
    discovered_by = data.get("discovered_by")
    if discovered_by and discovered_by not in ("agent", "human"):
        _error(f"{path}: discovered_by '{discovered_by}' 非法，必须是 'agent' 或 'human'", errors)

    # 校验 failure_context
    fc = data.get("failure_context")
    if fc:
        if not isinstance(fc, dict):
            _error(f"{path}: failure_context 必须是 object", errors)
        else:
            fc_required = {"file", "line", "code_snippet", "language", "pattern_description"}
            for field in fc_required:
                if field not in fc:
                    _error(f"{path}: failure_context 缺少字段 '{field}'", errors)
            if "line" in fc and not isinstance(fc["line"], int):
                _error(f"{path}: failure_context.line 必须是整数", errors)

    # 校验 evidence
    ev = data.get("evidence")
    if ev:
        if not isinstance(ev, dict):
            _error(f"{path}: evidence 必须是 object", errors)

    # 校验 review（当 status 非 pending 时必填）
    review = data.get("review")
    if status in ("confirmed", "rejected", "promoted"):
        if not review:
            _error(f"{path}: status='{status}' 时必须有 review 对象", errors)
        elif not isinstance(review, dict):
            _error(f"{path}: review 必须是 object", errors)
        else:
            # 校验 reviewer 和 reviewed_at
            if "reviewer" not in review:
                _error(f"{path}: review 缺少 'reviewer' 字段", errors)
            if "reviewed_at" not in review:
                _error(f"{path}: review 缺少 'reviewed_at' 字段", errors)
            if "decision" not in review:
                _error(f"{path}: review 缺少 'decision' 字段", errors)
            # 校验 rejected 必须有 reason
            if status == "rejected":
                if "reason" not in review:
                    _error(f"{path}: rejected 状态必须有 review.reason", errors)
            # 校验 confirmed 必须有 classification
            if status == "confirmed":
                if "classification" not in review:
                    _error(f"{path}: confirmed 状态必须有 review.classification", errors)
                elif review.get("classification") not in VALID_CLASSIFICATIONS:
                    _error(
                        f"{path}: review.classification '{review.get('classification')}' "
                        f"非法，必须是 {VALID_CLASSIFICATIONS}", errors
                    )
                # confirmed 必须有 enforcement
                if "enforcement" not in review:
                    _error(f"{path}: confirmed 状态必须有 review.enforcement", errors)
                elif review.get("enforcement") not in VALID_ENFORCEMENTS:
                    _error(
                        f"{path}: review.enforcement '{review.get('enforcement')}' "
                        f"非法，必须是 {VALID_ENFORCEMENTS}", errors
                    )

    return len(errors) == 0


def check_duplicate_ids(pending_dir: Path, errors: list[str]) -> None:
    """检查是否有重复的 id（跨文件）。"""
    ids: dict[str, list[Path]] = {}
    for path in pending_dir.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            pid = data.get("id")
            if pid:
                ids.setdefault(pid, []).append(path)
        except Exception:
            pass  # 已在 validate_pending_file 中报告

    for pid, paths in ids.items():
        if len(paths) > 1:
            paths_str = ", ".join(str(p) for p in paths)
            _error(f"重复的 id '{pid}' 出现在: {paths_str}", errors)


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
        help="严格模式：缺字段、非法状态、重复 id 均返回非零退出码"
    )
    args = parser.parse_args(argv)

    pending_dir = Path(args.path).resolve()
    if not pending_dir.exists():
        print(f"[pending-lessons-schema] 目录不存在: {pending_dir}")
        print(f"[pending-lessons-schema] 这是正常的（尚未产生 pending lessons）")
        return 0  # 目录不存在不是错误

    json_files = sorted(pending_dir.glob("*.json"))
    if not json_files:
        print(f"[pending-lessons-schema] 目录为空，无待校验文件")
        return 0

    print(f"[pending-lessons-schema] 校验 {len(json_files)} 个 pending lesson 文件...")

    all_errors: list[str] = []
    pass_count = 0
    fail_count = 0

    for path in json_files:
        errors: list[str] = []
        validate_pending_file(path, errors)
        if errors:
            fail_count += 1
            all_errors.extend(errors)
        else:
            pass_count += 1

    # 检查重复 id
    check_duplicate_ids(pending_dir, all_errors)

    if all_errors:
        print(f"\n[pending-lessons-schema] FAIL — {fail_count}/{len(json_files)} 文件有问题:\n")
        for err in all_errors:
            print(f"  - {err}")
        return 1 if args.strict else 0

    print(f"[pending-lessons-schema] PASS — {pass_count}/{len(json_files)} 文件通过校验")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
