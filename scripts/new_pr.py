#!/usr/bin/env python3
"""一条命令把当前分支送审：生成合规的 GitHub PR 描述并开 PR。

只做描述成型与开单，不做检查、不合并（送审时机由人决定）。复用仓库
PULL_REQUEST_TEMPLATE.md 的段落语义，缺元数据（AI-Usage trailer）时直接拒绝。

用法:
    new_pr.py [--title 标题] [--base main] [--draft] [--why ...] [--what ...]
              [--tested ...] [--risks ...] [--excludes ...] [--link ...] [--dry-run]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

TEMPLATE = """## 背景

{why}

## 变更内容

{what}

## 不包含的内容

{excludes}

## 自测确认

{tested}

## 风险与回滚

{risks}

## 关联

{link}
"""


def git(*args: str) -> str:
    run = subprocess.run(["git", *args], capture_output=True, text=True)
    if run.returncode != 0:
        raise SystemExit(f"git {args[0]} 失败：{run.stderr.strip()[-300:]}")
    return run.stdout.strip()


def has_ai_usage(message: str) -> bool:
    return any(line.strip().lower().startswith("ai-usage:") for line in message.splitlines())


def commit_lines(base: str) -> str:
    log = git("log", "--format=- %s", f"origin/{base}..HEAD")
    return log or "- （无提交）"


def build_body(why: str, what: str, excludes: str, tested: str, risks: str, link: str) -> str:
    return TEMPLATE.format(
        why=why or "见变更内容。",
        what=what or "- 见提交列表。",
        excludes=excludes or "- 无",
        tested=tested or "- [ ] 待补",
        risks=risks or "- 风险点：\n- 回滚方式：",
        link=link or "-",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", help="默认取最新 commit subject")
    parser.add_argument("--base", default="main")
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--why")
    parser.add_argument("--what")
    parser.add_argument("--tested")
    parser.add_argument("--risks")
    parser.add_argument("--excludes")
    parser.add_argument("--link", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch in ("main", "master", "HEAD"):
        raise SystemExit("当前不在功能分支上")
    if not git("rev-list", "--count", f"origin/{args.base}..HEAD"):
        raise SystemExit(f"相对 origin/{args.base} 没有提交")
    message = git("log", "-1", "--format=%B")
    if not has_ai_usage(message):
        raise SystemExit("最新提交缺 AI-Usage trailer（mr-validate 会拦），先补提交或 amend")

    title = args.title or git("log", "-1", "--format=%s")
    what = args.what or commit_lines(args.base)
    link = "\n".join(f"- {item}" for item in args.link) if args.link else "-"
    body = build_body(args.why or "", what, args.excludes or "", args.tested or "", args.risks or "", link)

    if args.dry_run:
        print(f"# {title}\n\n{body}")
        return 0

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(body)
        body_path = Path(handle.name)
    argv = ["gh", "pr", "create", "--base", args.base, "--head", branch, "--title", title, "--body-file", str(body_path)]
    if args.draft:
        argv.append("--draft")
    run = subprocess.run(argv, capture_output=True, text=True)
    body_path.unlink(missing_ok=True)
    if run.returncode != 0:
        raise SystemExit(f"gh pr create 失败：{run.stderr.strip()[-500:]}")
    print(run.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())