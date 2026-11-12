"""create_mr.py 审计修复的回归测试 (2026-09): 每条用例说明该缺陷的实际危害。"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import create_mr  # noqa: E402


def _git(cwd: str, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

# --- repo fixture ---
class _TempRepo(unittest.TestCase):
    """master 上有 base 提交、feature 分支上改动的临时仓库; 测试期间 cwd 在仓库根。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        _git(self.repo, "init", "-q", "-b", "master")
        _git(self.repo, "config", "user.email", "t@example.com")
        _git(self.repo, "config", "user.name", "t")
        _git(self.repo, "config", "core.autocrlf", "false")
        Path(self.repo, "a.txt").write_text("a\n", encoding="utf-8")
        Path(self.repo, "sub").mkdir()
        Path(self.repo, "sub", "s.txt").write_text("s\n", encoding="utf-8")
        self.commit_all("base")
        _git(self.repo, "checkout", "-qb", "feature")
        self._cwd = os.getcwd()
        os.chdir(self.repo)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def commit_all(self, msg: str = "change") -> None:
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", msg)

# --- tests ---
class NumstatPathTests(_TempRepo):
    def test_non_ascii_and_renamed_paths_still_trigger_risk(self) -> None:
        # 旧实现: 非 ASCII 路径被转义成 "ci/\351..." 且重命名显示为 "a => b",
        # 敏感/schema 匹配全部失效, 触及 ci/ 与 SQL 的 MR 被评为"低风险"。
        Path(self.repo, "ci").mkdir()
        Path(self.repo, "ci", "配置.yml").write_text("x: 1\n", encoding="utf-8")
        Path(self.repo, "db").mkdir()
        os.replace(Path(self.repo, "a.txt"), Path(self.repo, "db", "新.sql"))
        self.commit_all()

        rows = create_mr.numstat("master")
        paths = {p for _, _, p in rows}
        self.assertTrue({"ci/配置.yml", "db/新.sql", "a.txt"} <= paths, paths)
        risk = create_mr.assess_risk(rows, create_mr.DEFAULT_CONFIG)
        self.assertIn("ci/配置.yml", risk)
        self.assertIn("db/新.sql", risk)
        self.assertIn("ci/配置.yml", create_mr.changed_paths("master"))


class DiffFingerprintTests(_TempRepo):
    def test_fingerprint_is_same_from_subdirectory(self) -> None:
        # 旧实现 pathspec "." 在子目录只对子树取指纹, 根目录改动可绕过绑定校验。
        Path(self.repo, "a.txt").write_text("changed\n", encoding="utf-8")
        Path(self.repo, "sub", "s.txt").write_text("changed\n", encoding="utf-8")
        self.commit_all()
        manifest = ".agentgate/mr-description.md"
        at_root = create_mr.diff_fingerprint("master", manifest)
        os.chdir(Path(self.repo, "sub"))
        self.assertEqual(at_root, create_mr.diff_fingerprint("master", manifest))

# --- tests2 ---
class PrepareDryRunTests(_TempRepo):
    def test_prepare_dry_run_does_not_write_unvalidated_manifest(self) -> None:
        # dry-run 跳过本地校验; 若仍落盘绑定清单, 未校验描述会被当作已 prepare 产物提交。
        Path(self.repo, "a.txt").write_text("changed\n", encoding="utf-8")
        self.commit_all("fix: change")
        argv = ["create_mr.py", "--prepare", "--dry-run", "--why", "x",
                "--target-branch", "master"]
        out = io.StringIO()
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
            rc = create_mr.main()
        self.assertEqual(0, rc)
        self.assertFalse(Path(self.repo, ".agentgate", "mr-description.md").exists())
        self.assertIn("未写入", out.getvalue())


class DefaultPatternTests(unittest.TestCase):
    def test_nested_migrations_and_charts_are_detected(self) -> None:
        # 新路径语义下 "migrations/**"/"charts*/" 只锚定仓库根, 子目录迁移与 Helm chart 漏报。
        rows = [(1, 0, "services/api/migrations/001.sql"),
                (1, 0, "deploy/charts-prod/values.yaml")]
        risk = create_mr.assess_risk(rows, create_mr.DEFAULT_CONFIG)
        self.assertIn("高敏路径: deploy/charts-prod/values.yaml", risk)
        self.assertIn("schema 变更: services/api/migrations/001.sql", risk)


