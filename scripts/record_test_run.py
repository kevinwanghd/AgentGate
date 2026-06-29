#!/usr/bin/env python3
"""
record_test_run.py — 测试运行记录器

包装一条测试命令, 执行它、解析通过/失败数, 把结果追加到
.governance/test-evidence.jsonl。痕迹来自命令的真实退出码与输出,
不是手写声明 —— 让"如实记录"成为默认, "造假"需要刻意伪造。

设计原则:
  - 痕迹绑到真实执行: 退出码非 0 / 解析到 failed>0, 都如实记下来。
  - 不阻断: 本脚本只记录, 拦不拦由 check_tested.py 在提交/CI 时判断。
  - 透传退出码: 包装的测试命令失败, 本脚本也返回相同退出码, 不掩盖。

用法:
    # 跑测试并记录 (-- 之后是被包装的命令, 原样执行)
    python record_test_run.py -- dotnet test X.Flow.sln --filter Category=Unit

    # 显式声明本次测试覆盖了哪些生产文件 (可选, 逗号分隔)
    python record_test_run.py --covers src/A.cs,src/B.cs -- dotnet test ...

    # 指定证据文件
    python record_test_run.py --evidence .governance/test-evidence.jsonl -- pytest -q

退出码: 与被包装命令一致 (命令成功=0, 失败=其退出码)。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

EVIDENCE_PATH = ".governance/test-evidence.jsonl"

# 从常见测试框架输出里解析通过/失败数。保守: 解析不到就留 None, 不瞎猜。
_PARSERS = [
    # dotnet test / vstest:  "Passed!  - Failed: 0, Passed: 42, ..."  或  "Failed: 2, Passed: 40"
    re.compile(r'(?i)Failed:\s*(?P<failed>\d+),\s*Passed:\s*(?P<passed>\d+)'),
    # pytest:  "42 passed" / "40 passed, 2 failed"
    re.compile(r'(?i)(?:(?P<failed>\d+)\s+failed[,\s]+)?(?P<passed>\d+)\s+passed'),
    # jest / mocha:  "Tests: 2 failed, 40 passed, 42 total"
    re.compile(r'(?i)Tests:.*?(?:(?P<failed>\d+)\s+failed.*?)?(?P<passed>\d+)\s+passed'),
    # JUnit surefire:  "Tests run: 42, Failures: 0, Errors: 0"
    re.compile(r'(?i)Tests run:\s*(?P<total>\d+),\s*Failures:\s*(?P<failed>\d+)'),
]


def parse_counts(output: str) -> dict:
    """从测试输出解析 total/passed/failed。解析不到的字段留 None。"""
    for rx in _PARSERS:
        m = rx.search(output)
        if not m:
            continue
        gd = m.groupdict()
        failed = int(gd["failed"]) if gd.get("failed") not in (None, "") else None
        passed = int(gd["passed"]) if gd.get("passed") not in (None, "") else None
        total = int(gd["total"]) if gd.get("total") not in (None, "") else None
        if total is None and passed is not None and failed is not None:
            total = passed + failed
        if failed is None and total is not None and passed is not None:
            failed = total - passed
        return {"total": total, "passed": passed, "failed": failed}
    return {"total": None, "passed": None, "failed": None}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="包装并记录测试运行", add_help=True,
    )
    ap.add_argument("--evidence", default=EVIDENCE_PATH, help="证据文件路径")
    ap.add_argument("--covers", default="",
                    help="本次测试覆盖的生产文件, 逗号分隔 (可选)")
    ap.add_argument("--tool", default="", help="工具标识 (可选)")
    ap.add_argument("rest", nargs=argparse.REMAINDER,
                    help="-- 之后为被包装的测试命令")
    args = ap.parse_args()

    # 取 -- 之后的命令
    cmd = args.rest
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        sys.stderr.write(
            "[record-test] 用法: record_test_run.py [选项] -- <测试命令>\n"
            "例: record_test_run.py -- dotnet test X.Flow.sln\n"
        )
        return 2

    sys.stderr.write(f"[record-test] 运行: {' '.join(cmd)}\n")
    # 实时透传输出, 同时捕获用于解析
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        sys.stderr.write(f"[record-test] 找不到命令: {cmd[0]}\n")
        return 127

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    # 回显给用户
    sys.stdout.write(proc.stdout or "")
    sys.stderr.write(proc.stderr or "")

    counts = parse_counts(combined)
    covers = [c.strip() for c in args.covers.split(",") if c.strip()]

    record = {
        "ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cmd": " ".join(cmd),
        "exit_code": proc.returncode,
        "total": counts["total"],
        "passed": counts["passed"],
        # failed 兜底: 命令退出码非 0 但没解析到 failed 数, 记为 -1 表示"未知失败"
        "failed": counts["failed"] if counts["failed"] is not None
                  else (0 if proc.returncode == 0 else -1),
        "covers": covers,
    }
    if args.tool:
        record["tool"] = args.tool

    os.makedirs(os.path.dirname(args.evidence) or ".", exist_ok=True)
    with open(args.evidence, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    status = "通过" if record["failed"] == 0 and proc.returncode == 0 else "失败"
    sys.stderr.write(
        f"[record-test] 记录已写入 {args.evidence} "
        f"(结果={status}, total={record['total']}, failed={record['failed']})\n"
    )
    if not covers:
        sys.stderr.write(
            "[record-test] 提示: 未指定 --covers, check_tested.py 将依据"
            "本次 diff 是否改动测试文件来判断覆盖关系。\n"
        )

    # 透传被包装命令的退出码, 不掩盖测试失败
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
