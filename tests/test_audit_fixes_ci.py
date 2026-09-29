"""
2026-09 全仓审计第二轮: 门禁在 CI 中静默放行 / 误伤的回归测试。
"""
from __future__ import annotations

import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_job  # noqa: E402
import check_tested  # noqa: E402
import record_test_run  # noqa: E402
import scan_secrets  # noqa: E402
from governance_common import reason_blacklist_hit  # noqa: E402


class ReasonBlacklistTests(unittest.TestCase):
    """ASCII 黑名单按词匹配: 合法理由里的 template / wipe 不能被当成 temp / wip 拒绝。"""

    def test_ascii_words_match_on_word_boundary_only(self) -> None:
        self.assertIsNone(reason_blacklist_hit("使用 template 渲染, 已评审", ["temp", "wip", "hack"]))
        self.assertIsNone(reason_blacklist_hit("wipe cache before hackathon", ["wip", "hack"]))
        self.assertEqual(reason_blacklist_hit("temp fix", ["temp"]), "temp")
        self.assertEqual(reason_blacklist_hit("WIP: later", ["wip"]), "wip")

    def test_chinese_words_still_match_as_substring(self) -> None:
        self.assertEqual(reason_blacklist_hit("这是临时方案", ["临时"]), "临时")


class SecretScanTests(unittest.TestCase):
    def _hits(self, line: str) -> bool:
        diff = f"+++ b/app.py\n@@ -0,0 +1 @@\n+{line}\n"
        return any(name == "credential-assignment" for _, _, name, _ in scan_secrets.scan_diff(diff))

    # 假凭据在运行时拼接, 避免测试源码本身被 secret-scan 命中
    FAKE = '"' + "a" * 16 + '"'

    def test_attribute_and_subscript_assignments_are_detected(self) -> None:
        self.assertTrue(self._hits("self.token = " + self.FAKE))
        self.assertTrue(self._hits('cfg["password"] = ' + self.FAKE))

    def test_comparisons_are_not_flagged(self) -> None:
        self.assertFalse(self._hits("if password == " + self.FAKE + ":"))


class UntestedAnnotationTests(unittest.TestCase):
    def test_later_valid_annotation_is_accepted_when_first_is_expired(self) -> None:
        cfg = check_tested.load_config(None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "svc.py"
            path.write_text(
                '# risk:untested reason:"旧的外部依赖桩, 已废弃" owner:@a reviewed:2000-01-01\n'
                '# risk:untested reason:"依赖真实支付网关, 由沙箱联调覆盖" owner:@b '
                f'reviewed:{check_tested.dt.date.today().isoformat()}\n',
                encoding="utf-8",
            )
            ok, why = check_tested.has_untested_annotation(str(path), cfg)
        self.assertTrue(ok, why)


class TestPathClassificationTests(unittest.TestCase):
    def test_words_ending_in_test_are_production_code(self) -> None:
        for mod in (check_tested, check_job):
            self.assertFalse(mod._TEST_PATH_RE.search("latest.py"), mod.__name__)
            self.assertFalse(mod._TEST_PATH_RE.search("src/attest.go"), mod.__name__)
            self.assertTrue(mod._TEST_PATH_RE.search("test_orders.py"), mod.__name__)
            self.assertTrue(mod._TEST_PATH_RE.search("OrdersTests.cs"), mod.__name__)

class CheckJobCiTests(unittest.TestCase):
    """test-bypass 在 CI 中必须真正检查 MR diff, 而不是永远看空的 staged 改动。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.evidence = Path(self.tmp.name) / "evidence.jsonl"

    def _write_evidence(self, *records: dict) -> None:
        self.evidence.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")

    def _check(self, git_output: str, trailer: str | None = None):
        with mock.patch.object(check_job, "run_git", return_value=git_output) as run_git, \
                mock.patch.object(check_job, "repository_state", return_value="current"), \
                mock.patch("check_tested.read_tested_trailer", return_value=trailer):
            result = check_job.check(str(self.evidence), "origin/main", None)
        return result, run_git

    def test_diff_base_reads_mr_diff_not_staged(self) -> None:
        _, run_git = self._check("")
        args = run_git.call_args[0][0]
        self.assertNotIn("--cached", args)
        self.assertIn("origin/main...HEAD", args)

    def test_stale_failed_run_is_ignored_but_current_nonzero_exit_blocks(self) -> None:
        self._write_evidence(
            {"cmd": "pytest", "ts": "1", "failed": 3, "git_state": "old"},
            {"cmd": "dotnet test", "ts": "2", "failed": 0, "exit_code": 1, "git_state": "current"},
        )
        (errors, _), _ = self._check("M\tsrc/app.py\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("退出码 1", errors[0])

    def test_tested_trailer_fail_blocks_when_ci_has_no_local_evidence(self) -> None:
        (errors, _), _ = self._check("M\tsrc/app.py\n", trailer="fail")
        self.assertTrue(errors)


class RecordTestRunTests(unittest.TestCase):
    def test_nonzero_exit_with_partial_zero_failure_count_is_recorded_as_failure(self) -> None:
        proc = mock.Mock(returncode=1, stdout="Failed: 0, Passed: 10\n", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "e.jsonl"
            argv = ["record_test_run.py", "--evidence", str(evidence), "--", "dotnet", "test"]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(record_test_run.subprocess, "run", return_value=proc), \
                    mock.patch.object(record_test_run, "repository_state", side_effect=RuntimeError("no git")), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(record_test_run.main(), 1)
            record = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(record["failed"], -1)
        self.assertIsNone(record["git_state"])


class GitLabCiScriptTests(unittest.TestCase):
    """GitLab 以 set -e 执行 script: 裸 `cmd; EXIT_CODE=$?` 会在失败时直接退出, 结果文件写不出 fail。"""

    def setUp(self) -> None:
        self.ci = (ROOT / "ci" / "governance-ci.yml").read_text(encoding="utf-8")

    def test_exit_codes_are_captured_without_tripping_errexit(self) -> None:
        self.assertIsNone(re.search(r"^\s*EXIT_CODE=\$\?\s*$", self.ci, re.M))
        self.assertGreaterEqual(self.ci.count("&& EXIT_CODE=0 || EXIT_CODE=$?"), 8)

    def test_test_bypass_job_checks_mr_diff(self) -> None:
        job = self.ci[self.ci.index("governance:test-bypass:"):self.ci.index("governance:expired-report:")]
        self.assertIn('--diff-base "$BASE"', job)
        self.assertNotIn("git diff --cached", job)


if __name__ == "__main__":
    unittest.main()
