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
gate_decision = importlib.import_module("gate_decision")


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

    def test_mixed_warn_and_block_patterns_result_in_block(self) -> None:
        """同一行同时命中 warn 和 block 时, 最终模式应为 block。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "service.go"
            src.write_text("Danger()\n", encoding="utf-8")
            cfg = json.loads(json.dumps(self.cfg))
            cfg["risk_annotations"]["registered_types"].extend(
                ["danger-warn", "danger-block"]
            )
            cfg["risk_annotations"]["custom_patterns"] = [
                {
                    "type": "danger-warn",
                    "regex": r"Danger\(",
                    "desc": "warn-only dangerous call",
                    "exts": [".go"],
                    "mode": "warn",
                },
                {
                    "type": "danger-block",
                    "regex": r"Danger\(",
                    "desc": "blocking dangerous call",
                    "exts": [".go"],
                    "mode": "block",
                },
            ]
            diff = (
                f"+++ b/{src.as_posix()}\n"
                "@@ -0,0 +1 @@\n"
                "+Danger()\n"
            )
            violations = scan_risks.scan(diff, cfg)
        self.assertEqual("block", violations[0]["mode"])
        self.assertEqual("danger-block/danger-warn", violations[0]["type"])

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


class GoPatternHardeningTests(unittest.TestCase):
    """Regression coverage for the Go rule pack shipped in patterns/go.yml."""

    def setUp(self) -> None:
        self.cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        self.cfg["risk_annotations"]["enforcement"] = "hard"
        self.cfg["risk_annotations"]["pattern_includes"] = [
            str(ROOT / "patterns" / "go.yml")
        ]
        scan_risks._load_pattern_includes(self.cfg, None)

    def _scan_go(self, source: str, filename: str = "service.go") -> list[dict]:
        code = source.strip("\n")
        lines = code.splitlines()
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / filename
            src.write_text(code + "\n", encoding="utf-8")
            diff_lines = [
                f"+++ b/{src.as_posix()}",
                f"@@ -0,0 +1,{len(lines)} @@",
                *[f"+{line}" for line in lines],
            ]
            return scan_risks.scan("\n".join(diff_lines) + "\n", self.cfg)

    @staticmethod
    def _types(violations: list[dict]) -> set[str]:
        out: set[str] = set()
        for item in violations:
            out.update(item["type"].split("/"))
        return out

    def assertHits(self, risk_type: str, source: str) -> None:
        violations = self._scan_go(source)
        self.assertIn(risk_type, self._types(violations), violations)
        matched = [v for v in violations if risk_type in v["type"].split("/")]
        self.assertTrue(all(v["mode"] == "warn" for v in matched), matched)

    def assertDoesNotHit(self, risk_type: str, source: str) -> None:
        violations = self._scan_go(source)
        self.assertNotIn(risk_type, self._types(violations), violations)

    def test_go_cmd_inject_multiline_hits(self) -> None:
        self.assertHits(
            "go-cmd-inject",
            '''
cmd := exec.Command(
    "sh",
    "-c",
    "echo " + userInput,
)
''',
        )

    def test_go_cmd_inject_constant_args_do_not_hit(self) -> None:
        self.assertDoesNotHit(
            "go-cmd-inject",
            'cmd := exec.Command("tool", "--version")',
        )

    def test_go_ssrf_new_request_multiline_hits(self) -> None:
        self.assertHits(
            "go-ssrf",
            '''
req, _ := http.NewRequest(
    "GET",
    "https://api.example.test/" + userPath,
    nil,
)
client.Do(req)
''',
        )

    def test_go_ssrf_constant_url_does_not_hit(self) -> None:
        self.assertDoesNotHit(
            "go-ssrf",
            'resp, _ := http.Get("https://api.example.test/health")',
        )

    def test_go_sql_concat_common_forms_hit(self) -> None:
        samples = [
            'query := fmt.Sprintf("SELECT * FROM users WHERE id = %s", userID)',
            'query := "SELECT * FROM users WHERE id = " + userID',
            '''
query := "SELECT * FROM users WHERE name = '" +
    userName + "'"
''',
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertHits("go-sql-concat", sample)

    def test_go_sql_parameterized_query_does_not_hit(self) -> None:
        self.assertDoesNotHit(
            "go-sql-concat",
            'rows, _ := db.Query("SELECT * FROM users WHERE id = ?", userID)',
        )

    def test_go_hardcoded_secret_literals_hit(self) -> None:
        samples = [
            'password := "placeholder-secret-value"',
            "cfg := Config{ClientSecret: `placeholder-secret-value`}",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertHits("go-hardcoded-secret", sample)

    def test_go_hardcoded_secret_non_literals_do_not_hit(self) -> None:
        samples = [
            'token := os.Getenv("TOKEN")',
            'tokenCount := 3',
            'token := "short"',
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertDoesNotHit("go-hardcoded-secret", sample)

    def test_go_tls_skip_verify_hits(self) -> None:
        self.assertHits(
            "go-tls-skip-verify",
            "cfg := &tls.Config{InsecureSkipVerify: true}",
        )

    def test_go_tls_normal_config_does_not_hit(self) -> None:
        self.assertDoesNotHit(
            "go-tls-skip-verify",
            "cfg := &tls.Config{MinVersion: tls.VersionTLS12}",
        )

    def test_go_panic_hits_business_file(self) -> None:
        self.assertHits("go-panic-in-handler", 'panic("unexpected state")')

    def test_go_panic_absent_does_not_hit(self) -> None:
        self.assertDoesNotHit("go-panic-in-handler", "return fmt.Errorf(\"bad state\")")

    def test_go_weak_random_security_context_hits(self) -> None:
        self.assertHits("go-weak-random", "token := rand.Intn(1000000)")

    def test_go_weak_random_sampling_context_does_not_hit(self) -> None:
        self.assertDoesNotHit("go-weak-random", "sample := rand.Intn(10)")


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


class AiUsageNotMandatoryTests(unittest.TestCase):
    """AI-Usage 不再是强制字段: 无 trailer 无描述时不阻断。"""

    def _mr_desc(self, extra: str = "") -> str:
        return (
            "## 背景\n\n修复支付超时问题。\n\n"
            "## 变更内容\n\n- 增加重试逻辑\n\n"
            "## 自测确认\n\n- [x] 本地测试通过\n"
            + extra
        )

    def test_no_ai_usage_passes_by_default(self) -> None:
        """DEFAULT_CONFIG 不含 ai_usage, 缺少 trailer/描述不应产生 problem。"""
        cfg = validate_mr.load_config(None)
        problems = validate_mr.validate(self._mr_desc(), cfg, None)
        ai_problems = [p for p in problems if "AI-Usage" in p]
        self.assertEqual([], ai_problems, f"不应有 AI-Usage 问题: {ai_problems}")

    def test_ai_usage_optional_when_not_in_mandatory_fields(self) -> None:
        """mandatory_fields 中不含 ai_usage 时, 校验器不检查该字段。"""
        cfg = {"metadata": {"enforcement": "hard", "mandatory_fields": ["background", "changes", "self_test"]},
               "large_change": validate_mr.DEFAULT_CONFIG["large_change"]}
        problems = validate_mr.validate(self._mr_desc(), cfg, None)
        ai_problems = [p for p in problems if "AI-Usage" in p]
        self.assertEqual([], ai_problems)

    def test_ai_usage_still_checked_when_in_mandatory_fields(self) -> None:
        """显式在 mandatory_fields 里加回 ai_usage 时仍然校验。"""
        cfg = {"metadata": {
                   "enforcement": "hard",
                   "mandatory_fields": ["background", "changes", "self_test", "ai_usage"],
               },
               "large_change": validate_mr.DEFAULT_CONFIG["large_change"]}
        with mock.patch.object(validate_mr, "find_ai_usage_in_commits", return_value=(False, None)):
            problems = validate_mr.validate(self._mr_desc(), cfg, None)
        ai_problems = [p for p in problems if "AI-Usage" in p or "ai_usage" in p.lower()]
        self.assertTrue(ai_problems, "显式加回 ai_usage 后应当校验")


class WarnJobSummaryTests(unittest.TestCase):
    """warn 命中写入 GITHUB_STEP_SUMMARY, block 为零时不写失败表格。"""

    def setUp(self) -> None:
        self.cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        self.cfg["risk_annotations"]["enforcement"] = "hard"

    def _run_scan_with_summary(self, diff: str, cfg: dict) -> tuple[list[dict], str]:
        """运行 scan 并捕获写入 GITHUB_STEP_SUMMARY 的内容。"""
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            summary_path = f.name
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary_path}):
                violations = scan_risks.scan(diff, cfg)
                blocking = [v for v in violations if v.get("mode") != "warn"]
                warn_only = [v for v in violations if v.get("mode") == "warn"]
                scan_risks._write_summary(blocking, warn_only)
            with open(summary_path, encoding="utf-8") as f:
                summary = f.read()
        finally:
            os.unlink(summary_path)
        return violations, summary

    def test_pass_writes_green_summary(self) -> None:
        diff = "+++ b/pkg/foo/foo.go\n@@ -0,0 +1 @@\n+func Hello() {}\n"
        _, summary = self._run_scan_with_summary(diff, self.cfg)
        self.assertIn("✅", summary)
        self.assertNotIn("❌", summary)
        self.assertNotIn("⚠️", summary)

    def test_warn_violation_appears_in_summary(self) -> None:
        """mode:warn 规则的命中出现在 Job Summary 中, 门禁不阻断。"""
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
            src = Path(tmp) / "service.go"
            src.write_text('log.Info("password=" + pwd)\n', encoding="utf-8")
            diff = (
                f"+++ b/{src.as_posix()}\n"
                "@@ -0,0 +1 @@\n"
                '+log.Info("password=" + pwd)\n'
            )
            cfg = json.loads(json.dumps(self.cfg))
            cfg["risk_annotations"]["pattern_includes"] = [pat_path]
            scan_risks._load_pattern_includes(cfg, None)
            _, summary = self._run_scan_with_summary(diff, cfg)

        self.assertIn("⚠️", summary)
        self.assertNotIn("❌", summary)
        self.assertIn("warn", summary.lower())

    def test_no_summary_file_when_env_not_set(self) -> None:
        """未设置 GITHUB_STEP_SUMMARY 时, _write_summary 静默跳过不报错。"""
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
        with mock.patch.dict(os.environ, env, clear=True):
            try:
                scan_risks._write_summary([], [])
            except Exception as exc:
                self.fail(f"不应抛异常: {exc}")


class ReverseDepsTests(unittest.TestCase):
    """#8: run_affected_tests 反向依赖扩展。"""

    def setUp(self) -> None:
        self.ra = importlib.import_module("run_affected_tests")

    def _fake_go_list_json(self, pkgs: list[dict]) -> str:
        """生成 go list -json ./... 的输出格式（多个拼接 JSON 对象）。"""
        return "\n".join(json.dumps(p) for p in pkgs)

    def test_expand_with_importers_finds_dependent_pkg(self) -> None:
        """改了 pkg/db, importer pkg/service 应被加入测试集。"""
        pkgs = [
            {"ImportPath": "example.com/app/pkg/db", "Imports": []},
            {"ImportPath": "example.com/app/pkg/service",
             "Imports": ["example.com/app/pkg/db"]},
            {"ImportPath": "example.com/app/pkg/user",
             "Imports": ["example.com/app/pkg/service"]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            # 写 go.mod
            with open(os.path.join(tmp, "go.mod"), "w") as f:
                f.write("module example.com/app\ngo 1.21\n")
            # mock go list 输出
            fake_out = self._fake_go_list_json(pkgs)
            completed = mock.Mock(returncode=0, stdout=fake_out, stderr="")
            with mock.patch.object(
                self.ra.subprocess, "run", return_value=completed
            ), mock.patch("os.path.abspath", side_effect=lambda p: os.path.join(tmp, p) if not os.path.isabs(p) else p):
                reverse_map = self.ra.build_reverse_dep_map(tmp)
                # pkg/db を直接改動した場合の拡張
                with mock.patch.object(self.ra, "find_go_module_root", return_value=tmp):
                    expanded = self.ra.expand_with_importers(
                        ["pkg/db"], tmp, reverse_map
                    )
        self.assertIn("pkg/db", expanded)
        self.assertIn("pkg/service", expanded)
        # pkg/user は直接依存していないので含まれない (1-hop のみ)
        self.assertNotIn("pkg/user", expanded)

    def test_no_reverse_deps_returns_direct_only(self) -> None:
        """--no-reverse-deps フラグ相当: 反向依赖图が空の場合は直接パッケージのみ。"""
        direct = ["pkg/payment", "pkg/order"]
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "go.mod"), "w") as f:
                f.write("module example.com/app\ngo 1.21\n")
            result = self.ra.expand_with_importers(direct, tmp, {})
        self.assertEqual(sorted(direct), sorted(result))

    def test_go_list_failure_falls_back_gracefully(self) -> None:
        """go list が失敗してもエラーにならず空の map を返す。"""
        failed = mock.Mock(returncode=1, stdout="", stderr="error")
        with mock.patch.object(
            self.ra.subprocess, "run", return_value=failed
        ):
            result = self.ra.build_reverse_dep_map("/some/dir")
        self.assertEqual({}, result)

    def test_get_module_name_reads_go_mod(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "go.mod"), "w") as f:
                f.write("module github.com/company/myapp\ngo 1.22\n")
            name = self.ra.get_module_name(tmp)
        self.assertEqual("github.com/company/myapp", name)

    def test_affected_packages_deduplicates_dirs(self) -> None:
        diff = "pkg/payment/pay.go\npkg/payment/refund.go\npkg/order/order.go\n"
        pkgs = self.ra.affected_packages(diff)
        self.assertEqual(["pkg/order", "pkg/payment"], pkgs)


