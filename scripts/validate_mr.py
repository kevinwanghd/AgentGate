#!/usr/bin/env python3
"""
validate_mr.py — MR 治理规范 v1 描述校验器（软门禁, soft_deadline 后转硬）

校验 MR 描述是否包含必填段落 / 字段:
  - ## 背景
  - ## 变更内容
  - ## 自测确认
  - 大变更时额外要求 ## 风险与回滚

AI-Usage 字段: 不再列为强制字段。采集能力保留 (collect_ai_usage.py + hook),
但未安装 hook 时不阻断合并。如需恢复强制, 在 governance.config.yml 的
metadata.mandatory_fields 中加回 ai_usage 即可。

模式判定:
  读取 governance.config.yml 的 metadata.enforcement 与 soft_deadline。
  - enforcement == "hard"  → 缺字段退出码 1
  - enforcement == "soft"  → 看 soft_deadline:
        今天 > deadline → 视为 hard (自动转硬)
        否则           → 仅警告, 退出码 0

MR 描述来源 (优先级):
  1. --file <path>
  2. 环境变量 CI_MERGE_REQUEST_DESCRIPTION (GitLab CI 自带)
  3. stdin

用法:
    python validate_mr.py
    python validate_mr.py --file mr.md --config governance.config.yml
    echo "$CI_MERGE_REQUEST_DESCRIPTION" | python validate_mr.py

退出码:
    0  通过 (或软模式仅警告)
    1  硬模式下缺必填项
    2  运行错误
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys

from governance_common import ConfigError, load_config as load_shared_config

try:
    import yaml  # type: ignore
    _HAS_YAML = True
except Exception:  # pragma: no cover
    _HAS_YAML = False


DEFAULT_CONFIG = {
    "metadata": {
        "enforcement": "soft",
        "soft_deadline": None,
        "mandatory_fields": ["background", "changes", "self_test"],
    },
    "large_change": {
        "line_threshold": 500,
        "excluded_paths": ["*.lock", "*.Designer.cs", "migrations/**", "**/*.generated.*"],
        "sensitive_paths": ["ci/", "CODEOWNERS", "charts*/", "*secret*", ".gitlab-ci.yml"],
        "schema_paths": ["*.sql", "migrations/**", "*.proto"],
    },
}

# used = 补全类工具(Cursor Tab / Copilot 内联)有标记但无法精确测占比时的等级
AI_USAGE_VALUES = {"none", "light", "medium", "heavy", "used"}


# ============================================================
# 配置
# ============================================================
def load_config(path: str | None) -> dict:
    return load_shared_config(path, DEFAULT_CONFIG, ("metadata", "large_change"))


# ============================================================
# 读取 MR 描述
# ============================================================
def read_description(file_arg: str | None) -> str:
    if file_arg:
        with open(file_arg, "r", encoding="utf-8-sig") as f:
            return f.read()
    env = os.environ.get("CI_MERGE_REQUEST_DESCRIPTION")
    if env:
        return env.lstrip("\ufeff")
    if not sys.stdin.isatty():
        return sys.stdin.read().lstrip("\ufeff")
    return ""


# ============================================================
# 字段检查
# ============================================================
def _has_section(text: str, *titles: str) -> bool:
    """是否存在某二级标题段落且其下有非空内容。"""
    for title in titles:
        # 匹配 "## 标题" 后到下一个 "## " 或文末之间的内容
        pat = re.compile(
            r'^##\s+' + re.escape(title) + r'\s*$(?P<body>.*?)(?=^##\s|\Z)',
            re.MULTILINE | re.DOTALL,
        )
        m = pat.search(text)
        if m:
            body = m.group("body")
            # 去掉 html 注释和空白后是否还有内容
            body = re.sub(r'<!--.*?-->', '', body, flags=re.DOTALL)
            # 去掉纯模板占位 (如 "-" 空列表项 / 尖括号占位)
            stripped = re.sub(r'[-*\s]', '', body)
            stripped = re.sub(r'<[^>]*>', '', stripped)
            if stripped.strip():
                return True
    return False


def _find_ai_usage(text: str) -> tuple[bool, str | None]:
    # 逐个 AI-Usage 出现处检查, 跳过被尖括号包裹的模板占位 (如 "<none|light|medium|heavy>")
    found_placeholder = False
    for m in re.finditer(r'AI-Usage:\s*(<?)\s*([a-zA-Z][\w|/ -]*)', text):
        bracketed = m.group(1) == "<"
        raw = m.group(2)
        # 取第一个 token (占位符形如 none|light|medium|heavy)
        token = re.split(r'[|/\s]', raw, maxsplit=1)[0].lower()
        if bracketed:
            found_placeholder = True
            continue
        # 真实填写的值
        return (token in AI_USAGE_VALUES, token)
    if found_placeholder:
        # 只找到占位符, 视为未填写
        return (False, None)
    return (False, None)


def find_ai_usage_in_commits(diff_base: str | None) -> tuple[bool, str | None]:
    """
    从本次 MR 的 commit trailer 里读 AI-Usage (自动采集的权威来源)。
    优先于 MR 描述里的手填值 —— AI-Usage 由 collect_ai_usage.py 在提交时自动写入,
    不应由人手填。返回 (是否合法, 值)。
    """
    base = diff_base or "HEAD~1"
    try:
        out = subprocess.run(
            ["git", "log", f"{base}..HEAD", "--format=%B"],
            check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stdout
    except Exception:
        return (False, None)
    # 取最新一条出现的 AI-Usage trailer (git log 从新到旧, 第一个匹配即最新提交)
    for m in re.finditer(r'(?im)^AI-Usage:\s*([a-zA-Z]\w*)', out):
        token = m.group(1).lower()
        return (token in AI_USAGE_VALUES, token)
    return (False, None)


# ============================================================
# 大变更判定 (基于 git diff 统计)
# ============================================================
def _fnmatch_any(path: str, patterns: list[str]) -> bool:
    import fnmatch
    for pat in patterns:
        # 目录前缀模式 "ci/" 视为 "ci/**"
        if pat.endswith("/"):
            if path.startswith(pat) or fnmatch.fnmatch(path, pat + "**"):
                return True
        elif fnmatch.fnmatch(path, pat):
            return True
    return False


def detect_large_change(cfg: dict, diff_base: str | None) -> tuple[bool, list[str]]:
    """返回 (是否大变更, 触发原因列表)。无 git 时返回 (False, [])。"""
    lc = cfg["large_change"]
    reasons: list[str] = []
    try:
        base = diff_base or "HEAD~1"
        out = subprocess.run(
            ["git", "diff", "--numstat", f"{base}...HEAD"],
            check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stdout
    except Exception:
        return (False, [])

    total = 0
    excluded = lc.get("excluded_paths", [])
    sensitive = lc.get("sensitive_paths", [])
    schema = lc.get("schema_paths", [])
    touched_sensitive = set()
    touched_schema = set()

    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        add, dele, path = parts
        if _fnmatch_any(path, sensitive):
            touched_sensitive.add(path)
        if _fnmatch_any(path, schema):
            touched_schema.add(path)
        if _fnmatch_any(path, excluded):
            continue
        try:
            total += int(add) + int(dele)
        except ValueError:
            pass  # 二进制文件 numstat 是 "-"

    threshold = int(lc.get("line_threshold", 500))
    if total >= threshold:
        reasons.append(f"净改动 {total} 行 ≥ {threshold}")
    if touched_sensitive:
        reasons.append(f"触及高敏路径: {', '.join(sorted(touched_sensitive))}")
    if touched_schema:
        reasons.append(f"含 schema 变更: {', '.join(sorted(touched_schema))}")

    return (len(reasons) > 0, reasons)


def _get_ci_summary_path() -> str | None:
    """获取 CI 平台的 Job Summary 路径。
    GitHub Actions: $GITHUB_STEP_SUMMARY
    GitLab CI: 暂无原生 Job Summary (未来可扩展写入 artifacts)
    """
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        return path
    return None


def _write_large_diff_summary(
    total: int,
    threshold: int,
    reasons: list[str],
    diff_base: str | None,
    excluded: list[str],
) -> None:
    """当 PR 超过行阈值时，向 CI Job Summary 写拆分建议（含 Top 目录分布）。"""
    summary_path = _get_ci_summary_path()
    if not summary_path:
        return
    import collections
    import fnmatch as _fnmatch

    dir_totals: dict[str, int] = collections.defaultdict(int)
    try:
        base = diff_base or "HEAD~1"
        out = subprocess.run(
            ["git", "diff", "--numstat", f"{base}...HEAD"],
            check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stdout
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            add_s, del_s, path = parts
            if any(_fnmatch.fnmatch(path, p) for p in excluded):
                continue
            try:
                lines = int(add_s) + int(del_s)
            except ValueError:
                continue
            top_dir = path.split("/")[0] if "/" in path else "."
            dir_totals[top_dir] += lines
    except Exception:
        pass

    top5 = sorted(dir_totals.items(), key=lambda x: x[1], reverse=True)[:5]

    try:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("\n## ⚠️ 大变更提示\n\n")
            f.write(f"本次 PR 净改动 **{total}** 行（阈值 {threshold}），建议拆分为更小的 PR。\n\n")
            if reasons:
                for r in reasons:
                    f.write(f"- {r}\n")
                f.write("\n")
            if top5:
                f.write("### 改动分布（Top 目录）\n\n")
                f.write("| 目录 | 增减行数 |\n|------|----------|\n")
                for d, n in top5:
                    f.write(f"| `{d}` | {n} |\n")
                f.write("\n")
            f.write("> 建议按业务模块拆分，每个 PR 控制在 300 行以内，方便 review 和回滚。\n")
    except OSError:
        pass


# ============================================================
# 模式判定: soft / hard
# ============================================================
def resolve_mode(cfg: dict, force_soft: bool) -> tuple[str, str]:
    """返回 (mode, 说明)。mode ∈ {soft, hard}。"""
    if force_soft:
        return ("soft", "命令行 --soft 强制软模式")
    meta = cfg["metadata"]
    enforcement = (meta.get("enforcement") or "soft").lower()
    if enforcement == "hard":
        return ("hard", "config enforcement=hard")
    # soft: 检查 deadline
    deadline = meta.get("soft_deadline")
    if deadline:
        try:
            dl = dt.date.fromisoformat(str(deadline))
            if dt.date.today() > dl:
                return ("hard", f"soft_deadline {dl} 已过, 自动转硬")
            return ("soft", f"软模式, deadline {dl}")
        except ValueError:
            return ("soft", "软模式 (deadline 格式无法解析)")
    return ("soft", "软模式 (无 deadline)")


# ============================================================
# 主流程
# ============================================================
def validate(text: str, cfg: dict, diff_base: str | None) -> list[str]:
    """返回缺失项列表 (空 = 全部通过)。"""
    problems: list[str] = []
    fields = cfg["metadata"].get("mandatory_fields", [])

    if "background" in fields and not _has_section(text, "背景", "Background"):
        problems.append("缺少 ## 背景 段落 (或内容为空)")
    if "changes" in fields and not _has_section(text, "变更内容", "Changes"):
        problems.append("缺少 ## 变更内容 段落 (或内容为空)")
    if "ai_usage" in fields:
        # AI-Usage 自动采集: 优先读 commit trailer (权威来源, 由 collect_ai_usage.py 写入),
        # 不要求人在 MR 描述里手填。描述里的值仅作 trailer 缺失时的兜底。
        ok, val = find_ai_usage_in_commits(diff_base)
        if val is None:
            # trailer 没有 → 退回看描述 (兼容老 MR / 未装 hook 的仓库)
            ok, val = _find_ai_usage(text)
        if val is None:
            problems.append(
                "未检测到 AI-Usage (应由 git hook 自动写入 commit trailer; "
                "见 governance/scripts/install-hooks.sh)"
            )
        elif not ok:
            problems.append(
                f'AI-Usage 值非法 "{val}" (应为 none/light/medium/heavy/used)'
            )
    if "self_test" in fields and not _has_section(text, "自测确认", "Self Test", "自测"):
        problems.append("缺少 ## 自测确认 段落 (或内容为空)")

    # 大变更 → 要求风险与回滚
    is_large, reasons = detect_large_change(cfg, diff_base)
    if is_large:
        if not _has_section(text, "风险与回滚", "风险", "Risk"):
            problems.append(
                f"大变更需填 ## 风险与回滚 ({'; '.join(reasons)})"
            )

    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="MR 治理描述校验器")
    ap.add_argument("--file", help="MR 描述文件路径")
    ap.add_argument("--config", help="governance.config.yml 路径")
    ap.add_argument("--diff-base", help="diff 基准, 用于大变更判定")
    ap.add_argument("--soft", action="store_true", help="强制软模式 (仅警告)")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        sys.stderr.write(f"[validate-mr] 配置错误: {exc}\n")
        return 2
    text = read_description(args.file)

    if not text.strip():
        sys.stderr.write("[mr-validate] 错误: 无法获取 MR 描述 "
                         "(--file / CI_MERGE_REQUEST_DESCRIPTION / stdin 均为空)。\n")
        # 描述为空在硬模式下视为不通过
        mode, _ = resolve_mode(cfg, args.soft)
        return 1 if mode == "hard" else 0

    mode, reason = resolve_mode(cfg, args.soft)
    problems = validate(text, cfg, args.diff_base)

    is_large, reasons = detect_large_change(cfg, args.diff_base)
    if is_large:
        lc = cfg["large_change"]
        total_lines = 0
        try:
            base = args.diff_base or "HEAD~1"
            ns = subprocess.run(
                ["git", "diff", "--numstat", f"{base}...HEAD"],
                check=True, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            ).stdout
            excluded = lc.get("excluded_paths", [])
            import fnmatch as _fn
            for ln in ns.splitlines():
                pts = ln.split("\t")
                if len(pts) == 3:
                    try:
                        if not any(_fn.fnmatch(pts[2], p) for p in excluded):
                            total_lines += int(pts[0]) + int(pts[1])
                    except ValueError:
                        pass
        except Exception:
            total_lines = int(lc.get("line_threshold", 500))
        _write_large_diff_summary(
            total_lines,
            int(lc.get("line_threshold", 500)),
            reasons,
            args.diff_base,
            lc.get("excluded_paths", []),
        )

    if not problems:
        print(f"[mr-validate] PASS ({mode} 模式: {reason})")
        return 0

    label = "FAIL" if mode == "hard" else "WARN"
    print(f"[mr-validate] {label} ({mode} 模式: {reason})\n")
    for p in problems:
        marker = "✗" if mode == "hard" else "⚠"
        print(f"  {marker} {p}")
    print()

    if mode == "hard":
        print("[mr-validate] 硬模式: 上述缺失项必须补全才能合并。")
        print("模板见 .gitlab/merge_request_templates/default.md")
        return 1
    else:
        meta_dl = cfg["metadata"].get("soft_deadline")
        if meta_dl:
            print(f"[mr-validate] 软模式: 暂不阻断。这些项将在 {meta_dl} 后阻断合并, 请尽早补全。")
        else:
            print("[mr-validate] 软模式: 暂不阻断。配置 soft_deadline 后将到期转硬, 请尽早补全。")
        return 0


if __name__ == "__main__":
    sys.exit(main())
