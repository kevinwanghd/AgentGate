"""
2026-09 全仓审计修复的回归测试: 每条对应一个会让门禁/飞轮静默失效的缺陷。
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

import aggregate_pending  # noqa: E402
import check_tested  # noqa: E402
import gate_decision  # noqa: E402
import governance_metrics  # noqa: E402
import lessons_review  # noqa: E402

class LessonsReviewLookupTests(unittest.TestCase):
    """审核命令按前缀查找 lesson, 歧义时必须拒绝, 否则会确认/拒绝错误的 lesson。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.pending_dir = Path(self.tmp.name)
        patcher = mock.patch.object(lessons_review, "PENDING_DIR", self.pending_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name: str, data: dict) -> None:
        import yaml

        (self.pending_dir / name).write_text(yaml.safe_dump(data), encoding="utf-8")

    def test_ambiguous_fingerprint_prefix_is_rejected(self) -> None:
        self._write("a.yml", {"id": "pl-1", "fingerprint": "abc111"})
        self._write("b.yml", {"id": "pl-2", "fingerprint": "abc222"})
        with redirect_stderr(io.StringIO()) as err:
            data, path = lessons_review._load_pending("abc")
        self.assertIsNone(data)
        self.assertIsNone(path)
        self.assertIn("匹配到多条", err.getvalue())

    def test_unique_fingerprint_prefix_resolves(self) -> None:
        self._write("a.yml", {"id": "pl-1", "fingerprint": "abc111"})
        self._write("b.yml", {"id": "pl-2", "fingerprint": "def222"})
        data, path = lessons_review._load_pending("abc")
        self.assertEqual(data["id"], "pl-1")
        self.assertEqual(path.name, "a.yml")

    def test_unknown_status_does_not_crash_stats(self) -> None:
        self._write("a.yml", {"id": "pl-1", "fingerprint": "abc", "status": "Pending"})
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            rc = lessons_review.cmd_stats(mock.Mock())
        self.assertEqual(rc, 0)
        self.assertIn("未知 status", err.getvalue())

class ReviewFieldLayoutTests(unittest.TestCase):
    """lessons_review 把审核信息写在 review.* 下, 指标与聚合必须读同一位置。"""

    def _reviewed(self) -> dict:
        return {
            "fingerprint": "fp1",
            "source_repo": "repo",
            "status": "confirmed",
            "detected_at": "2026-09-01T00:00:00Z",
            # lessons_review 写入带 +08:00 的时间, 与 detected_at 的 Z 时间混用
            "review": {"reviewer": "alice", "reviewed_at": "2026-09-03T08:00:00+08:00"},
        }

    def test_review_latency_reads_nested_review_with_mixed_timezones(self) -> None:
        self.assertEqual(governance_metrics.calculate_review_latency([self._reviewed()]), 2.0)

    def test_aggregation_tracks_latest_decision_from_nested_review(self) -> None:
        agg = aggregate_pending.AggregatedFingerprint("fp1")
        agg.add(self._reviewed())
        self.assertEqual(agg.latest_decision, "confirmed")
        self.assertEqual(agg.latest_reviewer, "alice")


class PendingWriterTests(unittest.TestCase):
    def test_module_imports_and_merge_counts_occurrences(self) -> None:
        import yaml

        import pending_writer  # 旧实现在 import 时就 TypeError

        self.assertIsInstance(pending_writer.PENDING_DIR, Path)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.yml"
            pending_writer._write_pending_yaml(
                path, {"fingerprint": "fp", "occurrence_count": 1, "repos_seen": ["r1"]})
            with mock.patch.object(pending_writer, "_get_repo_name", return_value="r2"):
                pending_writer._merge_pending(path, {}, "base")
                pending_writer._merge_pending(path, {}, "base")
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(data["occurrence_count"], 3)
        self.assertEqual(data["repos_seen"], ["r1", "r2"])