class ScanIgnoreTests(unittest.TestCase):
    """#9: 行内 scan:ignore reason:"..." 豁免精确到行。"""

    def setUp(self) -> None:
        self.cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        self.cfg["risk_annotations"]["enforcement"] = "hard"

    def _make_diff(self, path: str, lines: list[str]) -> str:
        hdr = f"+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n"
        return hdr + "".join(f"+{l}\n" for l in lines)

    def test_inline_ignore_on_same_line_suppresses_violation(self) -> None:
        """magic-id 后跟 scan:ignore reason 在同一行时豁免。"""
        diff = self._make_diff("service/pay.go", [
            '"110101199001011234"  // scan:ignore reason:"fixture for integration test"',
        ])
        violations = scan_risks.scan(diff, self.cfg)
        self.assertEqual([], violations)

    def test_inline_ignore_on_prev_line_suppresses_violation(self) -> None:
        """scan:ignore 在命中行上一行时同样豁免。"""
        diff = self._make_diff("service/pay.go", [
            "// scan:ignore reason:\"known test fixture for idcard format check\"",
            '"110101199001011234"',
        ])
        violations = scan_risks.scan(diff, self.cfg)
        self.assertEqual([], violations)

    def test_inline_ignore_without_reason_does_not_suppress(self) -> None:
        """scan:ignore 没有 reason 或 reason 太短时不豁免。"""
        diff = self._make_diff("service/pay.go", [
            '"110101199001011234"  // scan:ignore',
        ])
        violations = scan_risks.scan(diff, self.cfg)
        # 没有合法 reason 就不豁免, 仍然报违规
        types = [v["type"] for v in violations]
        self.assertIn("magic-id", types)

    def test_inline_ignore_two_lines_away_does_not_suppress(self) -> None:
        """scan:ignore 在命中行两行之外不豁免 (只看同行和上一行)。"""
        diff = self._make_diff("service/pay.go", [
            "// scan:ignore reason:\"known test fixture for idcard format check\"",
            "// some other comment",
            '"110101199001011234"',
        ])
        violations = scan_risks.scan(diff, self.cfg)
        types = [v["type"] for v in violations]
        self.assertIn("magic-id", types)

    def test_scan_ignore_in_non_adjacent_file_has_no_effect(self) -> None:
        """不同文件的 scan:ignore 不会跨文件豁免。"""
        diff = (
            "+++ b/other/file.go\n@@ -0,0 +1 @@\n"
            '// scan:ignore reason:"this is a completely different file"\n'
            "+++ b/service/pay.go\n@@ -0,0 +1 @@\n"
            '+adminId == "admin"\n'
        )
        violations = scan_risks.scan(diff, self.cfg)
        # pay.go 里的 auth-bypass 没有被 other/file.go 的 ignore 豁免
        files = [v["file"] for v in violations]
        self.assertTrue(any("pay.go" in f for f in files))


