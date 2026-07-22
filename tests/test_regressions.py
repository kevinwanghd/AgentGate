from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

check_tested = importlib.import_module("check_tested")
create_mr = importlib.import_module("create_mr")
scan_risks = importlib.import_module("scan_risks")
validate_mr = importlib.import_module("validate_mr")


class ConfigFailureTests(unittest.TestCase):
    def test_explicit_missing_config_is_fatal(self) -> None:
        with self.assertRaises(Exception):
            scan_risks.load_config("definitely-missing.yml")

    def test_invalid_config_is_fatal(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
            f.write("risk_annotations: [not-a-mapping]\n")
            path = f.name
        try:
            with self.assertRaises(Exception):
                scan_risks.load_config(path)
        finally:
            os.unlink(path)

    def test_invalid_enforcement_is_rejected(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
            f.write("metadata:\n  enforcement: maybe\n")
            path = f.name
        try:
            with self.assertRaises(Exception):
                validate_mr.load_config(path)
        finally:
            os.unlink(path)

    def test_invalid_custom_regex_is_fatal_in_hard_mode(self) -> None:
        cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        cfg["risk_annotations"]["enforcement"] = "hard"
        cfg["risk_annotations"]["custom_patterns"] = [
            {"type": "broken", "regex": "(", "desc": "broken rule"}
        ]
        with self.assertRaises(scan_risks.ConfigError):
            scan_risks.build_custom_patterns(cfg)

    def test_invalid_custom_regex_returns_config_exit_code(self) -> None:
        cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        cfg["risk_annotations"]["enforcement"] = "hard"
        cfg["risk_annotations"]["custom_patterns"] = [
            {"type": "broken", "regex": "(", "desc": "broken rule"}
        ]
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
            f.write("+++ b/app.py\n@@ -0,0 +1 @@\n+print('x')\n")
            diff_path = f.name
        try:
            with mock.patch.object(scan_risks, "load_config", return_value=cfg), \
                    mock.patch.object(sys, "argv", ["scan_risks.py", "--diff-file", diff_path]):
                self.assertEqual(2, scan_risks.main())
        finally:
            os.unlink(diff_path)

    def test_invalid_custom_regex_is_skipped_in_soft_mode(self) -> None:
        cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        cfg["risk_annotations"]["enforcement"] = "soft"
        cfg["risk_annotations"]["custom_patterns"] = [
            {"type": "broken", "regex": "(", "desc": "broken rule"}
        ]
        self.assertEqual([], scan_risks.build_custom_patterns(cfg))


class RiskAnnotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        self.cfg["risk_annotations"]["enforcement"] = "hard"

    def test_each_risk_on_a_line_requires_its_own_annotation(self) -> None:
        today = dt.date.today().isoformat()
        lines = [
            f'// risk:auth-bypass reason:"approved internal identity comparison" owner:@sec reviewed:{today}',
            # risk:auth-bypass reason:"regression fixture for multiple risk coverage" owner:@sec reviewed:2026-07-11
            # risk:magic-id reason:"fixed object identifier used only by scanner regression" owner:@sec reviewed:2026-07-11
            'if (userId == "626786582b50ab8ec08b0fa0") return true;',
        ]
        ok, problems = scan_risks.find_annotation(
            lines, 2, {"auth-bypass", "magic-id"}, self.cfg
        )
        self.assertFalse(ok)
        self.assertTrue(any("magic-id" in p for p in problems))

    def test_test_removal_rejects_stale_review_date(self) -> None:
        old = (dt.date.today() - dt.timedelta(days=999)).isoformat()
        diff = (
            "-def test_payment():\n"
            f'+# risk:test-removal reason:"obsolete duplicate payment scenario" owner:@qa reviewed:{old}\n'
        )
        problems = scan_risks.check_test_removal(diff, self.cfg)
        self.assertTrue(problems)
        self.assertIn("过期", problems[0])

    def test_each_removed_test_requires_an_annotation(self) -> None:
        today = dt.date.today().isoformat()
        diff = (
            "-def test_payment():\n"
            "-def test_refund():\n"
            f'+# risk:test-removal reason:"duplicate payment scenario is obsolete" '
            f"owner:@qa reviewed:{today}\n"
        )
        problems = scan_risks.check_test_removal(diff, self.cfg)
        self.assertEqual(1, len(problems))
        self.assertIn("只有 1 条", problems[0])

    def test_multiline_empty_catch_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "service.cs"
            source.write_text("catch (Exception)\n{\n}\n", encoding="utf-8")
            diff = (
                f"+++ b/{source.as_posix()}\n"
                "@@ -0,0 +1,3 @@\n"
                "+catch (Exception)\n+{\n+}\n"
            )
            violations = scan_risks.scan(diff, self.cfg)
        self.assertTrue(any(v["type"] == "swallowed-exception" for v in violations))


class EvidenceBindingTests(unittest.TestCase):
    def test_failed_trailer_cannot_be_hidden_by_pass(self) -> None:
        completed = mock.Mock(stdout="Tested: fail\n\nTested: pass (10/10)\n")
        with mock.patch.object(check_tested.subprocess, "run", return_value=completed):
            self.assertEqual("fail", check_tested.read_tested_trailer("main"))

    def test_stale_evidence_does_not_count_as_green(self) -> None:
        evidence = [{
            "cmd": "pytest",
            "failed": 0,
            "git_state": "old-state",
            "covers": ["src/app.py"],
        }]
        effective = check_tested.filter_evidence_for_state(evidence, "new-state")
        self.assertEqual([], effective)

    def test_current_evidence_is_retained(self) -> None:
        evidence = [{"cmd": "pytest", "failed": 0, "git_state": "same"}]
        self.assertEqual(evidence, check_tested.filter_evidence_for_state(evidence, "same"))

    def test_create_mr_ignores_stale_evidence(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
            f.write(json.dumps({
                "cmd": "pytest", "failed": 0, "exit_code": 0,
                "passed": 4, "total": 4, "git_state": "old",
            }) + "\n")
            path = f.name
        try:
            with mock.patch.object(create_mr, "repository_state", return_value="new"):
                rendered = create_mr.gen_tested(path)
            self.assertNotIn("4/4", rendered)
            self.assertIn("[ ]", rendered)
        finally:
            os.unlink(path)


class TestFileExemptionTests(unittest.TestCase):
    """P0-1: 测试文件对大多数内置规则豁免, 只保留 skipped-test。"""

    def setUp(self) -> None:
        self.cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        self.cfg["risk_annotations"]["enforcement"] = "hard"

    def _make_diff(self, path: str, content: str) -> str:
        return f"+++ b/{path}\n@@ -0,0 +1 @@\n+{content}\n"

    def test_magic_id_in_go_test_file_is_exempt(self) -> None:
        """Go 测试文件里的长数字 ID (如身份证) 不触发 magic-id。"""
        diff = self._make_diff(
            "service/user/user_test.go",
            'idcard := "110101199001011234"',
        )
        violations = scan_risks.scan(diff, self.cfg)
        types = [v["type"] for v in violations]
        self.assertNotIn("magic-id", types)

    def test_auth_bypass_in_go_test_file_is_exempt(self) -> None:
        """Go 测试文件里的断言比较 (userId == "...") 不触发 auth-bypass。"""
        diff = self._make_diff(
            "pkg/auth/auth_test.go",
            'assert.Equal(t, userId, "test-user")',
        )
        violations = scan_risks.scan(diff, self.cfg)
        types = [v["type"] for v in violations]
        self.assertNotIn("auth-bypass", types)

    def test_skipped_test_in_python_test_file_is_still_detected(self) -> None:
        """Python 测试文件里 skip 标注仍然应当被检测 (skipped-test 在 _TEST_FILE_PATTERNS 中)。"""
        diff = self._make_diff(
            "tests/test_payment.py",
            "@pytest.mark.skip",
        )
        violations = scan_risks.scan(diff, self.cfg)
        types = [v["type"] for v in violations]
        self.assertIn("skipped-test", types)

    def test_magic_id_in_production_go_file_is_detected(self) -> None:
        """非测试 Go 文件的 magic-id 仍然触发。"""
        diff = self._make_diff(
            "service/user/user.go",
            'adminID := "110101199001011234"',
        )
        violations = scan_risks.scan(diff, self.cfg)
        types = [v["type"] for v in violations]
        self.assertIn("magic-id", types)

    def test_go_test_func_removal_is_detected(self) -> None:
        """P0-1: 删除 Go 的 func TestXxx 应被 test-removal 检测到。"""
        diff = (
            "-func TestPayment(t *testing.T) {\n"
            f'+// risk:test-removal reason:"consolidated into TestPaymentV2" '
            f'owner:@qa reviewed:{dt.date.today().isoformat()}\n'
        )
        problems = scan_risks.check_test_removal(diff, self.cfg)
        self.assertEqual([], problems)

    def test_go_test_func_removal_without_annotation_is_flagged(self) -> None:
        diff = "-func TestRefund(t *testing.T) {\n"
        problems = scan_risks.check_test_removal(diff, self.cfg)
        self.assertTrue(problems)


class ExtensionFilterTests(unittest.TestCase):
    """P0-2: 内置规则按扩展名过滤, Go 文件不被 C#/JS 专属规则触发。"""

    def setUp(self) -> None:
        self.cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        self.cfg["risk_annotations"]["enforcement"] = "hard"

    def _make_diff(self, path: str, content: str) -> str:
        return f"+++ b/{path}\n@@ -0,0 +1 @@\n+{content}\n"

    def test_swallowed_exception_not_triggered_on_go_file(self) -> None:
        """swallowed-exception (catch 语法) 不应在 Go 文件触发。"""
        diff = self._make_diff("pkg/foo/foo.go", "catch (Exception) { }")
        violations = scan_risks.scan(diff, self.cfg)
        types = [v["type"] for v in violations]
        self.assertNotIn("swallowed-exception", types)

    def test_swallowed_exception_triggered_on_cs_file(self) -> None:
        """swallowed-exception 在 C# 文件仍然触发。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cs_file = Path(tmp) / "Service.cs"
            cs_file.write_text("catch (Exception) { }\n", encoding="utf-8")
            diff = (
                f"+++ b/{cs_file.as_posix()}\n"
                "@@ -0,0 +1 @@\n"
                "+catch (Exception) { }\n"
            )
            violations = scan_risks.scan(diff, self.cfg)
        types = [v["type"] for v in violations]
        self.assertIn("swallowed-exception", types)

    def test_skipped_test_not_triggered_on_go_file(self) -> None:
        """skipped-test ([Fact(Skip=) / @pytest) 不适用于 Go 文件。"""
        diff = self._make_diff("pkg/foo/foo_test.go", '@pytest.mark.skip')
        violations = scan_risks.scan(diff, self.cfg)
        # 测试文件豁免会先过滤掉, 但即使是生产 .go 文件 skipped-test 也不应命中
        diff2 = self._make_diff("pkg/foo/foo.go", '@pytest.mark.skip')
        violations2 = scan_risks.scan(diff2, self.cfg)
        types = [v["type"] for v in violations2]
        self.assertNotIn("skipped-test", types)


class PatternIncludesTests(unittest.TestCase):
    """P1-3: pattern_includes 从外部 YAML 加载规则, 类型自动注册, mode:warn 不阻断。"""

    def setUp(self) -> None:
        self.cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        self.cfg["risk_annotations"]["enforcement"] = "hard"

    def _scan_go(self, code_line: str, patterns_yml: str) -> list[dict]:
        """用临时 patterns 文件和临时 Go 源文件构造 diff 并扫描。"""
        import tempfile, yaml as _yaml  # noqa: E401
        with tempfile.TemporaryDirectory() as tmp:
            # 写 patterns yml
            pat_path = os.path.join(tmp, "go.yml")
            with open(pat_path, "w", encoding="utf-8") as f:
                f.write(patterns_yml)
            # 加载 config 并注入 includes
            cfg = json.loads(json.dumps(self.cfg))
            cfg["risk_annotations"]["pattern_includes"] = [pat_path]
            scan_risks._load_pattern_includes(cfg, None)
            # 写 Go 源文件
            src = Path(tmp) / "service.go"
            src.write_text(code_line + "\n", encoding="utf-8")
            diff = (
                f"+++ b/{src.as_posix()}\n"
                "@@ -0,0 +1 @@\n"
                f"+{code_line}\n"
            )
            return scan_risks.scan(diff, cfg)

    def test_go_swallowed_error_warn_does_not_block(self) -> None:
        """patterns/go.yml 的 swallowed-error (mode:warn) 命中但不阻断。"""
        yml = (
            "patterns:\n"
            "  - type: swallowed-error\n"
            '    regex: \'\\b_\\s*=\\s*\\w[\\w.]*\\(\'\n'
            "    desc: 显式丢弃 error\n"
            "    exts: [\".go\"]\n"
            "    mode: warn\n"
        )
        violations = self._scan_go("_ = db.Close()", yml)
        self.assertTrue(violations, "应有 warn 违规")
        self.assertEqual("warn", violations[0]["mode"])

    def test_warn_violation_does_not_cause_hard_exit(self) -> None:
        """mode:warn 违规在 hard enforcement 下不返回退出码 1。"""
        import tempfile
        yml = (
            "patterns:\n"
            "  - type: swallowed-error\n"
            '    regex: \'\\b_\\s*=\\s*\\w[\\w.]*\\(\'\n'
            "    desc: 显式丢弃 error\n"
            "    exts: [\".go\"]\n"
            "    mode: warn\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            pat_path = os.path.join(tmp, "go.yml")
            with open(pat_path, "w", encoding="utf-8") as f:
                f.write(yml)
            src = Path(tmp) / "service.go"
            src.write_text("_ = db.Close()\n", encoding="utf-8")
            diff_path = os.path.join(tmp, "test.diff")
            with open(diff_path, "w", encoding="utf-8") as f:
                f.write(
                    f"+++ b/{src.as_posix()}\n"
                    "@@ -0,0 +1 @@\n"
                    "+_ = db.Close()\n"
                )
            cfg_path = os.path.join(tmp, "governance.config.yml")
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write(
                    "risk_annotations:\n"
                    "  enforcement: hard\n"
                    f"  pattern_includes:\n"
                    f"    - {pat_path}\n"
                    "  registered_types:\n"
                    "    - swallowed-error\n"
                )
            with mock.patch.object(
                sys, "argv",
                ["scan_risks.py", "--diff-file", diff_path, "--config", cfg_path]
            ):
                rc = scan_risks.main()
        self.assertEqual(0, rc, "warn-only 违规不应阻断 (exit 0)")

    def test_go_pattern_not_triggered_on_cs_file(self) -> None:
        """Go 专属规则 (exts:[.go]) 不对 .cs 文件触发。"""
        yml = (
            "patterns:\n"
            "  - type: sensitive-log\n"
            '    regex: \'\\bpassword\\b\'\n'
            "    desc: 敏感字段进日志\n"
            "    exts: [\".go\"]\n"
            "    mode: warn\n"
        )
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            pat_path = os.path.join(tmp, "go.yml")
            with open(pat_path, "w", encoding="utf-8") as f:
                f.write(yml)
            cfg = json.loads(json.dumps(self.cfg))
            cfg["risk_annotations"]["pattern_includes"] = [pat_path]
            scan_risks._load_pattern_includes(cfg, None)
            src = Path(tmp) / "Service.cs"
            src.write_text('log.Info("password=" + pwd);\n', encoding="utf-8")
            diff = (
                f"+++ b/{src.as_posix()}\n"
                "@@ -0,0 +1 @@\n"
                '+log.Info("password=" + pwd);\n'
            )
            violations = scan_risks.scan(diff, cfg)
        types = [v["type"] for v in violations]
        self.assertNotIn("sensitive-log", types)

    def test_pattern_includes_missing_file_is_skipped(self) -> None:
        """不存在的 pattern_includes 路径不抛异常, 仅打印警告。"""
        cfg = json.loads(json.dumps(self.cfg))
        cfg["risk_annotations"]["pattern_includes"] = ["/nonexistent/rules.yml"]
        try:
            scan_risks._load_pattern_includes(cfg, None)
        except Exception as exc:
            self.fail(f"不应抛异常: {exc}")


class RunAffectedTestsTests(unittest.TestCase):
    """P1-4: run_affected_tests.py 的核心逻辑单元测试。"""

    def setUp(self) -> None:
        run_affected = importlib.import_module("run_affected_tests")
        self.run_affected = run_affected

    def test_affected_packages_extracts_go_dirs(self) -> None:
        diff_output = (
            "pkg/payment/pay.go\n"
            "pkg/payment/pay_test.go\n"
            "pkg/user/user.go\n"
            "README.md\n"
        )
        pkgs = self.run_affected.affected_packages(diff_output)
        self.assertIn("pkg/payment", pkgs)
        self.assertIn("pkg/user", pkgs)
        self.assertNotIn("README.md", pkgs)

    def test_affected_packages_empty_diff(self) -> None:
        self.assertEqual([], self.run_affected.affected_packages(""))

    def test_affected_packages_no_go_files(self) -> None:
        diff_output = "docs/README.md\nci/pipeline.yml\n"
        self.assertEqual([], self.run_affected.affected_packages(diff_output))

    def test_run_tests_empty_packages_returns_zero(self) -> None:
        rc = self.run_affected.run_tests([], timeout=30)
        self.assertEqual(0, rc)

    def test_find_go_module_root_returns_none_outside_module(self) -> None:
        with mock.patch("os.path.isfile", return_value=False):
            result = self.run_affected.find_go_module_root()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