class GlabSubmitTests(unittest.TestCase):
    def test_command_line_too_long_returns_failure_instead_of_crashing(self) -> None:
        # glab 只能内联描述; Windows 32K 命令行上限会抛 OSError, 之前直接崩溃不走回退。
        err = OSError(206, "The filename or extension is too long")
        with mock.patch.object(create_mr.subprocess, "run", side_effect=err), \
                mock.patch("sys.stderr", io.StringIO()):
            rc = create_mr.submit_mr("t", "x" * 40000, "main", "glab")
        self.assertEqual(1, rc)

# --- tests3 ---
class EditorTests(unittest.TestCase):
    def test_editor_with_arguments_is_split(self) -> None:
        # EDITOR="code --wait" 曾被当成单个可执行文件名, 常见配置直接失败。
        done = mock.Mock(returncode=0)
        with mock.patch.dict(os.environ, {"EDITOR": "code --wait"}), \
                mock.patch.object(create_mr.shutil, "which", return_value=None), \
                mock.patch.object(create_mr.subprocess, "run", return_value=done) as run:
            self.assertEqual("body", create_mr.edit_description("body"))
        self.assertEqual(["code", "--wait"], run.call_args.args[0][:2])

    def test_missing_or_failing_editor_reports_error(self) -> None:
        # 编辑器缺失 (Windows 无 vi) 或非 0 退出应给出清晰错误, 而不是抛栈。
        for err in (FileNotFoundError("vi"), subprocess.CalledProcessError(1, "vi")):
            with mock.patch.dict(os.environ, {"EDITOR": "vi"}), \
                    mock.patch.object(create_mr.subprocess, "run", side_effect=err), \
                    mock.patch("sys.stderr", io.StringIO()) as errout:
                self.assertIsNone(create_mr.edit_description("body"))
            self.assertIn("EDITOR", errout.getvalue())


class PreflightTestCommandTests(unittest.TestCase):
    def _args(self) -> mock.Mock:
        return mock.Mock(skip_tests=False, preflight_test_command="npm test")

    def test_command_resolved_via_which(self) -> None:
        # Windows 上 npm 是 npm.cmd, 不经 shell 直接执行 "npm" 会 FileNotFoundError。
        done = mock.Mock(returncode=0)
        with mock.patch.object(create_mr.shutil, "which", return_value="C:/n/npm.cmd"), \
                mock.patch.object(create_mr.subprocess, "run", return_value=done) as run, \
                mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(0, create_mr.run_preflight_tests(self._args(), {}))
        self.assertEqual(["C:/n/npm.cmd", "test"], run.call_args.args[0])

    def test_missing_command_is_reported_not_raised(self) -> None:
        with mock.patch.object(create_mr.shutil, "which", return_value=None), \
                mock.patch.object(create_mr.subprocess, "run",
                                  side_effect=FileNotFoundError("npm")), \
                mock.patch("sys.stderr", io.StringIO()) as errout:
            self.assertEqual(2, create_mr.run_preflight_tests(self._args(), {}))
        self.assertIn("无法执行测试命令", errout.getvalue())

# --- tests4 ---
_GITLAB_ENV = ("AGENTGATE_GITLAB_TOKEN", "GITLAB_TOKEN", "GLAB_TOKEN", "PRIVATE_TOKEN",
               "GOVERNANCE_MR_VALIDATE_TOKEN", "GOVERNANCE_MERGE_BOT_TOKEN",
               "AGENTGATE_GITLAB_URL", "AGENTGATE_GITLAB_PROJECT_ID", "CI_SERVER_URL",
               "CI_PROJECT_ID", "CI_PROJECT_PATH")


def _fallback_args(**kw) -> mock.Mock:
    base = dict(gitlab_api=False, gitlab_url=None, gitlab_project_id=None,
                gitlab_token=None, source_branch="feat/x", target_branch="main",
                remove_source_branch=False, config=None)
    base.update(kw)
    return mock.Mock(**base)


