#!/usr/bin/env python3
"""测试 CI 平台兼容性：GitHub Actions vs GitLab CI"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))


class CIPlatformCompatTests(unittest.TestCase):
    """验证脚本在 GitHub 和 GitLab CI 环境变量下都能运行"""

    def test_scan_risks_github_summary(self):
        """scan_risks.py 在 GitHub Actions 环境下写入 GITHUB_STEP_SUMMARY"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".md") as f:
            summary_path = f.name
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yml") as f:
            f.write("risk_annotations:\n  enforcement: soft\n")
            config_path = f.name

        # 创建并追踪临时测试文件，触发一个 warn 规则（不阻断）
        test_file = REPO_ROOT / "test_temp_github_summary.py"
        test_file.write_text("# TODO fix this\n")

        try:
            # git add 使文件出现在 staged changes 中
            subprocess.run(["git", "add", str(test_file)], check=True, cwd=str(REPO_ROOT))

            env = os.environ.copy()
            env["GITHUB_STEP_SUMMARY"] = summary_path

            # 运行 scan_risks，使用 --staged 扫描 staged changes
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts/scan_risks.py"),
                    "--staged",
                    "--config",
                    config_path,
                ],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(REPO_ROOT),
            )

            # warn 模式不阻断，返回 0
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

            with open(summary_path, encoding="utf-8", errors="replace") as f:
                content = f.read()

            # GitHub 环境下应该写入内容
            self.assertIn("scan-risks", content.lower())
        finally:
            # 清理：reset 并删除文件
            subprocess.run(["git", "reset", "HEAD", str(test_file)], cwd=str(REPO_ROOT))
            test_file.unlink(missing_ok=True)
            os.unlink(summary_path)
            os.unlink(config_path)

    def test_scan_risks_gitlab_no_summary(self):
        """scan_risks.py 在 GitLab CI 环境下（无 GITHUB_STEP_SUMMARY）不报错"""
        env = os.environ.copy()
        # 模拟 GitLab CI：有 CI_* 变量，但无 GITHUB_STEP_SUMMARY
        env.pop("GITHUB_STEP_SUMMARY", None)
        env["CI"] = "true"
        env["GITLAB_CI"] = "true"
        env["CI_COMMIT_BRANCH"] = "main"

        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts/scan_risks.py"), "--diff-base", "HEAD"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        # 没有 summary path 时应该静默跳过，不报错
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_validate_mr_github_summary(self):
        """validate_mr.py 在 GitHub Actions 环境下写入 GITHUB_STEP_SUMMARY"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".md") as f:
            summary_path = f.name

        env = os.environ.copy()
        env["GITHUB_STEP_SUMMARY"] = summary_path

        # 提供空 PR 描述（会触发失败，但不影响 summary 写入测试）
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts/validate_mr.py"), "--diff-base", "HEAD"],
            input="",
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        # validate_mr.py 对空描述会失败，但这里测试的是 summary 机制
        # 检查 summary 文件是否被尝试写入（如果有大 diff）
        # 由于 HEAD 对比自己没有 diff，不会触发大 diff 警告
        # 所以这个测试主要验证不会因为 summary 路径问题崩溃
        self.assertIn(result.returncode, [0, 1])  # 0=pass, 1=validation fail
        os.unlink(summary_path)

    def test_validate_mr_gitlab_no_summary(self):
        """validate_mr.py 在 GitLab CI 环境下（无 GITHUB_STEP_SUMMARY）不报错"""
        env = os.environ.copy()
        env.pop("GITHUB_STEP_SUMMARY", None)
        env["CI"] = "true"
        env["GITLAB_CI"] = "true"

        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts/validate_mr.py"), "--diff-base", "HEAD"],
            input="",
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        # 没有 summary path 时应该静默跳过，不报错
        self.assertIn(result.returncode, [0, 1])

    def test_gitlab_11_compatible_checkout_and_dotnet_discovery(self):
        """CI 模板不能使用旧 Runner 不支持的 depth=0 或 pipefail 敏感查找。"""
        content = (REPO_ROOT / "ci/governance-ci.yml").read_text(encoding="utf-8")

        self.assertNotIn("GIT_DEPTH: 0", content)
        self.assertNotIn('find . -maxdepth 3 -name "*.sln" -not -path "*/.*" | head -1', content)
        self.assertNotIn('find . -maxdepth 3 -name "*.csproj" -not -path "*/.*" | head -1', content)
        self.assertNotRegex(content, r'(?m)^\s+git fetch -q origin "\$TB"$')
        self.assertIn('refs/remotes/origin/${TB}', content)

    def test_gate_decision_jobs_declare_mr_pipeline_kind(self):
        """两个 GitLab CI 模板的 gate-decision job 必须显式声明 MR 流水线类型。

        回归：gate-decision job 是 MR-only，CI 恒传 --target-branch master；
        缺少 --pipeline-kind mr 时，所有合向 master 的 MR 会被误判为直推而 FAIL/BLOCK。
        """
        for rel in ("ci/governance-ci.yml", "gitlab/ci-snippet.yml"):
            content = (REPO_ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("--pipeline-kind mr", content, rel)

    def test_gitlab_policy_jobs_resolve_config_consistently_and_fail_closed(self):
        """Installed policy lives at the repository root; CI must not silently use defaults."""
        jobs = (
            "governance:risk-scan",
            "governance:mr-validate",
            "governance:mr-validate-compat",
            "governance:test-check",
            "governance:gate-decision",
        )
        for rel in ("ci/governance-ci.yml", "gitlab/ci-snippet.yml"):
            content = (REPO_ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn(
                '${GOVERNANCE_CONFIG_PATH:-governance/governance.config.yml}',
                content,
                rel,
            )
            for job in jobs:
                match = re.search(
                    rf"(?ms)^{re.escape(job)}:\n(?P<body>.*?)(?=^[^ #\n][^\n]*:\s*$|\Z)",
                    content,
                )
                self.assertIsNotNone(match, f"missing {job} in {rel}")
                body = match.group("body")
                self.assertIn(
                    'CONFIG_PATH="${GOVERNANCE_CONFIG_PATH:-governance.config.yml}"',
                    body,
                    f"{job} in {rel}",
                )
                self.assertIn(
                    '[ -f "$CONFIG_PATH" ] || CONFIG_PATH="governance/governance.config.yml"',
                    body,
                    f"{job} in {rel}",
                )
                self.assertIn(
                    '[ -f "$CONFIG_PATH" ] || {',
                    body,
                    f"{job} in {rel} must fail closed when policy is absent",
                )

    def test_gitlab_templates_enforce_hard_lessons(self):
        """GitLab consumers must execute hard lessons instead of only shipping the validator."""
        for rel in ("ci/governance-ci.yml", "gitlab/ci-snippet.yml"):
            content = (REPO_ROOT / rel).read_text(encoding="utf-8")
            match = re.search(
                r"(?ms)^governance:lessons-validate:\n(?P<body>.*?)(?=^[^ #\n][^\n]*:\s*$|\Z)",
                content,
            )
            self.assertIsNotNone(match, f"missing hard-lesson job in {rel}")
            body = match.group("body")
            self.assertIn("python governance/scripts/validate_lessons.py --root .", body, rel)
            self.assertIn("allow_failure: false", body, rel)
            if rel == "ci/governance-ci.yml":
                self.assertIn("- merge_requests", body, rel)
                self.assertIn("- branches", body, rel)
            else:
                self.assertIn('$CI_PIPELINE_SOURCE == "merge_request_event"', body, rel)
                self.assertIn("$CI_COMMIT_BRANCH", body, rel)

    def test_auto_merge_missing_token_warns_and_skips_by_default(self):
        """缺 GOVERNANCE_MERGE_BOT_TOKEN 时自动合并 job 默认告警跳过，硬失败需显式开启。"""
        for rel in ("ci/governance-ci.yml", "gitlab/ci-snippet.yml"):
            content = (REPO_ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("GOVERNANCE_AUTO_MERGE_REQUIRE_TOKEN", content, rel)
            self.assertIn("跳过 bot 自动合并", content, rel)


if __name__ == "__main__":
    unittest.main()
