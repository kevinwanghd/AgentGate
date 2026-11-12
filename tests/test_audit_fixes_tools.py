"""
2026-09 审计修复回归测试 (工具类脚本): collect_ai_usage / report_expired / validate_yaml / validate_lessons。
每条测试对应一个会让 trailer 失真、报表崩溃或门禁静默放行的缺陷。
"""
from __future__ import annotations

import gc
import io
import os
import subprocess
import sys
import tempfile
import unittest
import warnings
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import collect_ai_usage  # noqa: E402
import report_expired  # noqa: E402
import validate_lessons  # noqa: E402
import validate_yaml  # noqa: E402


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(cwd: Path) -> None:
    _git(cwd, "init", "-q")
    _git(cwd, "config", "user.email", "t@t")
    _git(cwd, "config", "user.name", "t")
    _git(cwd, "config", "commit.gpgsign", "false")


class _Chdir:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self):
        self.old = os.getcwd()
        os.chdir(self.path)

    def __exit__(self, *exc):
        os.chdir(self.old)


class CollectAiUsageAggregateTests(unittest.TestCase):
    def test_post_commit_hook_lines_are_not_ai_lines(self) -> None:
        # hook 记录的是整个 commit 的 diff, 若计入 AI 行, 5 行 AI 改动会被夸大成 heavy
        changed = {"src/a.py": 100}
        evidence = [
            {"tool": "auto", "model": "unknown", "file": "src/a.py",
             "added": 100, "removed": 0, "source": "post-commit-hook"},
            {"tool": "claude-code", "file": "src/a.py", "added": 5, "removed": 0},
        ]
        agg = collect_ai_usage.aggregate(evidence, changed)
        self.assertEqual(5, agg["ai_lines"])
        self.assertEqual("light", collect_ai_usage.classify(agg))

    def test_hook_only_evidence_is_used_not_graded(self) -> None:
        # 仅 hook 记录时无可信 AI 行数, 按文档降级为 used, 不能伪造比例
        agg = collect_ai_usage.aggregate(
            [{"tool": "auto", "file": "a.py", "added": 10, "removed": 0,
              "source": "post-commit-hook"}],
            {"a.py": 10},
        )
        self.assertEqual("used", collect_ai_usage.classify(agg))

    def test_stale_evidence_for_unchanged_files_is_ignored(self) -> None:
        # 历史上别的文件的证据不能让本次提交带上 AI-Tools 或被判 used
        evidence = [
            {"tool": "claude-code", "model": "opus", "file": "old.py", "added": 50},
            {"tool": "cursor", "file": "old.py"},
        ]
        agg = collect_ai_usage.aggregate(evidence, {"new.py": 10})
        self.assertEqual([], agg["tools"])
        self.assertEqual([], agg["models"])
        self.assertFalse(agg["has_imprecise_tool"])
        self.assertEqual("none", collect_ai_usage.classify(agg))

    def test_fileless_session_marker_still_counts_as_used(self) -> None:
        # 文档约定补全类工具可只写会话级标记 (无 file), 该行为保持
        agg = collect_ai_usage.aggregate([{"tool": "cursor"}], {"a.py": 3})
        self.assertEqual("used", collect_ai_usage.classify(agg))

    def test_windows_and_dot_prefixed_paths_match_git_paths(self) -> None:
        # Windows agent 写 src\\Foo.cs, git numstat 永远是 src/Foo.cs; 不归一化则 AI 行恒为 0
        changed = {"src/Foo.cs": 10, "src/Bar.cs": 10}
        evidence = [
            {"tool": "claude-code", "file": "src\\Foo.cs", "added": 8, "removed": 0},
            {"tool": "claude-code", "file": "./src/Bar.cs", "added": 4, "removed": 0},
        ]
        agg = collect_ai_usage.aggregate(evidence, changed)
        self.assertEqual({"src/Foo.cs": 8, "src/Bar.cs": 4}, agg["ai_files"])


