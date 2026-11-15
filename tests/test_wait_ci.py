from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

wait_ci = importlib.import_module("wait_ci")


def run(number: int, status: str, conclusion: str | None, created: str = "2026-01-01T00:00:00Z") -> dict:
    return {
        "run_number": number,
        "status": status,
        "conclusion": conclusion,
        "created_at": created,
    }


class ClassifyTests(unittest.TestCase):
    def test_unfinished_run_is_not_decided(self):
        self.assertIsNone(wait_ci.classify(None))
        self.assertIsNone(wait_ci.classify(run(1, "queued", None)))
        self.assertIsNone(wait_ci.classify(run(1, "in_progress", None)))

    def test_successful_conclusions_pass(self):
        for conclusion in ("success", "neutral", "skipped"):
            self.assertEqual(wait_ci.classify(run(1, "completed", conclusion)), "pass")

    def test_failed_conclusions_fail(self):
        for conclusion in ("failure", "cancelled", "timed_out", "action_required", "stale"):
            self.assertEqual(wait_ci.classify(run(1, "completed", conclusion)), "fail")


class LatestRunTests(unittest.TestCase):
    def test_picks_highest_run_number(self):
        runs = [run(3, "completed", "failure"), run(7, "completed", "success"), run(5, "in_progress", None)]
        self.assertEqual(wait_ci.latest_run(runs)["run_number"], 7)

    def test_empty_is_none(self):
        self.assertIsNone(wait_ci.latest_run([]))


class WaitForTests(unittest.TestCase):
    def test_all_workflows_pass(self):
        with mock.patch.object(wait_ci, "fetch_runs", return_value=[run(1, "completed", "success")]):
            result, _ = wait_ci.wait_for("o/r", "sha", ["ci.yml", "lint.yml"], "token", 1, 0.01)
        self.assertEqual(result, "pass")

    def test_failure_returns_immediately(self):
        with mock.patch.object(wait_ci, "fetch_runs", return_value=[run(4, "completed", "failure")]):
            result, reason = wait_ci.wait_for("o/r", "sha", ["ci.yml"], "token", 5, 0.01)
        self.assertEqual(result, "fail")
        self.assertIn("#4", reason)

    def test_missing_run_times_out(self):
        with mock.patch.object(wait_ci, "fetch_runs", return_value=[]):
            result, reason = wait_ci.wait_for("o/r", "sha", ["ci.yml"], "token", 0.05, 0.01)
        self.assertEqual(result, "fail")
        self.assertIn("未启动", reason)


if __name__ == "__main__":
    unittest.main()