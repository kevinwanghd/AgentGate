#!/usr/bin/env python3
"""Tests for the GitHub rollout inventory and planner."""

from __future__ import annotations

import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import rollout_github


class GitHubRolloutTests(unittest.TestCase):
    def test_default_inventory_is_github_only(self):
        repos = rollout_github.load_inventory(REPO_ROOT / "rollout.repos.yml")
        self.assertEqual(
            [repo.slug for repo in repos],
            [
                "kevinwanghd/deliverhq",
                "kevinwanghd/UseGEO",
                "kevinwanghd/GoodNews_Globe",
            ],
        )
        self.assertTrue(all(repo.platform == "github" for repo in repos))
        self.assertTrue(all(repo.mode == "thin" for repo in repos))

    def test_rejects_gitlab_inventory(self):
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".yml", encoding="utf-8"
        ) as f:
            yaml.safe_dump(
                {
                    "repos": [
                        {
                            "owner": "internal",
                            "name": "private-service",
                            "platform": "gitlab",
                        }
                    ]
                },
                f,
            )
            inventory = Path(f.name)
        try:
            with self.assertRaises(ValueError):
                rollout_github.load_inventory(inventory)
        finally:
            inventory.unlink(missing_ok=True)

    def test_pr_requires_push(self):
        with self.assertRaises(SystemExit):
            rollout_github.parse_args(["--pr"])

    def test_rollout_plan_prepares_and_verifies_manifest(self):
        args = Namespace(
            python="python",
            install_script=REPO_ROOT / "install.sh",
            bash="bash",
        )
        repo = rollout_github.load_inventory(REPO_ROOT / "rollout.repos.yml")[0]
        repo_dir = Path("work") / repo.name

        prepare = rollout_github.prepare_command(args, repo_dir, "main")
        verify = rollout_github.verify_command(args, "main")

        self.assertIn("create_mr.py", prepare[1])
        self.assertIn("--prepare", prepare)
        self.assertIn("--target-branch", prepare)
        self.assertIn("origin/main", prepare)
        self.assertEqual(["python", str(REPO_ROOT / "scripts" / "agentgate.py"), "pr", "verify"], verify[:4])
        self.assertIn("origin/main", verify)

    def test_install_command_uses_login_bash_for_msys_tools(self):
        args = Namespace(
            install_script=REPO_ROOT / "install.sh",
            bash="bash",
        )
        repo = rollout_github.load_inventory(REPO_ROOT / "rollout.repos.yml")[0]
        repo_dir = Path("work") / repo.name

        command = rollout_github.install_command(args, repo, repo_dir)

        self.assertEqual(["bash", "-lc"], command[:2])
        self.assertIn("install.sh", command[2])
        self.assertIn("--platform github", command[2])


if __name__ == "__main__":
    unittest.main()
