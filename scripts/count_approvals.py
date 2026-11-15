#!/usr/bin/env python3
"""统计一个 PR 上有效的 approve 数，供 gate 的 critical 审批使用。

有效 = 最新一条 review 是 APPROVED、提交人不区分大小写不是 bot、且时间不早于当前 head 提交。
用法: count_approvals.py --repo owner/name --pr 12，结果打印到 stdout（一个整数）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys


def gh_json(path: str, paginate: bool = False):
    args = ["gh", "api", path]
    if paginate:
        args += ["--paginate", "--slurp"]
    run = subprocess.run(args, capture_output=True, text=True)
    if run.returncode != 0:
        raise SystemExit(f"gh api 失败：{run.stderr[-300:]}")
    value = json.loads(run.stdout or "null")
    if paginate:
        pages = value if isinstance(value, list) else [value]
        return [item for page in pages for item in (page if isinstance(page, list) else [page])]
    return value


def latest_reviews(reviews: list[dict]) -> dict[str, dict]:
    """每个提交人只保留最新一条 review（按 submitted_at）。"""
    latest: dict[str, dict] = {}
    for review in reviews:
        login = (review.get("user") or {}).get("login") or ""
        if not login:
            continue
        current = latest.get(login.lower())
        if current is None or review.get("submitted_at", "") >= current.get("submitted_at", ""):
            latest[login.lower()] = review
    return latest


def count_valid(reviews: list[dict], head_time: str) -> int:
    total = 0
    for review in latest_reviews(reviews).values():
        user = review.get("user") or {}
        login = (user.get("login") or "").lower()
        if user.get("type") == "Bot" or login.endswith("[bot]"):
            continue
        if review.get("state") != "APPROVED":
            continue
        if review.get("submitted_at", "") < head_time:
            continue
        total += 1
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True)
    args = parser.parse_args()

    reviews = gh_json(f"repos/{args.repo}/pulls/{args.pr}/reviews?per_page=100", paginate=True) or []
    head = gh_json(f"repos/{args.repo}/pulls/{args.pr}")["head"]["sha"]
    head_time = gh_json(f"repos/{args.repo}/commits/{head}")["commit"]["committer"]["date"]
    print(count_valid(reviews, head_time))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())