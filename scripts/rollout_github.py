#!/usr/bin/env python3
"""Roll out AgentGate's GitHub thin entrypoint to many repositories.

The script is conservative by default: it prints the intended work and only
changes target repositories when --apply is provided.
"""

from __future__ import annotations

import argparse
import datetime as dt
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INVENTORY = REPO_ROOT / "rollout.repos.yml"
WINDOWS_GIT_BASH_CANDIDATES = (
    Path(r"C:\Program Files\Git\bin\bash.exe"),
    Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
)


@dataclass(frozen=True)
class RolloutRepo:
    owner: str
    name: str
    platform: str
    mode: str
    agents: str
    agentgate_repo: str
    agentgate_ref: str
    branch_prefix: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.slug}.git"


def run(cmd: list[str], cwd: Path | None, *, apply: bool) -> None:
    label = " ".join(cmd)
    if not apply:
        print(f"[dry-run] {label}")
        return
    print(f"[run] {label}")
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def capture(cmd: list[str], cwd: Path) -> str:
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def load_inventory(path: Path) -> list[RolloutRepo]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    defaults = data.get("defaults") or {}
    repos = data.get("repos") or []
    loaded: list[RolloutRepo] = []
    for raw in repos:
        merged: dict[str, Any] = {**defaults, **(raw or {})}
        platform = str(merged.get("platform", "github"))
        if platform != "github":
            raise ValueError(
                f"{merged.get('owner')}/{merged.get('name')} uses platform={platform}; "
                "rollout_github.py accepts only GitHub repositories"
            )
        loaded.append(
            RolloutRepo(
                owner=str(merged["owner"]),
                name=str(merged["name"]),
                platform=platform,
                mode=str(merged.get("mode", "thin")),
                agents=str(merged.get("agents", "claude")),
                agentgate_repo=str(merged.get("agentgate_repo", "kevinwanghd/AgentGate")),
                agentgate_ref=str(merged.get("agentgate_ref", "github-stable")),
                branch_prefix=str(merged.get("branch_prefix", "chore/agentgate-rollout")),
            )
        )
    return loaded


def default_branch(repo_dir: Path) -> str:
    try:
        origin_head = capture(["git", "rev-parse", "--abbrev-ref", "origin/HEAD"], repo_dir)
        if origin_head.startswith("origin/"):
            return origin_head.split("/", 1)[1]
    except subprocess.CalledProcessError:
        pass
    for candidate in ("main", "master"):
        try:
            capture(["git", "rev-parse", "--verify", f"origin/{candidate}"], repo_dir)
            return candidate
        except subprocess.CalledProcessError:
            continue
    raise RuntimeError(f"cannot determine default branch for {repo_dir}")


def ensure_clean(repo_dir: Path) -> None:
    status = capture(["git", "status", "--porcelain"], repo_dir)
    if status:
        raise RuntimeError(f"{repo_dir} has local changes; commit or stash them before rollout")


def install_command(args: argparse.Namespace, repo: RolloutRepo, repo_dir: Path) -> list[str]:
    script = shlex.quote(str(args.install_script).replace("\\", "/"))
    target = shlex.quote(str(repo_dir).replace("\\", "/"))
    command = " ".join(
        [
            script,
            target,
            "--platform",
            shlex.quote(repo.platform),
            "--mode",
            shlex.quote(repo.mode),
            "--agents",
            shlex.quote(repo.agents),
            "--agentgate-repo",
            shlex.quote(repo.agentgate_repo),
            "--agentgate-ref",
            shlex.quote(repo.agentgate_ref),
        ]
    )
    return [args.bash, "-lc", command]


def prepare_command(args: argparse.Namespace, repo_dir: Path, base_branch: str) -> list[str]:
    return [
        args.python,
        str(REPO_ROOT / "scripts" / "create_mr.py"),
        "--prepare",
        "--target-branch",
        f"origin/{base_branch}",
        "--config",
        "governance.config.yml",
        "--why",
        "为 GitHub 仓库接入 AgentGate 共享门禁，后续 AgentGate 更新时可以通过批量 rollout 重复同步。",
        "--excludes",
        "不修改业务代码、不改变 GitLab 私仓接入方式、不直接合并到 main。",
        "--tested",
        "已运行 AgentGate 安装器并生成绑定当前 diff 的 PR 描述清单；推送前会执行 agentgate.py pr verify。",
        "--risks",
        "风险点：新增 GitHub Actions 门禁可能影响 PR 合并节奏。应对：采用 thin workflow 和软启动配置；回滚方式是 revert 本次治理接入提交。",
    ]


def verify_command(args: argparse.Namespace, base_branch: str) -> list[str]:
    return [
        args.python,
        str(REPO_ROOT / "scripts" / "agentgate.py"),
        "pr",
        "verify",
        "--target-branch",
        f"origin/{base_branch}",
        "--config",
        "governance.config.yml",
    ]


def default_bash() -> str:
    if sys.platform == "win32":
        for candidate in WINDOWS_GIT_BASH_CANDIDATES:
            if candidate.exists():
                return str(candidate)
    return shutil.which("bash") or "bash"