class PendingFormatRoundTripTests(unittest.TestCase):
    """pending_writer 写出的 YAML 必须能被 lessons_review 审核并通过 CI schema 校验 (同一套格式)。"""

    def test_writer_output_is_reviewable_and_schema_valid(self) -> None:
        import pending_lessons_schema
        import pending_writer

        diff = "+++ b/src/db.py\n@@ -0,0 +1 @@\n+q = \"SELECT * FROM t WHERE id=\" + uid\n"
        violation = {"file": "src/db.py", "line": 1, "type": "sql-string-concat", "desc": "SQL 拼接"}
        with tempfile.TemporaryDirectory() as tmp:
            pending_dir = Path(tmp)
            with mock.patch.object(pending_writer, "_get_repo_name", return_value="repo"), \
                    mock.patch.object(pending_writer, "_get_current_branch", return_value="feat"):
                lesson = pending_writer.create_pending_lesson(violation, diff, "origin/main", pending_dir)
            args = mock.Mock(fingerprint=lesson["fingerprint"][:12], classification="code-pattern",
                             enforcement="soft", reviewer="bob", target="", suggested_regex="")
            with mock.patch.object(lessons_review, "PENDING_DIR", pending_dir), redirect_stdout(io.StringIO()):
                self.assertEqual(lessons_review.cmd_confirm(args), 0)
                data, _ = lessons_review._load_pending(lesson["fingerprint"])
            with redirect_stdout(io.StringIO()):
                rc = pending_lessons_schema.main(["--path", str(pending_dir), "--strict"])
        self.assertEqual(data["status"], "confirmed")
        self.assertEqual(data["review"]["target_path"], "patterns/python.yml")
        self.assertEqual(rc, 0)

    def test_schema_cli_treats_missing_dir_as_normal(self) -> None:
        import pending_lessons_schema

        with redirect_stdout(io.StringIO()):
            self.assertEqual(pending_lessons_schema.main(["--path", "no/such/dir", "--strict"]), 0)


class FingerprintStructureTests(unittest.TestCase):
    """指纹只抹平命名和字面量, 不能抹平代码结构, 否则无关违规被合并成一条 lesson。"""

    def test_different_control_structures_do_not_collide(self) -> None:
        import fingerprint

        self.assertNotEqual(fingerprint.compute_fingerprint("t", "catch {}"),
                            fingerprint.compute_fingerprint("t", "if {}"))

    def test_literal_placeholders_survive_identifier_pass(self) -> None:
        import fingerprint

        self.assertEqual(fingerprint.normalize_code_for_fingerprint('x = "a" + 1'), "<VAR> = <STR> + <NUM>")

    def test_renamed_variables_still_match(self) -> None:
        import fingerprint

        self.assertEqual(fingerprint.compute_fingerprint("t", "foo = bar(1)"),
                         fingerprint.compute_fingerprint("t", "x = y(2)"))


class GatePolicyTests(unittest.TestCase):
    """PR 侧无法通过精简目标配置或伪造 evidence 来绕开门禁。"""

    def test_target_policy_missing_keys_keeps_default_protection(self) -> None:
        completed = mock.Mock(stdout="auto_merge:\n  strategy: merge\n")
        with mock.patch.object(gate_decision.subprocess, "run", return_value=completed):
            policy = gate_decision.load_policy_from_target_branch("origin/main", "governance.config.yml")
        self.assertEqual(policy["auto_merge"]["strategy"], "merge")
        self.assertIn("governance.config.yml", policy["auto_merge"]["protected_paths"])
        self.assertIn("medium", policy["auto_merge"]["required_checks_by_risk"])

    def test_required_checks_never_come_from_evidence(self) -> None:
        required = gate_decision._required_checks_for_risk({}, "medium", {})
        self.assertIn("risk-scan", required)
        self.assertIn("test-check", required)


class CheckTestedCiModeTests(unittest.TestCase):
    def test_stale_evidence_file_does_not_bypass_ci_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            diff = Path(tmp) / "change.diff"
            diff.write_text("+++ b/src/app.py\n@@ -0,0 +1 @@\n+print('x')\n", encoding="utf-8")
            evidence = Path(tmp) / "evidence.jsonl"
            evidence.write_text(json.dumps({"git_state": "old-state", "exit_code": 0}) + "\n",
                                encoding="utf-8")
            argv = ["check_tested.py", "--diff-file", str(diff), "--evidence", str(evidence), "--ci-mode"]
            out = io.StringIO()
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(check_tested, "repository_state", return_value="current"), \
                    redirect_stdout(out):
                self.assertEqual(check_tested.main(), 1)
        self.assertIn("FAIL (CI 模式)", out.getvalue())


class InstallerCompletenessTests(unittest.TestCase):
    def test_every_script_used_by_gitlab_ci_is_installed(self) -> None:
        ci = (ROOT / "ci" / "governance-ci.yml").read_text(encoding="utf-8")
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        used = set(re.findall(r"governance/scripts/([\w-]+\.(?:py|sh))", ci))
        installed = set(re.findall(r'write_file "governance/scripts/([\w-]+\.(?:py|sh))"', installer))
        self.assertTrue(used)
        self.assertEqual(used - installed, set())


if __name__ == "__main__":
    unittest.main()
