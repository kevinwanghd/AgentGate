"""
2026-11 第二轮全仓审计修复的回归测试。

每条对应一个已确认的缺陷（攻击场景见各 docstring），覆盖:
- check_job: 重命名绕过 / pytest 布局漏识别 / covers 语义
- pending_writer: snippet 行号偏移 / 原子写
- scan_risks: "++" 内容行误判文件头 / 测试文件正则过宽
- scan_secrets: "++" 前缀新增行漏扫 / 重复发现去重
- gate_decision: --target-ref 忽略 --config
- governance_metrics / aggregate_pending: 非 mapping 根节点 / status 缺失
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import aggregate_pending  # noqa: E402
import check_job  # noqa: E402
import gate_decision  # noqa: E402
import governance_metrics  # noqa: E402
import pending_writer  # noqa: E402
import scan_risks  # noqa: E402
import scan_secrets  # noqa: E402


class CheckJobRenameTests(unittest.TestCase):
    """重命名+改内容曾被 git -M 的 R 状态整体丢弃, test-bypass 完全静默放行。"""

    def test_rename_status_is_parsed_as_addition(self) -> None:
        # --no-renames 后重命名拆成 A/D, 新路径必须进 prod_files
        out = "R096\told/FooService.cs\tnew/BarService.cs"
        with mock.patch.object(check_job, "run_git", return_value=out):
            files = check_job.get_changed_files("origin/main")
        self.assertIn("new/BarService.cs", files)


class CheckJobPytestLayoutTests(unittest.TestCase):
    """tests/test_foo.py 是 pytest 最常见布局, 候选生成侧曾完全漏掉。"""

    def test_pytest_layout_candidate_generated(self) -> None:
        candidates = check_job.find_test_candidates("src/foo.py")
        self.assertIn("tests/test_foo.py", candidates)


class CheckJobCoversSemanticsTests(unittest.TestCase):
    """covers 记录的是生产文件, 曾拿它与测试候选比对导致真跑了测试也 FAIL。"""

    def test_covers_prod_file_satisfies_record(self) -> None:
        evidence = [{"cmd": "dotnet test", "ts": "1", "covers": ["src/FooService.cs"], "failed": 0}]
        related = ["tests/FooServiceTests.cs", "src/FooService.cs"]
        self.assertTrue(check_job.has_recent_test_record(evidence, related))


class PendingWriterSnippetTests(unittest.TestCase):
    """@@ 头行曾被计入行号, 所有行系统性偏移一行, 指纹退化为 路径:行号。"""

    def test_first_added_line_extracted_correctly(self) -> None:
        diff = (
            "diff --git a/src/db.py b/src/db.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/src/db.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+import os\n"
            "+password = 'hardcoded'\n"
        )
        snippet = pending_writer._extract_snippet(diff, "src/db.py", 1)
        self.assertEqual(snippet, "import os")
        snippet2 = pending_writer._extract_snippet(diff, "src/db.py", 2)
        self.assertEqual(snippet2, "password = 'hardcoded'")

    def test_hunk_of_next_file_not_leaked(self) -> None:
        # 行号在本文件落空时不能匹配到下一个文件的 hunk
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,0 +1 @@\n"
            "+x = 1\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -1,0 +1 @@\n"
            "+y = 2\n"
        )
        snippet = pending_writer._extract_snippet(diff, "a.py", 99)
        self.assertEqual(snippet, "a.py:99")


class PendingWriterAtomicWriteTests(unittest.TestCase):
    """非原子重写曾在中断时截断整条 lesson。"""

    def test_write_is_atomic_via_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lesson.yml"
            pending_writer._write_pending_yaml(path, {"id": "pl-1", "status": "pending"})
            self.assertTrue(path.exists())
            self.assertFalse(path.with_suffix(".yml.tmp").exists())
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(data["id"], "pl-1")


class ScanRisksDoublePlusTests(unittest.TestCase):
    """内容以 ++ 开头的新增行曾被误判为 +++ 文件头, 后续行全部丢失。"""

    def test_double_plus_content_line_kept_in_file(self) -> None:
        diff = (
            "diff --git a/app.cpp b/app.cpp\n"
            "--- a/app.cpp\n"
            "+++ b/app.cpp\n"
            "@@ -1,0 +1,3 @@\n"
            "+++ counter;\n"
            "+if (role == \"admin\") { return true; }\n"
            "+x = 1;\n"
        )
        parsed = scan_risks.parse_diff(diff)
        self.assertEqual(len(parsed["app.cpp"]), 3)
        self.assertIn("if (role == \"admin\") { return true; }", [t for _, t in parsed["app.cpp"]])


class ScanRisksTestRegexTests(unittest.TestCase):
    """latest.cs / contest.java 曾被 IGNORECASE 的 Tests?\\.[a-z]+$ 误判为测试文件。"""

    def test_latest_dot_cs_is_not_test_file(self) -> None:
        self.assertFalse(scan_risks._is_test_file("src/latest.cs"))
        self.assertFalse(scan_risks._is_test_file("src/contest.java"))

    def test_real_test_files_still_detected(self) -> None:
        self.assertTrue(scan_risks._is_test_file("tests/test_foo.py"))
        self.assertTrue(scan_risks._is_test_file("src/FooTests.cs"))
        self.assertTrue(scan_risks._is_test_file("src/foo_test.go"))


class ScanSecretsDoublePlusTests(unittest.TestCase):
    """内容以 ++ 开头的新增行曾因 not startswith("+++") 被整行丢弃, 密钥可藏其中。"""

    # 拼接构造, 避免假密钥字面量被自家 secret-scan 当真发现 (tests/** 只豁免 risk-scan)
    _FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"

    def test_double_plus_line_is_scanned(self) -> None:
        diff = (
            "diff --git a/app.js b/app.js\n"
            "--- a/app.js\n"
            "+++ b/app.js\n"
            "@@ -1,0 +1,1 @@\n"
            f"+++i; const key = \"{self._FAKE_AWS_KEY}\";\n"
        )
        lines = scan_secrets.added_lines(diff)
        self.assertEqual(len(lines), 1)
        self.assertIn(self._FAKE_AWS_KEY, lines[0][2])
        findings = scan_secrets.scan_diff(diff)
        self.assertTrue(findings)

    def test_duplicate_findings_are_deduped(self) -> None:
        secret_line = f"+const key = \"{self._FAKE_AWS_KEY}\";\n"
        diff = (
            "diff --git a/app.js b/app.js\n"
            "--- a/app.js\n"
            "+++ b/app.js\n"
            "@@ -1,0 +1,1 @@\n" + secret_line
        ) * 2
        findings = scan_secrets.scan_diff(diff)
        self.assertEqual(len(findings), 1)


class GateDecisionTargetRefConfigTests(unittest.TestCase):
    """--target-ref 路径曾硬编码 governance.config.yml, 忽略调用方 --config。"""

    def test_target_ref_uses_config_path(self) -> None:
        calls = {}

        def fake_run(cmd, **kwargs):
            calls["cmd"] = cmd
            class R:
                stdout = "auto_merge:\n  protected_paths: []\n"
            return R()

        with mock.patch.object(gate_decision.subprocess, "run", side_effect=fake_run):
            gate_decision.load_policy_from_target_branch(
                "origin/main", "policies/custom.config.yml"
            )
        self.assertIn("policies/custom.config.yml", calls["cmd"][-1])


class MetricsNonMappingRootTests(unittest.TestCase):
    """pending YAML 根节点为列表/标量时曾让整份报表 AttributeError 崩溃。"""

    def test_metrics_skips_non_mapping_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = root / ".governance" / "pending-lessons"
            pending.mkdir(parents=True)
            (pending / "bad.yml").write_text("- a\n- b\n", encoding="utf-8")
            files = governance_metrics.load_pending_files(root)
            self.assertEqual(files, [])

    def test_aggregate_skips_non_mapping_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = root / ".governance" / "pending-lessons"
            pending.mkdir(parents=True)
            (pending / "bad.yml").write_text("- a\n- b\n", encoding="utf-8")
            results = aggregate_pending.load_pending_files([root])
            self.assertEqual(results, [])


class AggregateConflictStatusTests(unittest.TestCase):
    """同 fingerprint 一条缺 status 时 sorted(str, None) 曾抛 TypeError。"""

    def test_missing_status_normalized_to_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = root / ".governance" / "pending-lessons"
            pending.mkdir(parents=True)
            import yaml

            (pending / "a.yml").write_text(
                yaml.safe_dump({"fingerprint": "fp", "pattern_type": "t", "status": "pending"}),
                encoding="utf-8",
            )
            (pending / "b.yml").write_text(
                yaml.safe_dump({"fingerprint": "fp", "pattern_type": "t", "status": "confirmed"}),
                encoding="utf-8",
            )
            (pending / "c.yml").write_text(
                yaml.safe_dump({"fingerprint": "fp", "pattern_type": "t"}),
                encoding="utf-8",
            )
            results = aggregate_pending.load_pending_files([root])
            dicts = [d for _, d in results]
            aggregations = aggregate_pending.aggregate_dicts_by_fingerprint(dicts)
            conflicts = aggregate_pending.resolve_conflicts(aggregations, strategy="confirmed")
            self.assertEqual(len(conflicts), 1)
            self.assertIn("pending", conflicts[0]["statuses"])


if __name__ == "__main__":
    unittest.main()