def rollout_one(args: argparse.Namespace, repo: RolloutRepo) -> None:
    repo_dir = args.workspace / repo.name
    branch_name = args.branch or f"{repo.branch_prefix}-{dt.date.today().strftime('%Y%m%d')}"

    print(f"\n==> {repo.slug}")
    if not repo_dir.exists():
        run(["git", "clone", repo.clone_url, str(repo_dir)], None, apply=args.apply)
    elif not (repo_dir / ".git").exists():
        raise RuntimeError(f"{repo_dir} exists but is not a git repository")

    if not args.apply:
        print(f"[dry-run] would prepare branch {branch_name} in {repo_dir}")
        print(f"[dry-run] would run: {' '.join(install_command(args, repo, repo_dir))}")
        print("[dry-run] would commit installer output")
        print(
            "[dry-run] would prepare bound PR description: "
            f"{' '.join(prepare_command(args, repo_dir, 'main'))}"
        )
        print("[dry-run] would amend .agentgate/mr-description.md into the rollout commit")
        print(
            "[dry-run] would verify bound PR description before push: "
            f"{' '.join(verify_command(args, 'main'))}"
        )
        if args.push:
            print(f"[dry-run] would push {branch_name} and open PR={args.pr}")
        return

    ensure_clean(repo_dir)
    run(["git", "fetch", "origin"], repo_dir, apply=True)
    base_branch = default_branch(repo_dir)
    run(["git", "switch", base_branch], repo_dir, apply=True)
    run(["git", "pull", "--ff-only", "origin", base_branch], repo_dir, apply=True)
    ensure_clean(repo_dir)
    run(["git", "switch", "-c", branch_name], repo_dir, apply=True)
    run(install_command(args, repo, repo_dir), repo_dir, apply=True)

    status = capture(["git", "status", "--porcelain"], repo_dir)
    if not status:
        print(f"[skip] {repo.slug} already has the requested AgentGate state")
        return

    run(["git", "add", "."], repo_dir, apply=True)
    run(
        [
            "git",
            "commit",
            "-m",
            "chore: roll out AgentGate GitHub governance",
            "-m",
            "## Background\n- Add the shared AgentGate GitHub thin entrypoint.\n\n"
            "## Changes\n- Install AgentGate PR template, governance docs, local config, and GitHub workflow.\n\n"
            "## Self Test\n- Ran AgentGate installer for this repository.\n\n"
            "## Risk and Rollback\n- Low-risk governance onboarding; revert this commit to roll back.",
        ],
        repo_dir,
        apply=True,
    )
    run(prepare_command(args, repo_dir, base_branch), repo_dir, apply=True)
    run(["git", "add", ".agentgate/mr-description.md"], repo_dir, apply=True)
    run(["git", "commit", "--amend", "--no-edit"], repo_dir, apply=True)
    run(verify_command(args, base_branch), repo_dir, apply=True)

    if args.push:
        run(verify_command(args, base_branch), repo_dir, apply=True)
        run(["git", "push", "-u", "origin", branch_name], repo_dir, apply=True)
        if args.pr:
            run(
                [
                    args.gh,
                    "pr",
                    "create",
                    "--repo",
                    repo.slug,
                    "--base",
                    base_branch,
                    "--head",
                    branch_name,
                    "--title",
                    "chore: roll out AgentGate GitHub governance",
                    "--body-file",
                    ".agentgate/mr-description.md",
                ],
                repo_dir,
                apply=True,
            )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--workspace", type=Path, default=REPO_ROOT.parent / "agentgate-rollout-work")
    parser.add_argument("--install-script", type=Path, default=REPO_ROOT / "install.sh")
    parser.add_argument("--repo", action="append", help="Limit rollout to owner/name or repository name")
    parser.add_argument("--branch", help="Use a fixed branch name instead of the date-based default")
    parser.add_argument("--bash", default=default_bash())
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--gh", default=shutil.which("gh") or "gh")
    parser.add_argument("--apply", action="store_true", help="Actually clone/edit/commit repositories")
    parser.add_argument("--push", action="store_true", help="Push rollout branches after committing")
    parser.add_argument("--pr", action="store_true", help="Open GitHub PRs after pushing")
    args = parser.parse_args(argv)
    args.inventory = args.inventory.resolve()
    args.workspace = args.workspace.resolve()
    args.install_script = args.install_script.resolve()
    if args.pr and not args.push:
        parser.error("--pr requires --push")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    repos = load_inventory(args.inventory)
    if args.repo:
        wanted = set(args.repo)
        repos = [r for r in repos if r.slug in wanted or r.name in wanted]
    if not repos:
        raise SystemExit("no repositories selected")
    if not args.install_script.exists():
        raise SystemExit(f"install script not found: {args.install_script}")
    if args.apply:
        args.workspace.mkdir(parents=True, exist_ok=True)
    print(f"[rollout] inventory={args.inventory}")
    print(f"[rollout] workspace={args.workspace}")
    print(f"[rollout] mode={'apply' if args.apply else 'dry-run'}")
    for repo in repos:
        rollout_one(args, repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
