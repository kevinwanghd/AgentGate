#!/usr/bin/env python3
"""
report_expired.py — 过期 / 即将过期 risk: 注解周报

扫描仓库全量源码, 找出所有 risk: 注解, 按过期状态分类输出报告。
退出码始终为 0 (报告用途, 不阻断)。

用法:
    python report_expired.py [--root .] [--config governance.config.yml] [--output report.md]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys

from governance_common import ConfigError, load_config as load_shared_config

try:
    import yaml  # type: ignore
    _HAS_YAML = True
except Exception:
    _HAS_YAML = False

# 复用 scan_risks.py 相同的扩展名和配置加载逻辑
SCAN_EXTENSIONS = {
    ".cs", ".js", ".ts", ".jsx", ".tsx", ".java", ".go",
    ".py", ".rb", ".php", ".cpp", ".cc", ".c", ".h", ".hpp",
    ".kt", ".rs", ".scala", ".swift",
}

DEFAULT_MAX_AGE = 180
WARN_DAYS_BEFORE = 30  # 提前 N 天预警

_INLINE_RE = re.compile(
    r'risk:\s*([\w-]+)'
    r'.*?reason:\s*"([^"]*)"'
    r'.*?owner:\s*(@?[\w/.-]+)'
    r'.*?reviewed:\s*(\d{4}-\d{2}-\d{2})',
    re.IGNORECASE | re.DOTALL,
)

# 块注解: 两行之间合并扫描, 此处只抓 reviewed 日期
_BLOCK_RE = re.compile(
    r'risk-begin'
    r'.*?type:\s*([\w-]+)'
    r'.*?reason:\s*([^\n]+)'
    r'.*?owner:\s*(@?[\w/.-]+)'
    r'.*?reviewed:\s*(\d{4}-\d{2}-\d{2})'
    r'.*?risk-end',
    re.IGNORECASE | re.DOTALL,
)


def load_max_age(config_path: str | None) -> int:
    cfg = load_shared_config(
        config_path,
        {"risk_annotations": {"reviewed_max_age_days": DEFAULT_MAX_AGE}},
        ("risk_annotations",),
    )
    try:
        return int(cfg["risk_annotations"]["reviewed_max_age_days"])
    except (TypeError, ValueError) as exc:
        raise ConfigError("risk_annotations.reviewed_max_age_days 必须是整数") from exc


def scan_file(path: str) -> list[dict]:
    """返回该文件里找到的所有注解记录。"""
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return []

    records = []
    # 多行块优先 (贪婪匹配, 避免和单行重复)
    for m in _BLOCK_RE.finditer(text):
        lineno = text[: m.start()].count("\n") + 1
        records.append({
            "file": path, "line": lineno,
            "type": m.group(1).strip().lower(),
            "reason": m.group(2).strip().strip('"'),
            "owner": m.group(3).strip(),
            "reviewed": m.group(4).strip(),
        })

    # 单行内联
    for m in _INLINE_RE.finditer(text):
        lineno = text[: m.start()].count("\n") + 1
        records.append({
            "file": path, "line": lineno,
            "type": m.group(1).strip().lower(),
            "reason": m.group(2).strip(),
            "owner": m.group(3).strip(),
            "reviewed": m.group(4).strip(),
        })
    return records


def classify(reviewed_str: str, max_age: int) -> tuple[str, int]:
    """
    返回 (状态, 剩余天数)。
    状态: 'expired' | 'expiring_soon' | 'ok'
    剩余天数: 负数表示已过期多少天
    """
    try:
        rev = dt.date.fromisoformat(reviewed_str)
    except ValueError:
        return ("expired", -9999)
    age = (dt.date.today() - rev).days
    remaining = max_age - age
    if remaining < 0:
        return ("expired", remaining)
    if remaining < WARN_DAYS_BEFORE:
        return ("expiring_soon", remaining)
    return ("ok", remaining)


def collect(root: str, max_age: int) -> dict[str, list[dict]]:
    """返回 {状态: [记录]}"""
    buckets: dict[str, list[dict]] = {"expired": [], "expiring_soon": [], "ok": []}
    skip_dirs = {".git", "node_modules", "bin", "obj", ".idea", "__pycache__"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fname in filenames:
            if os.path.splitext(fname)[1].lower() not in SCAN_EXTENSIONS:
                continue
            fpath = os.path.join(dirpath, fname)
            for rec in scan_file(fpath):
                status, remaining = classify(rec["reviewed"], max_age)
                rec["remaining"] = remaining
                rec["status"] = status
                buckets[status].append(rec)
    return buckets


def render(buckets: dict, max_age: int) -> str:
    today = dt.date.today().isoformat()
    lines = [
        f"# 过期注解周报 — {today}",
        "",
        f"> 注解有效期：{max_age} 天 | 预警窗口：{WARN_DAYS_BEFORE} 天",
        "",
    ]
    total = sum(len(v) for v in buckets.values())
    expired = buckets["expired"]
    soon = buckets["expiring_soon"]
    ok = buckets["ok"]

    lines += [
        "## 摘要",
        "",
        f"| 状态 | 数量 |",
        f"|---|---|",
        f"| 🔴 已过期 | {len(expired)} |",
        f"| 🟡 即将过期（{WARN_DAYS_BEFORE} 天内）| {len(soon)} |",
        f"| ✅ 有效 | {len(ok)} |",
        f"| 合计 | {total} |",
        "",
    ]

    if expired:
        lines += ["## 🔴 已过期（需立即更新或移除注解）", ""]
        lines += _table(expired, show_overdue=True)

    if soon:
        lines += [f"## 🟡 即将过期（{WARN_DAYS_BEFORE} 天内到期）", ""]
        lines += _table(soon, show_overdue=False)

    if not expired and not soon:
        lines += ["## ✅ 所有注解均在有效期内\n"]

    return "\n".join(lines) + "\n"


def _table(records: list[dict], show_overdue: bool) -> list[str]:
    header = "| 文件 | 行 | 类型 | owner | reviewed | " + (
        "已过期(天)" if show_overdue else "剩余(天)"
    ) + " |"
    sep = "|---|---|---|---|---|---|"
    rows = [header, sep]
    for r in sorted(records, key=lambda x: x["remaining"]):
        days = abs(r["remaining"]) if show_overdue else r["remaining"]
        rows.append(
            f"| `{r['file']}` | {r['line']} | {r['type']} "
            f"| {r['owner']} | {r['reviewed']} | {days} |"
        )
    return rows + [""]


def main() -> int:
    ap = argparse.ArgumentParser(description="过期 risk: 注解周报")
    ap.add_argument("--root", default=".", help="扫描根目录")
    ap.add_argument("--config", help="governance.config.yml 路径")
    ap.add_argument("--output", help="输出文件路径 (默认 stdout)")
    ap.add_argument("--fail-on-expired", action="store_true",
                    help="有过期注解时返回退出码 1 (默认仅报告)")
    args = ap.parse_args()

    try:
        max_age = load_max_age(args.config)
    except ConfigError as exc:
        sys.stderr.write(f"[report-expired] 配置错误: {exc}\n")
        return 2
    buckets = collect(args.root, max_age)
    report = render(buckets, max_age)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[report-expired] 报告已写入 {args.output}", file=sys.stderr)
    else:
        print(report)

    if args.fail_on_expired and buckets["expired"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
