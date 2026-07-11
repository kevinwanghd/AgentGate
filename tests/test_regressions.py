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


class EvidenceBindingTests(unittest.TestCase):
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