class BrowserFallbackUrlTests(unittest.TestCase):
    def _open(self, env: dict, description: str) -> str:
        full_env = {**{k: "" for k in _GITLAB_ENV}, **env}
        with mock.patch.dict(os.environ, full_env), \
                mock.patch.object(create_mr, "_create_mr_config", return_value={}), \
                mock.patch.object(create_mr.webbrowser, "open", return_value=True) as br, \
                mock.patch("sys.stderr", io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            create_mr.open_gitlab_mr_fallback("t", description, _fallback_args())
        return br.call_args.args[0]

    def test_numeric_project_id_uses_project_path_for_web_url(self) -> None:
        # https://gitlab/123/merge_requests/new 是无效页面; 浏览器 URL 需要 namespace 路径。
        url = self._open({"AGENTGATE_GITLAB_URL": "https://gl.example.com",
                          "AGENTGATE_GITLAB_PROJECT_ID": "123",
                          "CI_PROJECT_PATH": "group/proj"}, "desc")
        self.assertTrue(url.startswith("https://gl.example.com/group/proj/merge_requests/new?"))

    def test_long_description_is_not_put_in_url(self) -> None:
        # 超长 query 会被服务端 414 拒绝, 用户连预填页都打不开。
        url = self._open({"AGENTGATE_GITLAB_URL": "https://gl.example.com",
                          "AGENTGATE_GITLAB_PROJECT_ID": "group/proj"}, "长" * 5000)
        self.assertLessEqual(len(url), create_mr.MAX_PREFILL_URL_LENGTH)

# --- tests5 ---
class SubmitRoutingTests(unittest.TestCase):
    def test_config_gitlab_settings_enable_auto_api(self) -> None:
        # _require_gitlab_api_args 读 config 的 gitlab_url/gitlab_project_id, 自动检测却不读,
        # 导致只在 config 里配置的仓库永远不会走 API。
        cfg = {"gitlab_url": "https://gl.example.com", "gitlab_project_id": "group/proj"}
        env = {**{k: "" for k in _GITLAB_ENV}, "AGENTGATE_GITLAB_TOKEN": "tok"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(create_mr, "_create_mr_config", return_value=cfg), \
                mock.patch.object(create_mr, "submit_gitlab_api", return_value=0) as api, \
                mock.patch.object(create_mr, "detect_cli") as detect, \
                mock.patch("sys.stderr", io.StringIO()):
            rc = create_mr._submit_with_fallback("t", "d", _fallback_args())
        self.assertEqual(0, rc)
        api.assert_called_once()
        detect.assert_not_called()

    def test_gh_failure_gives_github_instructions(self) -> None:
        # GitHub 仓库 gh 失败时输出 GitLab 预填页/手工复制到 GitLab 的指引是误导。
        env = {k: "" for k in _GITLAB_ENV}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(create_mr, "_create_mr_config", return_value={}), \
                mock.patch.object(create_mr, "detect_cli", return_value="gh"), \
                mock.patch.object(create_mr, "submit_mr", return_value=1), \
                mock.patch.object(create_mr, "open_gitlab_mr_fallback") as gitlab_fb, \
                mock.patch("sys.stderr", io.StringIO()) as errout, \
                contextlib.redirect_stdout(io.StringIO()):
            rc = create_mr._submit_with_fallback("t", "d", _fallback_args())
        self.assertEqual(1, rc)
        gitlab_fb.assert_not_called()
        self.assertIn("gh pr create", errout.getvalue())
        self.assertNotIn("GitLab", errout.getvalue())


class TargetBranchDefaultTests(unittest.TestCase):
    def test_uses_remote_default_branch(self) -> None:
        # 硬编码 master: main 为默认分支的仓库会对着不存在/错误的 base 计算 diff。
        ok = mock.Mock(returncode=0, stdout="origin/main\n")
        with mock.patch.object(create_mr.subprocess, "run", return_value=ok):
            self.assertEqual("main", create_mr.default_target_branch())
        missing = mock.Mock(returncode=1, stdout="")
        with mock.patch.object(create_mr.subprocess, "run", return_value=missing):
            self.assertEqual("master", create_mr.default_target_branch())


class DeadCodeTests(unittest.TestCase):
    def test_unused_helpers_removed(self) -> None:
        # 未使用的 latest_commit_body / 可选 yaml 导入会误导维护者以为存在第二套配置解析路径。
        self.assertFalse(hasattr(create_mr, "latest_commit_body"))
        self.assertFalse(hasattr(create_mr, "_HAS_YAML"))


if __name__ == "__main__":
    unittest.main()
