from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

count_approvals = importlib.import_module("count_approvals")

HEAD_TIME = "2026-11-15T10:00:00Z"


def review(login: str, state: str, submitted: str, kind: str = "User") -> dict:
    return {
        "user": {"login": login, "type": kind},
        "state": state,
        "submitted_at": submitted,
    }


class CountValidTests(unittest.TestCase):
    def test_counts_fresh_approval(self):
        self.assertEqual(count_approvals.count_valid([review("alice", "APPROVED", HEAD_TIME)], HEAD_TIME), 1)

    def test_ignores_stale_approval(self):
        self.assertEqual(count_approvals.count_valid([review("alice", "APPROVED", "2026-11-14T00:00:00Z")], HEAD_TIME), 0)

    def test_latest_state_wins(self):
        reviews = [
            review("alice", "APPROVED", "2026-11-15T11:00:00Z"),
            review("alice", "CHANGES_REQUESTED", "2026-11-15T12:00:00Z"),
        ]
        self.assertEqual(count_approvals.count_valid(reviews, HEAD_TIME), 0)

    def test_reapprove_counts(self):
        reviews = [
            review("alice", "CHANGES_REQUESTED", "2026-11-15T11:00:00Z"),
            review("alice", "APPROVED", "2026-11-15T12:00:00Z"),
        ]
        self.assertEqual(count_approvals.count_valid(reviews, HEAD_TIME), 1)

    def test_bots_ignored(self):
        reviews = [
            review("github-actions[bot]", "APPROVED", HEAD_TIME, kind="Bot"),
            review("renovate", "APPROVED", HEAD_TIME, kind="Bot"),
        ]
        self.assertEqual(count_approvals.count_valid(reviews, HEAD_TIME), 0)

    def test_distinct_users(self):
        reviews = [
            review("alice", "APPROVED", HEAD_TIME),
            review("bob", "APPROVED", HEAD_TIME),
        ]
        self.assertEqual(count_approvals.count_valid(reviews, HEAD_TIME), 2)


if __name__ == "__main__":
    unittest.main()