class CollectAiUsageGitTests(unittest.TestCase):
    def test_root_commit_diff_does_not_raise(self) -> None:
        # 仓库第一次提交没有 HEAD~1; 以前抛 CalledProcessError, pre-push 会被阻断
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            _init_repo(repo)
            (repo / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
            _git(repo, "add", "a.py")
            _git(repo, "commit", "-qm", "root")
            with _Chdir(repo):
                self.assertEqual({"a.py": 2}, collect_ai_usage.diff_numstat(None, False))
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "collect_ai_usage.py"), "--pre-push"],
                cwd=repo, capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertIn("AI-Usage:", proc.stdout)

    def test_renamed_file_is_counted(self) -> None:
        # 重命名在 numstat 里是 {a.py => b.py}, 扩展名解析成 ".py}" 导致该文件漏统计
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            _init_repo(repo)
            body = "".join(f"l{i}\n" for i in range(20))
            (repo / "a.py").write_text(body, encoding="utf-8")
            _git(repo, "add", "a.py")
            _git(repo, "commit", "-qm", "base")
            _git(repo, "mv", "a.py", "b.py")
            (repo / "b.py").write_text(body + "extra\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "rename")
            with _Chdir(repo):
                changed = collect_ai_usage.diff_numstat(None, False)
            self.assertIn("b.py", changed)
            self.assertFalse(any("=>" in p for p in changed))


class ReportExpiredTests(unittest.TestCase):
    def test_inline_annotation_does_not_span_lines(self) -> None:
        # 残缺的 risk: 行不能借下一条注解的字段拼成一条记录, 否则行号/类型报错且真注解被吞
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "a.py"
            src.write_text(
                "# risk: auth-bypass TODO\n"
                '# risk:secret-in-code reason:"fixture key for local tests" '
                "owner:@qa reviewed:2026-01-01\n",
                encoding="utf-8",
            )
            records = report_expired.scan_file(str(src))
        self.assertEqual([(2, "secret-in-code")], [(r["line"], r["type"]) for r in records])
        self.assertEqual("@qa", records[0]["owner"])

    def test_scan_file_closes_file_handle(self) -> None:
        # 全仓扫描逐文件打开, 句柄泄漏在 Windows 上会锁文件并触发 ResourceWarning
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "a.py"
            src.write_text("x = 1\n", encoding="utf-8")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                report_expired.scan_file(str(src))
                gc.collect()
        self.assertEqual([], [w for w in caught if issubclass(w.category, ResourceWarning)])


class ValidateYamlTests(unittest.TestCase):
    def test_multi_document_yaml_is_valid(self) -> None:
        # --- 分隔的多文档是合法 YAML (k8s 清单常见), 误报会让 CI 无故失败
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "multi.yml"
            path.write_text("a: 1\n---\nb: 2\n", encoding="utf-8")
            self.assertEqual((True, None), validate_yaml.validate_file(path))

    def test_ci_mode_fails_when_nothing_was_checked(self) -> None:
        # CI 在错误目录运行时什么都没检查却返回 0, 语法门禁会静默失效
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_yaml.py"), "--ci"],
                cwd=d, capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
        self.assertEqual(1, proc.returncode)
        self.assertIn("governance.config.yml", proc.stderr)


def _lesson_yaml(lesson_id: str, enforcement: str) -> str:
    return (
        "version: agentgate.io/lessons/v1\n"
        "lessons:\n"
        f"  - id: {lesson_id}\n"
        f"    enforcement: {enforcement}\n"
        "    applies_to: [ci]\n"
        "    trigger: t\n    risk: r\n    fix: f\n    regression: g\n"
    )


class ValidateLessonsTests(unittest.TestCase):
    def test_broken_file_and_unreadable_target_are_reported_not_raised(self) -> None:
        # 一个坏文件 / 检查目标缺失以前抛 traceback, 其余 lessons 全部没被校验
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bad = root / "bad.yml"
            bad.write_text("lessons: [unclosed\n", encoding="utf-8")
            good = root / "good.yml"
            good.write_text(
                _lesson_yaml("gitlab_legacy.job_timeout_unsupported", "hard"), encoding="utf-8"
            )
            out = io.StringIO()
            with redirect_stdout(out):
                rc = validate_lessons.main(["--root", d, str(bad), str(good)])
        self.assertEqual(1, rc)
        text = out.getvalue()
        self.assertIn("bad.yml: cannot load lesson file", text)
        self.assertIn("gitlab_legacy.job_timeout_unsupported check could not read", text)

    def test_error_names_the_file_actually_checked(self) -> None:
        # 只有 governance/ci-snippet.yml 时报错却指向 ci/governance-ci.yml, 使用者会去改错文件
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            snippet = root / "governance" / "ci-snippet.yml"
            snippet.parent.mkdir(parents=True)
            snippet.write_text("job:\n  timeout: 5m\n  needs: [a]\n", encoding="utf-8")
            errors: list[str] = []
            validate_lessons.check_gitlab_job_timeout_unsupported(root, errors)
            validate_lessons.check_gitlab_modern_schema_unsupported(root, errors)
        self.assertEqual(2, len(errors))
        for err in errors:
            self.assertIn("governance/ci-snippet.yml", err)
            self.assertNotIn("ci/governance-ci.yml", err)


if __name__ == "__main__":
    unittest.main()
