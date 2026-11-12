#!/usr/bin/env python3
"""
governance_scan_all.py — 统一入口：一次性执行 risk-scan + secret-scan + mr-validate

用途: CI 优化 P1-B，将 3 个独立的 Python scan job 合并为 1 个，
      减少 runner 启动次数（3 runner → 1 runner，节省约 40~80 秒启动开销）。

输出:
  - 每个 scan 的详细结果实时打印到 stdout
  - 退出码: 任意一项失败则整体失败 (max exit code)
  - stdout 末尾打印摘要 JSON，方便 CI 日志解析

用法:
  python3 scripts/governance_scan_all.py --diff-base origin/main --config governance.config.yml
  python3 scripts/governance_scan_all.py --diff-base origin/main --config governance.config.yml --pr-body-file /tmp/pr_body.txt
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from governance_common import host_python, msys_to_host


def run(name: str, cmd: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    """运行子命令，返回 (exit_code, stdout, stderr)。"""
    # 在 MINGW 下将所有看起来像文件路径的参数转为原生路径
    fixed_cmd: list[str] = []
    for arg in cmd:
        # 绝对 MSYS 路径 (/c/... 或 /tmp/...) 需要转换；git ref 如 HEAD~1 不转
        if isinstance(arg, str) and (arg.startswith("/") and len(arg) > 2 and arg[1] not in (".", "/")):
            fixed_cmd.append(msys_to_host(arg))
        else:
            fixed_cmd.append(arg)
    print(f"\n{'='*60}")
    print(f"[governance_scan_all] Running: {' '.join(fixed_cmd)}")
    print(f"{'='*60}")
    try:
        result = subprocess.run(
            fixed_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            # 子命令不依赖 stdin；不显式关闭会让 validate_mr.py 在
            # 无 --file 时读父级 stdin，若父级 stdin 是未关闭的管道会挂死。
            stdin=subprocess.DEVNULL,
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        print(f"[governance_scan_all] ERROR running {name}: {e}", file=sys.stderr)
        return 2, "", str(e)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Run all governance scans: risk-scan, secret-scan, mr-validate."
    )
    parser.add_argument(
        "--diff-base",
        required=True,
        help="Git ref for diff base, e.g. origin/main",
    )
    parser.add_argument(
        "--config",
        default="governance.config.yml",
        help="Path to governance.config.yml",
    )
    parser.add_argument(
        "--pr-body-file",
        help="Path to file containing PR body (for mr-validate). "
             "If omitted, mr-validate reads from stdin.",
    )
    args = parser.parse_args(argv)

    results: dict[str, dict] = {}
    scripts_dir = os.path.dirname(os.path.abspath(__file__))

    # ── 1. Risk scan ────────────────────────────────────────────────
    risk_cmd = [
        host_python(),
        os.path.join(scripts_dir, "scan_risks.py"),
        "--diff-base",
        args.diff_base,
        "--config",
        args.config,
    ]
    rc, stdout, stderr = run("risk-scan", risk_cmd)
    results["risk_scan"] = {
        "exit_code": rc,
        "stdout_lines": len(stdout.splitlines()),
        "stderr_lines": len(stderr.splitlines()) if stderr else 0,
    }
    if rc != 0:
        results["risk_scan"]["status"] = "fail"
        print(f"[governance_scan_all] risk-scan FAILED (exit {rc})", file=sys.stderr)
    else:
        results["risk_scan"]["status"] = "pass"

    # ── 2. Secret scan ─────────────────────────────────────────────
    secret_cmd = [
        host_python(),
        os.path.join(scripts_dir, "scan_secrets.py"),
        "--diff-base",
        args.diff_base,
    ]
    rc, stdout, stderr = run("secret-scan", secret_cmd)
    results["secret_scan"] = {
        "exit_code": rc,
        "stdout_lines": len(stdout.splitlines()),
        "stderr_lines": len(stderr.splitlines()) if stderr else 0,
    }
    if rc != 0:
        results["secret_scan"]["status"] = "fail"
        print(f"[governance_scan_all] secret-scan FAILED (exit {rc})", file=sys.stderr)
    else:
        results["secret_scan"]["status"] = "pass"

    # ── 3. MR validation ───────────────────────────────────────────
    mr_cmd = [
        host_python(),
        os.path.join(scripts_dir, "validate_mr.py"),
        "--diff-base",
        args.diff_base,
        "--config",
        args.config,
    ]
    if args.pr_body_file:
        mr_cmd.extend(["--file", args.pr_body_file])
    # 不传 --file: validate_mr.py 会走 empty-body 逻辑（不报 FileNotFoundError）

    rc, stdout, stderr = run("mr-validate", mr_cmd)
    results["mr_validate"] = {
        "exit_code": rc,
        "stdout_lines": len(stdout.splitlines()),
        "stderr_lines": len(stderr.splitlines()) if stderr else 0,
    }
    if rc != 0:
        results["mr_validate"]["status"] = "fail"
        print(f"[governance_scan_all] mr-validate FAILED (exit {rc})", file=sys.stderr)
    else:
        results["mr_validate"]["status"] = "pass"

    # ── Summary ────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("[governance_scan_all] SUMMARY")
    print(f"{'='*60}")
    for name, res in results.items():
        status = res.get("status", "unknown")
        icon = "✅" if status == "pass" else "❌"
        print(f"  {icon} {name}: {status} (exit {res['exit_code']})")

    overall_exit = max(r.get("exit_code", 0) for r in results.values())
    summary = {
        "schema": "governance_scan_all/v1",
        "overall": "fail" if overall_exit != 0 else "pass",
        "exit_code": overall_exit,
        "results": results,
    }
    print(f"\n---\nJSON_SUMMARY_START{json.dumps(summary, ensure_ascii=False)}JSON_SUMMARY_END")
    print(f"\n[governance_scan_all] Overall exit code: {overall_exit}")

    return overall_exit


if __name__ == "__main__":
    raise SystemExit(main())
