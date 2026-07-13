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


if __name__ == "__main__":
    unittest.main()
