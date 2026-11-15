#!/usr/bin/env python3
"""等待仓库声明的 CI workflow 在 PR 提交上完成，并把结果交给 gate。

用法:
    wait_ci.py --repo owner/name --sha <提交> --workflows ci.yml,other.yml \
               [--timeout-minutes 30] [--interval-seconds 15] [--output "$GITHUB_OUTPUT"]

输出最后一行是 ``ci=pass|fail|skip``；配置了 --output 时同时写入 GITHUB_OUTPUT。
工作流在超时前没有启动、失败或被取消都算 fail。脚本自身退出码保持 0，
由 gate_decision.py 依据 evidence 判定，避免两步各判一次。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
PASSING = {"success", "neutral", "skipped"}


def latest_run(runs: list[dict]) -> dict | None:
    """同一提交可能有多条运行（重跑、并发事件），取编号最大的那条。"""
    if not runs:
        return None
    return max(runs, key=lambda run: (run.get("run_number") or 0, run.get("created_at") or ""))


def classify(run: dict | None) -> str | None:
    """运行还没结束返回 None；结束了返回 pass/fail。"""
    if not run or run.get("status") != "completed":
        return None
    return "pass" if run.get("conclusion") in PASSING else "fail"


def fetch_runs(repo: str, sha: str, workflow: str, token: str) -> list[dict]:
    query = urllib.parse.urlencode(
        {"head_sha": sha, "event": "pull_request", "per_page": 50}
    )
    url = f"{API}/repos/{repo}/actions/workflows/{urllib.parse.quote(workflow)}/runs?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agentgate-wait-ci",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response).get("workflow_runs") or []


def wait_for(repo: str, sha: str, workflows: list[str], token: str, timeout: float, interval: float) -> tuple[str, str]:
    """返回 (结果, 原因)。结果只会是 pass/fail。"""
    deadline = time.monotonic() + timeout
    pending = {workflow: None for workflow in workflows}
    while True:
        for workflow in list(pending):
            try:
                run = latest_run(fetch_runs(repo, sha, workflow, token))
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    return "fail", f"{workflow} 不存在或没有权限读取"
                if error.code in (401, 403):
                    return "fail", f"读取 {workflow} 被拒绝（需要 actions: read）"
                print(f"{workflow}: 读取失败 {error.code}，稍后重试", flush=True)
                continue
            except (urllib.error.URLError, TimeoutError) as error:
                print(f"{workflow}: 网络错误 {error}，稍后重试", flush=True)
                continue
            verdict = classify(run)
            if verdict == "fail":
                conclusion = run.get("conclusion")
                return "fail", f"{workflow} 运行 #{run.get('run_number')} 结束状态为 {conclusion}"
            if verdict == "pass":
                print(f"{workflow}: 运行 #{run.get('run_number')} 通过", flush=True)
                pending.pop(workflow)
        if not pending:
            return "pass", "配置的 CI workflow 全部通过"
        if time.monotonic() >= deadline:
            waiting = ", ".join(
                f"{workflow}({'未启动' if run is None else '未完成'})"
                for workflow, run in pending.items()
            )
            return "fail", f"等待 CI 超时：{waiting}"
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--sha", required=True, help="PR head 提交")
    parser.add_argument("--workflows", required=True, help="逗号分隔的 workflow 文件名")
    parser.add_argument("--timeout-minutes", type=float, default=30)
    parser.add_argument("--interval-seconds", type=float, default=15)
    parser.add_argument("--output", help="写入该文件（通常是 $GITHUB_OUTPUT）")
    args = parser.parse_args()

    workflows = [item.strip() for item in args.workflows.split(",") if item.strip()]
    if not workflows:
        print("ci=skip")
        return 0
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("缺少 GH_TOKEN，无法读取 CI 结果", file=sys.stderr)
        result, reason = "fail", "缺少读取 CI 的令牌"
    else:
        print(f"等待 {', '.join(workflows)} 在 {args.sha[:12]} 上的 pull_request 运行", flush=True)
        result, reason = wait_for(
            args.repo,
            args.sha,
            workflows,
            token,
            args.timeout_minutes * 60,
            args.interval_seconds,
        )
    print(reason, flush=True)
    print(f"ci={result}")
    if args.output:
        with open(args.output, "a", encoding="utf-8") as output:
            output.write(f"ci={result}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())