class LargeDiffSummaryTests(unittest.TestCase):
    """Tier 3: 大 diff 时向 GITHUB_STEP_SUMMARY 写拆分建议。"""

    def _numstat_output(self) -> str:
        return (
            "300\t50\tsrc/payment/pay.go\n"
            "100\t20\tsrc/order/order.go\n"
            "80\t10\tsrc/user/user.go\n"
        )

    def test_large_diff_writes_warning_to_summary(self) -> None:
        """净改动超阈值时, Job Summary 包含大变更警告和目录分布。"""
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            summary_path = f.name
        try:
            completed = mock.Mock(returncode=0, stdout=self._numstat_output())
            with mock.patch.object(validate_mr.subprocess, "run", return_value=completed), \
                 mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary_path}):
                validate_mr._write_large_diff_summary(
                    total=560, threshold=500,
                    reasons=["净改动 560 行 ≥ 500"],
                    diff_base="origin/main",
                    excluded=[],
                )
            with open(summary_path, encoding="utf-8") as f:
                content = f.read()
        finally:
            os.unlink(summary_path)

        self.assertIn("⚠️", content)
        self.assertIn("560", content)
        self.assertIn("src", content)

    def test_no_summary_when_env_not_set(self) -> None:
        """未设置 GITHUB_STEP_SUMMARY 时静默跳过。"""
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
        with mock.patch.dict(os.environ, env, clear=True):
            try:
                validate_mr._write_large_diff_summary(600, 500, [], None, [])
            except Exception as exc:
                self.fail(f"不应抛异常: {exc}")

    def test_small_diff_does_not_trigger(self) -> None:
        """未超阈值时不写 Summary (函数不被调用, 因为 is_large 为 False)。"""
        cfg = json.loads(json.dumps(validate_mr.DEFAULT_CONFIG))
        with mock.patch.object(
            validate_mr.subprocess, "run",
            return_value=mock.Mock(returncode=0, stdout="10\t5\tsrc/foo.go\n"),
        ):
            is_large, _ = validate_mr.detect_large_change(cfg, "origin/main")
        self.assertFalse(is_large)


