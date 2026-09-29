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


def run(name: str, cmd: list[str], cwd: str | None = None, stdin=None) -> tuple[int, str, str]:
    """运行子命令，返回 (exit_code, stdout, stderr)。"""
    print(f"\n{'='*60}")
    print(f"[governance_scan_all] Running: {' '.join(cmd)}")
    print(f"{'='*60}")
    try:
        result = subprocess.run(
            cmd,
            stdin=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
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
        default=None,
        help="Path to governance.config.yml (omit to let each scan use its own default)",
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
        sys.executable or "python3",
        os.path.join(scripts_dir, "scan_risks.py"),
        "--diff-base",
        args.diff_base,
    ]
    # 只在显式传入时转发 --config; 显式路径不存在会被子脚本当作配置错误
    if args.config:
        risk_cmd.extend(["--config", args.config])
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
        sys.executable or "python3",
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
        sys.executable or "python3",
        os.path.join(scripts_dir, "validate_mr.py"),
        "--diff-base",
        args.diff_base,
    ]
    if args.config:
        mr_cmd.extend(["--config", args.config])
    mr_stdin = None
    if args.pr_body_file:
        mr_cmd.extend(["--file", args.pr_body_file])
    else:
        # 不传 --file: validate_mr 会先读 CI_MERGE_REQUEST_DESCRIPTION (GitLab),
        # 再读 stdin; stdin 接 DEVNULL 防止挂起, 且不依赖 Windows 上不存在的 /dev/null
        mr_stdin = subprocess.DEVNULL

    rc, stdout, stderr = run("mr-validate", mr_cmd, stdin=mr_stdin)
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

    # 任一非零即失败; 被信号终止的子进程返回负数, 不能用 max() 直接取 (会被 0 盖过)
    codes = [r.get("exit_code", 0) for r in results.values()]
    overall_exit = 0 if all(c == 0 for c in codes) else max(max(codes), 1)
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
