#!/usr/bin/env python3
"""
check_tested.py — 检测本次改动的生产代码是否做过测试 (diff-only, 不重跑测试)

原理同 scan_risks.py: 只看本次 diff, 秒级, 零外部依赖。它不重新跑测试,
而是检查 record_test_run.py 留下的真实运行痕迹 + 本次 diff 的测试文件改动。

判定 (对每个本次改动的"生产代码文件"):
  放行条件 (满足其一):
    A. 存在一条 failed==0 的测试运行记录, 且本次 MR diff 里改动了测试文件
       (双重信号: 痕迹证明跑过且全绿, diff 证明确实写/改了测试)
    B. 该文件被 --covers 显式声明在某条 failed==0 的记录里
    C. 文件上方/同文件有 risk:untested 注解 (复用风险注解契约豁免)
    D. 命中 config 的 testing.exclude_paths 白名单 (DTO / 迁移 / 生成代码等)

  硬规则 (优先于一切放行):
    - 任一测试运行记录 failed>0 或 failed==-1 (未知失败) → 直接拦, 不许带红测试提交。

信任边界 (写进文档): 静态痕迹证明"有没有测", 不证明"测得对不对"。
record_test_run.py 把痕迹绑到真实退出码, 但仍依赖 agent 如实运行 ——
要真正的证明, 用 CI 差异覆盖率 (见 docs, soft_deadline 后可选硬化)。

用法:
    python check_tested.py --diff-base origin/master
    python check_tested.py --staged
    python check_tested.py --diff-file my.diff --evidence .governance/test-evidence.jsonl

退出码:
    0  全部改动的生产代码都有测试痕迹 / 合法豁免; 且无失败测试记录
    1  存在未测且未豁免的生产代码, 或存在失败测试记录 (硬阻断)
    2  运行错误
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
import re
import subprocess
import sys

try:
    import yaml  # type: ignore
    _HAS_YAML = True
except Exception:  # pragma: no cover
    _HAS_YAML = False


EVIDENCE_PATH = ".governance/test-evidence.jsonl"

DEFAULT_CONFIG = {
    "testing": {
        "enforcement": "soft",          # v1 软启动
        "soft_deadline": None,
        "exclude_paths": [              # 整目录/模式免测试检查
            "**/Migrations/**",
            "**/*.Designer.cs",
            "**/*.generated.*",
            "**/Program.cs",
            "**/Startup.cs",
            "**/*Dto.cs",
            "**/*Dtos.cs",
            "**/*.proto",
            "*.sql",
        ],
        "untested_max_age_days": 180,   # risk:untested 注解有效期, 同风险注解
        "reason_blacklist": [
            "临时", "先这样", "历史原因", "TODO", "待确认",
            "quick fix", "temp", "wip", "hack", "for now",
        ],
    },
}

# 生产代码扩展名 (与 scan_risks 一致)
PROD_EXTENSIONS = {
    ".cs", ".js", ".ts", ".jsx", ".tsx", ".java", ".go",
    ".py", ".rb", ".php", ".cpp", ".cc", ".c", ".h", ".hpp",
    ".kt", ".rs", ".scala", ".swift",
}

# 测试文件判定: 路径或文件名带这些标志
_TEST_PATH_RE = re.compile(
    r'(^|/)(tests?|spec|__tests__)(/|$)'
    r'|(\.tests?|\.spec|_test|test_)\.[a-z]+$'
    r'|tests?\.[a-z]+$',
    re.IGNORECASE,
)

MIN_REASON_LEN = 10
ANNOTATION_LOOKBACK = 5

_UNTESTED_INLINE_RE = re.compile(
    r'risk:\s*untested'
    r'.*?reason:\s*"(?P<reason>[^"]*)"'
    r'.*?owner:\s*(?P<owner>@?[\w/.-]+)'
    r'.*?reviewed:\s*(?P<reviewed>\d{4}-\d{2}-\d{2})',
    re.IGNORECASE | re.DOTALL,
)


# ============================================================
# 配置
# ============================================================
def load_config(path: str | None) -> dict:
    if not path:
        for cand in ("governance.config.yml", "governance.config.yaml"):
            if os.path.isfile(cand):
                path = cand
                break
    if not path or not os.path.isfile(path) or not _HAS_YAML:
        return DEFAULT_CONFIG
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:  # pragma: no cover
        return DEFAULT_CONFIG
    merged = {**DEFAULT_CONFIG, **data}
    merged["testing"] = {**DEFAULT_CONFIG["testing"], **(data.get("testing") or {})}
    return merged


# ============================================================
# git / diff
# ============================================================
def run_git(args: list[str]) -> str:
    try:
        return subprocess.run(
            ["git", *args], check=True, capture_output=True, text=True
        ).stdout
    except FileNotFoundError:
        sys.stderr.write("[check-tested] 找不到 git\n")
        sys.exit(2)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"[check-tested] git 失败: {e.stderr}\n")
        sys.exit(2)


def get_diff(diff_base: str | None, staged: bool) -> str:
    if staged:
        return run_git(["diff", "--cached", "--unified=0", "--no-color"])
    if diff_base:
        return run_git(["diff", f"{diff_base}...HEAD", "--unified=0", "--no-color"])
    return run_git(["diff", "HEAD~1...HEAD", "--unified=0", "--no-color"])


_FILE_RE = re.compile(r'^\+\+\+ (?:b/)?(.+)$')


def changed_files(diff_text: str) -> set[str]:
    files: set[str] = set()
    for line in diff_text.splitlines():
        m = _FILE_RE.match(line)
        if m and m.group(1) != "/dev/null":
            path = m.group(1).strip()
            files.add(path)
    return files


def is_test_file(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path))


def _fnmatch_any(path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/"):
            if path.startswith(pat) or fnmatch.fnmatch(path, pat + "**"):
                return True
        elif fnmatch.fnmatch(path, pat):
            return True
    return False


# ============================================================
# 测试证据
# ============================================================
def load_evidence(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _latest_per_cmd(evidence: list[dict]) -> list[dict]:
    """同一命令 append 多条时, 只保留最新 (按 ts)。"""
    latest: dict[str, dict] = {}
    for rec in evidence:
        cmd = rec.get("cmd", "")
        prev = latest.get(cmd)
        if prev is None or str(rec.get("ts", "")) >= str(prev.get("ts", "")):
            latest[cmd] = rec
    return list(latest.values())


def summarize_for_trailer(evidence: list[dict]) -> str:
    """把证据汇总成一行 Tested: trailer 值。供提交 hook 写入 commit。"""
    eff = _latest_per_cmd(evidence)
    if not eff:
        return "none"
    total = passed = 0
    any_fail = False
    have_counts = False
    for r in eff:
        f = r.get("failed")
        if isinstance(f, int) and f != 0:
            any_fail = True
        t, p = r.get("total"), r.get("passed")
        if isinstance(t, int) and isinstance(p, int):
            total += t
            passed += p
            have_counts = True
    if any_fail:
        return "fail"
    if have_counts:
        return f"pass ({passed}/{total})"
    return "pass"


def read_tested_trailer(diff_base: str | None) -> str | None:
    """CI 场景: 证据文件 (gitignore) 不在, 退回读 commit 的 Tested: trailer。"""
    base = diff_base or "HEAD~1"
    try:
        out = subprocess.run(
            ["git", "log", f"{base}..HEAD", "--format=%B"],
            check=True, capture_output=True, text=True,
        ).stdout
    except Exception:
        return None
    for m in re.finditer(r'(?im)^Tested:\s*(\S+)', out):
        return m.group(1).lower()
    return None


def _read_lines(path: str) -> list[str] | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return None


def has_untested_annotation(path: str, cfg: dict) -> tuple[bool, str]:
    """整文件搜 risk:untested 注解, 校验字段。返回 (是否合法豁免, 说明)。"""
    lines = _read_lines(path)
    if lines is None:
        return (False, "无法读取文件")
    text = "\n".join(lines)
    m = _UNTESTED_INLINE_RE.search(text)
    if not m:
        return (False, "无 risk:untested 注解")
    reason = m.group("reason")
    reviewed = m.group("reviewed")
    tc = cfg["testing"]
    if len(reason.strip()) < MIN_REASON_LEN:
        return (False, f"untested reason 过短 (<{MIN_REASON_LEN}字)")
    low = reason.lower()
    for bad in tc.get("reason_blacklist", []):
        if bad.lower() in low:
            return (False, f'untested reason 含黑名单词 "{bad}"')
    try:
        rev = dt.date.fromisoformat(reviewed)
        age = (dt.date.today() - rev).days
        max_age = int(tc.get("untested_max_age_days", 180))
        if age > max_age:
            return (False, f"untested 注解已过期 ({age}天>{max_age}天)")
        if age < 0:
            return (False, "untested reviewed 在未来")
    except ValueError:
        return (False, f'untested reviewed 格式非法 "{reviewed}"')
    return (True, "合法 risk:untested 豁免")


# ============================================================
# 主逻辑
# ============================================================
def resolve_mode(cfg: dict, force_soft: bool) -> tuple[str, str]:
    if force_soft:
        return ("soft", "命令行 --soft")
    tc = cfg["testing"]
    enf = (tc.get("enforcement") or "soft").lower()
    if enf == "hard":
        return ("hard", "config enforcement=hard")
    dl = tc.get("soft_deadline")
    if dl:
        try:
            d = dt.date.fromisoformat(str(dl))
            if dt.date.today() > d:
                return ("hard", f"soft_deadline {d} 已过, 自动转硬")
            return ("soft", f"软模式, deadline {d}")
        except ValueError:
            return ("soft", "软模式 (deadline 格式无法解析)")
    return ("soft", "软模式 (无 deadline)")


def check(diff_text: str, evidence: list[dict], cfg: dict,
          trailer: str | None = None) -> tuple[list[str], list[dict]]:
    """
    返回 (硬错误列表, 未测文件违规列表)。
    硬错误: 失败的测试记录 (无条件拦)。

    evidence: 本地证据文件 (优先, 信息最全, 含 covers)。
    trailer:  CI 场景下证据文件不在仓库 (gitignore), 退回用 commit 的 Tested: trailer。
              取值 'pass' / 'pass (p/t)' / 'fail' / 'none'。
    """
    hard_errors: list[str] = []

    # 证据是 append-only 的: 同一条命令可能"先失败后修复再跑绿"留下多条记录。
    # 只看每条命令的最新一次结果 (按 ts), 否则历史失败会永久误拦。
    effective = _latest_per_cmd(evidence)

    # 1. 硬规则: 任何命令的"最新一次"运行失败 → 拦
    for rec in effective:
        failed = rec.get("failed")
        if isinstance(failed, int) and failed != 0:
            label = "未知失败(退出码非0)" if failed < 0 else f"{failed} 个用例失败"
            hard_errors.append(
                f"测试运行记录显示失败: {label} — cmd: {rec.get('cmd', '?')}"
            )

    # 2. 收集本次 diff 的文件
    files = changed_files(diff_text)
    prod_files = [
        f for f in files
        if os.path.splitext(f)[1].lower() in PROD_EXTENSIONS and not is_test_file(f)
    ]
    touched_test_file = any(is_test_file(f) for f in files)

    # 有没有一条全绿的测试记录 (只看每条命令最新结果)
    green_runs = [
        r for r in effective
        if isinstance(r.get("failed"), int) and r["failed"] == 0
    ]

    # CI 退路: 无本地证据但 commit 带 Tested: trailer
    trailer_pass = False
    if not effective and trailer:
        if trailer.startswith("fail"):
            hard_errors.append(
                "commit 的 Tested: trailer 标记测试失败 (Tested: fail)"
            )
        elif trailer.startswith("pass"):
            trailer_pass = True
    # --covers 显式覆盖到的文件集合 (仅取自全绿记录)
    covered: set[str] = set()
    for r in green_runs:
        for c in r.get("covers", []) or []:
            covered.add(c)

    violations: list[dict] = []
    tc = cfg["testing"]
    exclude = tc.get("exclude_paths", [])

    for path in sorted(prod_files):
        # D. 白名单
        if _fnmatch_any(path, exclude):
            continue
        # B. 显式 covers
        if path in covered:
            continue
        # A. 有全绿记录 + 本次改了测试文件 (双重信号)
        if green_runs and touched_test_file:
            continue
        # A. 有全绿记录 + 本次改了测试文件 (双重信号)
        if green_runs and touched_test_file:
            continue
        # A'. CI 退路: commit Tested: trailer 标记 pass + 本次改了测试文件
        if trailer_pass and touched_test_file:
            continue
        # C. risk:untested 注解豁免
        ok, why = has_untested_annotation(path, cfg)
        if ok:
            continue
        # 未通过任何放行条件
        reason = []
        if not green_runs and not trailer_pass:
            reason.append("无全绿测试记录/Tested:trailer (用 record_test_run.py 跑测试)")
        elif not touched_test_file:
            reason.append("本次 diff 未改动任何测试文件, 且未 --covers 声明")
        reason.append(f"也无合法 risk:untested 注解 ({why})")
        violations.append({"file": path, "reasons": reason})

    return hard_errors, violations


def main() -> int:
    ap = argparse.ArgumentParser(description="检测改动代码是否做过测试")
    ap.add_argument("--diff-base", help="diff 基准 ref")
    ap.add_argument("--staged", action="store_true", help="检查已暂存改动")
    ap.add_argument("--diff-file", help="从文件读 diff (测试用)")
    ap.add_argument("--config", help="governance.config.yml 路径")
    ap.add_argument("--evidence", default=EVIDENCE_PATH, help="测试证据文件")
    ap.add_argument("--soft", action="store_true", help="强制软模式")
    ap.add_argument("--emit-trailer", action="store_true",
                    help="只输出 Tested: trailer 值 (供提交 hook 写入 commit), 不做检查")
    args = ap.parse_args()

    cfg = load_config(args.config)

    # 提交 hook 用: 把本地证据汇总成一行 Tested: trailer
    if args.emit_trailer:
        evidence = load_evidence(args.evidence)
        print(f"Tested: {summarize_for_trailer(evidence)}")
        return 0

    if args.diff_file:
        with open(args.diff_file, encoding="utf-8") as f:
            diff_text = f.read()
    else:
        diff_text = get_diff(args.diff_base, args.staged)

    if not diff_text.strip():
        print("[check-tested] diff 为空, 无需检查。")
        return 0

    evidence = load_evidence(args.evidence)
    # 本地无证据 (CI 场景) 时, 退回读 commit 的 Tested: trailer
    trailer = None
    if not evidence and not args.diff_file:
        trailer = read_tested_trailer(args.diff_base)
    hard_errors, violations = check(diff_text, evidence, cfg, trailer)
    mode, mode_why = resolve_mode(cfg, args.soft)

    # 失败测试记录: 无条件硬拦 (不受软模式影响 —— 带红测试提交永远不允许)
    if hard_errors:
        print("[check-tested] FAIL — 存在失败的测试运行记录:\n")
        for e in hard_errors:
            print(f"  ✗ {e}")
        print("\n修复测试后重新运行 record_test_run.py 再提交。")
        return 1

    if not violations:
        print(f"[check-tested] PASS ({mode} 模式: {mode_why}) — "
              "改动的生产代码均有测试痕迹或合法豁免。")
        return 0

    label = "FAIL" if mode == "hard" else "WARN"
    marker = "✗" if mode == "hard" else "⚠"
    print(f"[check-tested] {label} ({mode} 模式: {mode_why}) — 以下生产代码缺测试痕迹:\n")
    for v in violations:
        print(f"  {marker} {v['file']}")
        for r in v["reasons"]:
            print(f"      - {r}")
    print()
    print("  放行任一即可:")
    print("    1) 用 record_test_run.py 跑单元测试, 并在本次 MR 改动对应测试文件")
    print('    2) 确无法单测的代码加注解: '
          '// risk:untested reason:"..." owner:@team reviewed:今天')
    print("    3) DTO/迁移/生成代码等可在 governance.config.yml 的 "
          "testing.exclude_paths 白名单")

    if mode == "hard":
        return 1
    tc_dl = cfg["testing"].get("soft_deadline")
    if tc_dl:
        print(f"\n[check-tested] 软模式: 暂不阻断, {tc_dl} 后转硬。")
    else:
        print("\n[check-tested] 软模式: 暂不阻断 (配置 soft_deadline 后到期转硬)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