class MRDescriptionEncodingTests(unittest.TestCase):
    def test_stdin_description_strips_utf8_bom_before_heading_match(self) -> None:
        text = "\ufeff## 背景\n测试背景\n\n## 变更内容\n测试变更\n\n## 自测确认\n已测试\n"
        with mock.patch.object(sys, "stdin", mock.Mock(isatty=lambda: False, read=lambda: text)):
            self.assertTrue(validate_mr._has_section(validate_mr.read_description(None), "背景"))

    def test_file_description_accepts_utf8_bom(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8-sig") as f:
            f.write("## 背景\n测试背景\n")
            path = f.name
        try:
            self.assertTrue(validate_mr._has_section(validate_mr.read_description(path), "背景"))
        finally:
            os.unlink(path)


class GateDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(json.dumps(gate_decision.DEFAULT_CONFIG))

    def test_clean_checks_are_auto_mergeable_by_default(self) -> None:
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/orders/service.py"],
            checks={"lint": "pass", "unit": "pass"},
            config=self.config,
        )
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["merge_action"], "AUTO_MERGE")
        self.assertEqual(result["risk_level"], "medium")

    def test_protected_path_requires_human_approval(self) -> None:
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["scripts/scan_risks.py"],
            checks={"lint": "pass", "unit": "pass"},
            config=self.config,
        )
        self.assertEqual(result["risk_level"], "critical")
        self.assertEqual(result["result"], "WAITING_APPROVAL")
        self.assertEqual(result["merge_action"], "WAIT")
        self.assertIn("protected_paths_changed", result["blocking_reasons"])

    def test_failed_check_blocks_and_is_not_retried_as_green(self) -> None:
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/orders/service.py"],
            checks={"lint": "fail", "unit": "pass"},
            config=self.config,
        )
        self.assertEqual(result["result"], "FAIL")
        self.assertEqual(result["merge_action"], "BLOCK")
        self.assertIn("required_check_failed", result["blocking_reasons"])

    def test_missing_required_check_is_blocking(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["auto_merge"]["required_checks"] = ["lint", "unit"]
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/orders/service.py"],
            checks={"lint": "pass"},
            config=config,
        )
        self.assertEqual(result["result"], "FAIL")
        self.assertIn("required_check_missing", result["blocking_reasons"])

    def test_non_pass_check_status_is_blocking(self) -> None:
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/orders/service.py"],
            checks={"lint": "queued", "unit": "pass"},
            config=self.config,
        )
        self.assertEqual(result["result"], "FAIL")
        self.assertEqual(result["merge_action"], "BLOCK")

    def test_disabled_auto_merge_waits_even_when_checks_pass(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["auto_merge"]["enabled"] = False
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/orders/service.py"],
            checks={"lint": "pass", "unit": "pass"},
            config=config,
        )
        self.assertEqual(result["result"], "WAITING_APPROVAL")
        self.assertEqual(result["merge_action"], "WAIT")
        self.assertIn("auto_merge_disabled", result["blocking_reasons"])

    def test_cli_accepts_utf8_bom_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            output = Path(directory) / "gate.json"
            evidence.write_text(
                '{"checks":{"risk-scan":"pass","secret-scan":"pass",'
                '"mr-validate":"pass","test-check":"pass","go-test":"pass",'
                '"selftest":"pass"}}',
                encoding="utf-8-sig",
            )
            with mock.patch.object(gate_decision, "_changed_paths", return_value=[]):
                with mock.patch.object(sys, "argv", [
                    "gate_decision.py", "--evidence", str(evidence),
                    "--source-sha", "head", "--target-sha", "base",
                    "--policy-sha", "policy", "--diff-base", "base",
                    "--output", str(output),
                ]):
                    self.assertEqual(gate_decision.main(), 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["result"], "PASS")


class GitLabAutoMergeTemplateTests(unittest.TestCase):
    def test_central_gitlab_template_has_gate_and_merge_bot_guards(self) -> None:
        template = (ROOT / "ci" / "governance-ci.yml").read_text(encoding="utf-8")
        self.assertIn("governance:gate-decision:", template)
        self.assertIn("governance:auto-merge:", template)
        self.assertIn("GOVERNANCE_MERGE_BOT_TOKEN", template)
        self.assertIn("CI_MERGE_REQUEST_SOURCE_PROJECT_ID", template)
        self.assertIn('--data-urlencode "sha=${SHA}"', template)
        self.assertIn("merge_when_pipeline_succeeds=true", template)

    def test_installer_ships_gate_decision_and_gitlab_auto_merge_jobs(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('scripts/gate_decision.py"   | write_file "governance/scripts/gate_decision.py"', installer)
        self.assertIn("governance:gate-decision:", installer)
        self.assertIn("governance:auto-merge:", installer)
        self.assertIn("GOVERNANCE_MERGE_BOT_TOKEN", installer)
        self.assertIn("CI_MERGE_REQUEST_SOURCE_PROJECT_ID", installer)


if __name__ == "__main__":
    unittest.main()
