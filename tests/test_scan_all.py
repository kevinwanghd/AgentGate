from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

governance_scan_all = importlib.import_module("governance_scan_all")


def _git(repo: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


class GovernanceScanAllTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self._cwd = os.getcwd()
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        _git(self.repo, "branch", "-M", "main")
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-qm", "init")
        os.chdir(self.repo)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _pr_body(self) -> str:
        body = (
            "## 背景\n\n这是一个用于测试统一扫描入口的合规变更说明，字数足够通过中文校验。\n\n"
            "## 变更内容\n\n- 新增或修改测试用的占位文件内容。\n\n"
            "## 自测确认\n\n- 已在本地运行相关测试脚本并确认通过。\n\n"
            "AI-Usage: heavy\n"
        )
        f = self.repo / "pr_body.md"
        f.write_text(body, encoding="utf-8")
        return str(f)

    def test_all_pass_returns_zero(self) -> None:
        # 无风险的普通文本变更 → risk/secret 通过；合规 body → mr 通过
        (self.repo / "README.md").write_text("# 合规变更\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-qm", "chore: add readme")

        buf = __import__("io").StringIO()
        with redirect_stdout(buf):
            rc = governance_scan_all.main([
                "--diff-base", "HEAD~1",
                "--config", str(ROOT / "governance.config.yml"),
                "--pr-body-file", self._pr_body(),
            ])
        self.assertEqual(rc, 0, msg=buf.getvalue())

    def test_risk_failure_returns_nonzero(self) -> None:
        # 引入无注解硬风险代码 (.cs) → risk-scan 失败 → 整体非 0
        src = self.repo / "src"
        src.mkdir()
        (src / "Auth.cs").write_text(
            'bool check(string userId){ return userId == "626786582b50ab8ec08b0fa0"; }\n',
            encoding="utf-8",
        )
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-qm", "feat: auth")

        buf = __import__("io").StringIO()
        with redirect_stdout(buf):
            rc = governance_scan_all.main([
                "--diff-base", "HEAD~1",
                "--config", str(ROOT / "governance.config.yml"),
                "--pr-body-file", self._pr_body(),
            ])
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
