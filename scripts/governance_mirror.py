#!/usr/bin/env python3  # risk:untested reason:"CLI 同步工具，无直接业务逻辑路径" owner:@kevinwanghd reviewed:2026-08-31
"""
governance_mirror.py — 扫描结果写入 pending lessons

当 scan_risks.py 发现 blocking findings 时，将候选规则写入 .governance/pending-lessons/。

触发点：
- scan_risks.py 发现 block 级别风险后，由调用方主动调用 record_pending_from_scan()
- 扫描器本身只负责发现，不负责"修复完成"生命周期（按审核意见 §6.1）

幂等性：
- 同一 fingerprint + source_repo 只产生一条 pending
- 重复扫描不产生重复候选

用法:
    from governance_mirror import record_pending_from_scan
    record_pending_from_scan(violation, source_repo="deliverhq", source_ref="abc123")

    python scripts/governance_mirror.py --from-scan-output violations.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

# ---------- Fingerprint 计算 ----------

def compute_fingerprint(pattern_type: str, code_snippet: str, version: int = 1) -> str:
    """
    计算跨仓库聚合 fingerprint。

    fingerprint = SHA256(pattern_type + ":" + normalized_code_shape)
    不包含仓库名、CR id 或临时路径。

    version: normalization 版本，升级时避免旧数据重新聚合
    """
    # 标准化代码片段：去除具体值、路径、注释
    normalized = _normalize_for_fingerprint(code_snippet)
    raw = f"{version}:{pattern_type}:{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _normalize_for_fingerprint(code: str) -> str:
    """
    标准化代码片段，用于 fingerprint 计算。
    去除具体值、路径、换行，保留代码结构。
    """
    import re
    # 去除注释
    normalized = re.sub(r"//.*", "", code)
    normalized = re.sub(r"#.*", "", normalized)
    normalized = re.sub(r"/\*.*?\*/", "", normalized, flags=re.DOTALL)
    # 去除字符串字面量（保留引号标记）
    normalized = re.sub(r'"[^"]*"', '""', normalized)
    normalized = re.sub(r"'[^']*'", "''", normalized)
    # 去除数字字面量（保留数字标记）
    normalized = re.sub(r"\b\d+\b", "0", normalized)
    # 去除换行和多余空白
    normalized = re.sub(r"\s+", " ", normalized).strip()
    # 转小写（结构不区分大小写）
    return normalized.lower()


def _get_language_from_path(file_path: str) -> str:
    """从文件路径推断编程语言。"""
    ext = os.path.splitext(file_path)[1].lower()
    lang_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascript",
        ".tsx": "typescript",
        ".java": "java",
        ".cs": "csharp",
        ".go": "go",
        ".rb": "ruby",
        ".php": "php",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c",
        ".hpp": "cpp",
        ".kt": "kotlin",
        ".rs": "rust",
        ".scala": "scala",
        ".swift": "swift",
        ".dart": "dart",
        ".sh": "bash",
    }
    return lang_map.get(ext, "unknown")


# ---------- Pending 记录 ----------

def _pending_dir() -> Path:
    """获取 pending lessons 目录路径。"""
    return Path(__file__).parent.parent / ".governance" / "pending-lessons"


def _pending_file_path(fingerprint: str, source_repo: str) -> Path | None:
    """查找同一 fingerprint + source_repo 的已存在 pending 文件。"""
    d = _pending_dir()
    if not d.exists():
        return None
    # 查找 fingerprint 匹配的文件
    for path in d.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if (data.get("fingerprint") == fingerprint and
                    data.get("source_repo") == source_repo and
                    data.get("status") == "pending"):
                return path
        except Exception:
            pass
    return None


def record_pending_from_scan(
    violation: dict,
    source_repo: str,
    source_ref: str,
    diff_base: str = "",
    discovered_by: str = "agent",
) -> tuple[Path, bool]:
    """
    将 scan_risks.py 的违规记录写入 pending lessons。

    参数:
        violation: scan_risks.py 的违规对象，包含 file/line/type/problems/mode
        source_repo: 来源仓库名
        source_ref: 触发扫描的 ref（如 commit SHA、MR ID）
        diff_base: 扫描基准
        discovered_by: "agent" | "human"

    返回:
        (pending_file_path, is_new): 文件路径和是否为新建

    幂等性：同一 fingerprint + source_repo 只会有一条 pending
    """
    d = _pending_dir()
    d.mkdir(parents=True, exist_ok=True)

    # 提取风险信息
    pattern_type = (violation.get("type") or "").split("/")[0]  # 取第一个类型
    file_path = violation.get("file", "")
    line_no = violation.get("line", 0)
    code_snippet = violation.get("code_snippet", "")
    desc = violation.get("desc", "")
    raw_violation = violation  # 保留原始违规对象

    # 计算 fingerprint
    fp = compute_fingerprint(pattern_type, code_snippet)

    # 检查是否已存在（幂等）
    existing = _pending_file_path(fp, source_repo)
    if existing:
        # 存在则合并计数（追加 evidence）
        try:
            with open(existing, encoding="utf-8") as f:
                data = json.load(f)
            # 追加新的 evidence 记录
            if "evidence_history" not in data:
                data["evidence_history"] = []
            evidence_entry = {
                "source_ref": source_ref,
                "detected_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
                "file": file_path,
                "line": line_no,
                "diff_base": diff_base,
            }
            data["evidence_history"].append(evidence_entry)
            data["occurrence_count"] = data.get("occurrence_count", 1) + 1
            data["last_seen"] = evidence_entry["detected_at"]
            with open(existing, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return (existing, False)
        except Exception as e:
            sys.stderr.write(f"[governance-mirror] 合并 pending 失败: {e}\n")

    # 生成新 pending 文件
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    pending_id = f"{fp}_{timestamp}"
    detected_at = now.isoformat()

    # 构造 failure_context
    failure_context = {
        "file": file_path,
        "line": line_no,
        "code_snippet": code_snippet,
        "language": _get_language_from_path(file_path),
        "pattern_description": desc,
    }

    # 构造 evidence
    evidence = {
        "diff_base": diff_base,
        "scan_command": "scan_risks.py",
        "scan_output_hash": hashlib.sha256(
            json.dumps(violation, sort_keys=True).encode()
        ).hexdigest()[:16],
        "raw_violation": {
            "file": violation.get("file"),
            "line": violation.get("line"),
            "type": violation.get("type"),
            "desc": violation.get("desc"),
            "problems": violation.get("problems"),
            "mode": violation.get("mode"),
        },
    }

    # 构造 regression 建议
    regression = (
        f"新增代码不得包含 {pattern_type} 风险模式；"
        f"如需豁免，请在风险代码上方添加符合规范的 risk: 注解"
    )

    pending_data = {
        "id": pending_id,
        "pattern_type": pattern_type,
        "source_repo": source_repo,
        "source_ref": source_ref,
        "detected_at": detected_at,
        "failure_context": failure_context,
        "evidence": evidence,
        "regression": regression,
        "status": "pending",
        "fingerprint": fp,
        "discovered_by": discovered_by,
        "occurrence_count": 1,
        "last_seen": detected_at,
    }

    # 写入文件
    filename = f"{now.strftime('%Y%m%d')}_{fp}.json"
    filepath = d / filename

    # 处理文件名冲突
    if filepath.exists():
        filename = f"{now.strftime('%Y%m%d')}_{fp}_{timestamp}.json"
        filepath = d / filename

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(pending_data, f, ensure_ascii=False, indent=2)
        return (filepath, True)
    except OSError as e:
        sys.stderr.write(f"[governance-mirror] 写入 pending 失败: {e}\n")
        raise


# ---------- CLI 入口 ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="将 scan_risks.py 输出写入 pending lessons"
    )
    parser.add_argument(
        "--from-scan-output",
        type=str,
        help="scan_risks.py 的 JSON 输出文件路径"
    )
    parser.add_argument(
        "--source-repo",
        default="agentgate",
        help="来源仓库名（默认: agentgate）"
    )
    parser.add_argument(
        "--source-ref",
        default="",
        help="触发扫描的 ref（如 commit SHA）"
    )
    parser.add_argument(
        "--diff-base",
        default="",
        help="扫描基准"
    )
    args = parser.parse_args(argv)

    if not args.from_scan_output:
        parser.print_help()
        return 1

    try:
        with open(args.from_scan_output, encoding="utf-8") as f:
            violations = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        sys.stderr.write(f"[governance-mirror] 读取扫描输出失败: {e}\n")
        return 1

    if not isinstance(violations, list):
        violations = [violations]

    if not violations:
        print("[governance-mirror] 无违规记录")
        return 0

    print(f"[governance-mirror] 处理 {len(violations)} 条违规记录...")

    new_count = 0
    existing_count = 0
    for v in violations:
        try:
            path, is_new = record_pending_from_scan(
                v,
                source_repo=args.source_repo,
                source_ref=args.source_ref,
                diff_base=args.diff_base,
            )
            if is_new:
                new_count += 1
                print(f"  [new] {path.name}")
            else:
                existing_count += 1
                print(f"  [skip] {path.name} (已存在)")
        except Exception as e:
            sys.stderr.write(f"  [error] {v.get('file', '?')}:{v.get('line', '?')} - {e}\n")

    print(f"\n[governance-mirror] 完成: {new_count} 新建, {existing_count} 已存在")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
