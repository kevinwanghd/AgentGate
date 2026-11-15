from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

gate_decision = importlib.import_module("gate_decision")

SHA = "a" * 40


def decide(config_auto: dict, changed: list[str], checks: dict[str, str]) -> dict:
    return gate_decision.build_gate_result(
        source_sha=SHA,
        target_sha=SHA,
        policy_sha=SHA,
        changed_paths=changed,
        checks=checks,
        config={"auto_merge": config_auto},
        valid_approvals=0,
        target_branch="main",
        pipeline_kind="mr",
    )


BASE_CHECKS = {"risk-scan": "pass", "secret-scan": "pass", "mr-validate": "pass"}


class SkipCountsAsPassTests(unittest.TestCase):
    def test_skip_fails_when_not_configured(self):
        result = decide(
            {"required_checks": ["review"], "risk_paths": {"low": ["docs/**"]}},
            ["docs/a.md"],
            {**BASE_CHECKS, "review": "skip"},
        )
        self.assertEqual(result["result"], "FAIL")
        self.assertIn("required_check_failed", result["blocking_reasons"])

    def test_skip_passes_when_configured(self):
        result = decide(
            {
                "required_checks": ["review"],
                "skip_counts_as_pass": ["review"],
                "risk_paths": {"low": ["docs/**"]},
            },
            ["docs/a.md"],
            {**BASE_CHECKS, "review": "skip"},
        )
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["merge_action"], "AUTO_MERGE")

    def test_fail_still_blocks_even_if_skip_allowed(self):
        result = decide(
            {
                "required_checks": ["review"],
                "skip_counts_as_pass": ["review"],
                "risk_paths": {"low": ["docs/**"]},
            },
            ["docs/a.md"],
            {**BASE_CHECKS, "review": "fail"},
        )
        self.assertEqual(result["result"], "FAIL")

    def test_other_checks_still_require_pass(self):
        result = decide(
            {
                "required_checks": ["review", "ci"],
                "skip_counts_as_pass": ["review"],
                "risk_paths": {"low": ["docs/**"]},
            },
            ["docs/a.md"],
            {**BASE_CHECKS, "review": "skip", "ci": "skip"},
        )
        self.assertEqual(result["result"], "FAIL")


if __name__ == "__main__":
    unittest.main()