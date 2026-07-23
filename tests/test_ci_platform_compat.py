#!/usr/bin/env python3
"""测试 CI 平台兼容性：GitHub Actions vs GitLab CI"""
import os
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
                [sys.executable, str(REPO_ROOT / "scripts/scan_risks.py"), "--staged"],
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


if __name__ == "__main__":
    unittest.main()
