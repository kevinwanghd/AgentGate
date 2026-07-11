#!/usr/bin/env python3
"""
collect_ai_usage.py — 自动采集 AI 代码使用情况, 输出 commit trailer

设计目标: 不让人来填 AI-Usage, 而是 AI 开发时自动采证、提交时自动算等级。

工作流:
  1. AI agent 开发时, 每次 Edit/Write 后向 .governance/ai-evidence.jsonl 追加一行证据
     (由各 agent 指令文件要求 agent 自己做, 见 agent-instructions/)。
  2. 提交时本脚本对照本次 diff 真实行数, 算出 AI 改动行 / 总改动行的占比。
  3. 按阈值映射 none/light/medium/heavy, 输出 commit trailer 文本。

证据行格式 (.governance/ai-evidence.jsonl, 每行一个 JSON):
  {"ts":"2026-06-26T10:00:00Z","tool":"claude-code","model":"opus-4",
   "file":"src/Foo.cs","added":80,"removed":3}

补全类工具 (Cursor Tab / Copilot 内联) 无法精确自报行数, 它们可只写一行
标记本次会话用了该工具 (added/removed 省略或为 0), 此时等级降级处理:
  有工具标记但无可信行数 → AI-Usage: used (程度未知), 不伪造比例。

退出码: 始终 0 (采集类脚本不阻断)。

用法:
    # 提交前 (prepare-commit-msg hook 里) 算 trailer, 对照已暂存改动
    python collect_ai_usage.py --staged

    # 对照某次 diff 范围
    python collect_ai_usage.py --diff-base origin/master

    # 仅输出 trailer 文本 (供 hook 追加到 commit message)
    python collect_ai_usage.py --staged --trailer-only

    # 输出汇总 JSON (供 CI / 报表用)
    python collect_ai_usage.py --staged --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

# 证据文件默认位置 (相对仓库根)
EVIDENCE_PATH = ".governance/ai-evidence.jsonl"

# 占比 → 等级 阈值 (AI 改动行 / 本次总改动行)
#   none:   无任何 AI 证据
#   light:  (0, 0.2]   行内补全 / 少量辅助
#   medium: (0.2, 0.6] 部分逻辑由 AI 生成
#   heavy:  (0.6, 1.0] 主体由 AI 生成
THRESHOLDS = [
    (0.6, "heavy"),
    (0.2, "medium"),
    (0.0, "light"),
]

# 统计的文件: 源码 + 脚本/配置/IaC 类 (这些也是 AI 实际产出, 改 CI/脚本/配置的
# 提交同样应计入 AI-Usage, 否则纯工具/CI 类 PR 盖不上 trailer 会被 mr-validate 拦)。
SCAN_EXTENSIONS = {
    # 源码
    ".cs", ".js", ".ts", ".jsx", ".tsx", ".java", ".go",
    ".py", ".rb", ".php", ".cpp", ".cc", ".c", ".h", ".hpp",
    ".kt", ".rs", ".scala", ".swift",
    # 脚本 / 配置 / IaC
    ".sh", ".bash", ".ps1",
    ".yml", ".yaml", ".toml", ".json", ".xml",
    ".tf", ".dockerfile",
}


# ============================================================
# git 交互
# ============================================================
def run_git(args: list[str]) -> str:
    try:
        return subprocess.run(
            ["git", *args], check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stdout
    except FileNotFoundError:
        sys.stderr.write("[ai-usage] 找不到 git\n")
        sys.exit(0)
    except subprocess.CalledProcessError:
        return ""


def diff_numstat(diff_base: str | None, staged: bool) -> dict[str, int]:
    """返回 {文件: 改动行数(add+del)}, 只含源码文件。"""
    if staged:
        out = run_git(["diff", "--cached", "--numstat"])
    elif diff_base:
        out = run_git(["diff", "--numstat", f"{diff_base}...HEAD"])
    else:
        out = run_git(["diff", "--numstat", "HEAD~1...HEAD"])

    result: dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        add, dele, path = parts
        if os.path.splitext(path)[1].lower() not in SCAN_EXTENSIONS:
            continue
        try:
            result[path] = int(add) + int(dele)
        except ValueError:
            pass  # 二进制文件 numstat 为 "-"
    return result


# ============================================================
# 证据采集
# ============================================================
def load_evidence(path: str) -> list[dict]:
    """读取 jsonl 证据, 跳过坏行。"""
    if not os.path.isfile(path):
        return []
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def aggregate(evidence: list[dict], changed: dict[str, int]) -> dict:
    """
    汇总证据, 与本次实际 diff 对照。
    只采信本次 diff 里确实改动了的文件的证据 (防止陈旧证据污染)。
    返回汇总 dict。
    """
    tools: set[str] = set()
    models: set[str] = set()
    ai_lines_by_file: dict[str, int] = {}
    has_imprecise_tool = False  # 补全类工具: 有标记但无可信行数

    for rec in evidence:
        f = rec.get("file")
        tool = rec.get("tool")
        model = rec.get("model")
        if tool:
            tools.add(str(tool))
        if model:
            models.add(str(model))

        added = rec.get("added")
        removed = rec.get("removed")
        # 无行数信息 = 补全类工具自报, 记标记但不计入比例
        if added is None and removed is None:
            has_imprecise_tool = True
            continue

        # 只采信本次确实改动的源码文件
        if not f or f not in changed:
            continue
        try:
            n = int(added or 0) + int(removed or 0)
        except (ValueError, TypeError):
            continue
        # 同一文件多次编辑累加, 但封顶到该文件本次实际改动行数 (防高估)
        ai_lines_by_file[f] = min(
            ai_lines_by_file.get(f, 0) + n, changed[f]
        )

    total_changed = sum(changed.values())
    total_ai = sum(ai_lines_by_file.values())
    ratio = (total_ai / total_changed) if total_changed else 0.0

    return {
        "tools": sorted(tools),
        "models": sorted(models),
        "ai_lines": total_ai,
        "total_lines": total_changed,
        "ratio": round(ratio, 3),
        "has_imprecise_tool": has_imprecise_tool,
        "ai_files": ai_lines_by_file,
    }


def classify(agg: dict) -> str:
    """
    映射等级。
      - 总改动为 0          → none
      - 无证据 + 无工具标记 → none
      - 有精确行数 → 按比例
      - 仅补全类标记(无行数)→ used (程度未知)
    """
    if agg["total_lines"] == 0:
        return "none"
    if agg["ai_lines"] == 0:
        # 无精确行数, 但有补全类工具标记
        if agg["has_imprecise_tool"]:
            return "used"
        return "none"
    ratio = agg["ratio"]
    for thresh, level in THRESHOLDS:
        if ratio > thresh:
            return level
    return "light"


# ============================================================
# 输出
# ============================================================
def build_trailer(agg: dict, level: str) -> str:
    lines = [f"AI-Usage: {level}"]
    if agg["tools"]:
        lines.append(f"AI-Tools: {', '.join(agg['tools'])}")
    if agg["models"]:
        lines.append(f"AI-Models: {', '.join(agg['models'])}")
    if agg["total_lines"]:
        lines.append(f"AI-Lines: {agg['ai_lines']}/{agg['total_lines']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="自动采集 AI 代码使用情况并算 trailer")
    ap.add_argument("--staged", action="store_true", help="对照已暂存改动")
    ap.add_argument("--diff-base", help="对照 diff 基准, 如 origin/master")
    ap.add_argument("--evidence", default=EVIDENCE_PATH, help="证据文件路径")
    ap.add_argument("--trailer-only", action="store_true", help="仅输出 trailer 文本")
    ap.add_argument("--json", action="store_true", help="输出汇总 JSON")
    args = ap.parse_args()

    changed = diff_numstat(args.diff_base, args.staged)
    evidence = load_evidence(args.evidence)
    agg = aggregate(evidence, changed)
    level = classify(agg)
    trailer = build_trailer(agg, level)

    if args.json:
        print(json.dumps({**agg, "level": level}, ensure_ascii=False, indent=2))
        return 0

    if args.trailer_only:
        print(trailer)
        return 0

    # 默认: 人类可读摘要 + trailer
    print("[ai-usage] 自动采集结果")
    print(f"  AI 改动行 / 总改动行 = {agg['ai_lines']} / {agg['total_lines']}"
          f" (占比 {agg['ratio']:.0%})")
    print(f"  工具: {', '.join(agg['tools']) or '(无证据)'}")
    print(f"  判定等级: {level}")
    if not evidence:
        print("  注意: 未找到证据文件, 等级按无 AI 证据处理。"
              "AI agent 应在开发时写入 .governance/ai-evidence.jsonl。")
    print()
    print("  建议 commit trailer:")
    for ln in trailer.splitlines():
        print(f"    {ln}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
