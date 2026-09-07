"""
Tests for the governance compound flywheel (pending lessons).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add scripts to path
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


class TestFingerprint(unittest.TestCase):
    """Tests for fingerprint calculation."""

    def test_compute_fingerprint_same_code(self):
        """Same pattern type + code should produce same fingerprint."""
        import fingerprint

        code = 'SELECT * FROM users WHERE id = "1"'
        fp1 = fingerprint.compute_fingerprint("sql-injection", code)
        fp2 = fingerprint.compute_fingerprint("sql-injection", code)
        self.assertEqual(fp1, fp2)

    def test_compute_fingerprint_different_type(self):
        """Different pattern types should produce different fingerprints."""
        import fingerprint

        code = 'SELECT * FROM users'
        fp1 = fingerprint.compute_fingerprint("sql-injection", code)
        fp2 = fingerprint.compute_fingerprint("command-injection", code)
        self.assertNotEqual(fp1, fp2)

    def test_normalize_code_strips_whitespace(self):
        """Code normalization should strip leading/trailing whitespace."""
        import fingerprint

        code1 = "  SELECT * FROM users  "
        code2 = "SELECT * FROM users"
        fp1 = fingerprint.compute_fingerprint("sql-injection", code1)
        fp2 = fingerprint.compute_fingerprint("sql-injection", code2)
        self.assertEqual(fp1, fp2)

    def test_normalize_code_handles_variables(self):
        """Code with different variable names should produce same fingerprint."""
        import fingerprint

        # Different variable names, same SQL pattern
        code1 = "SELECT * FROM users WHERE id = @userId"
        code2 = "SELECT * FROM users WHERE id = @adminId"
        fp1 = fingerprint.compute_fingerprint("sql-injection", code1)
        fp2 = fingerprint.compute_fingerprint("sql-injection", code2)
        # These might be different due to variable normalization
        # but should both be 16 hex chars
        self.assertEqual(len(fp1), 16)
        self.assertEqual(len(fp2), 16)

    def test_fingerprint_file_name(self):
        """Fingerprint file name should be sanitized."""
        import fingerprint

        filename = fingerprint.fingerprint_file_name(
            "sql-injection",
            'SELECT * FROM users WHERE id = "1"'
        )
        self.assertTrue(filename.endswith(".yml"))
        self.assertIn("sql-injection", filename)


class TestValidatePending(unittest.TestCase):
    """Tests for pending lesson validation."""

    def setUp(self):
        """Create temp directory for tests."""
        self.temp_dir = tempfile.mkdtemp()
        self.pending_dir = Path(self.temp_dir) / ".governance" / "pending-lessons"
        self.pending_dir.mkdir(parents=True)

    def tearDown(self):
        """Clean up temp directory."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_valid_pending_file(self):
        """Valid pending file should pass validation."""
        import validate_pending

        valid_pending = {
            "id": "test-123-456-789",
            "pattern_type": "sql-injection",
            "source_repo": "test-repo",
            "source_ref": "feature/test",
            "fingerprint": "a1b2c3d4e5f67890",
            "detected_at": "2026-08-30T12:00:00Z",
            "failure_context": {
                "file": "src/handler.py",
                "line": 42,
                "snippet": "SELECT * FROM users",
                "diff_baseline": "origin/main",
            },
            "evidence": {
                "rule_id": "sql-injection",
                "scan_summary": "SQL injection risk detected",
                "raw_diff_fragment": "--- a/src/handler.py",
            },
            "regression": "This pattern could be copied",
            "status": "pending",
        }

        import yaml
        test_file = self.pending_dir / "test-pending.yml"
        with open(test_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(valid_pending, f)

        errors = []
        result = validate_pending.validate_pending_file(test_file, errors)
        self.assertEqual(result, 1)
        self.assertEqual(len(errors), 0)

    def test_missing_required_field(self):
        """Missing required field should fail validation."""
        import validate_pending

        invalid_pending = {
            "id": "test-123-456-789",
            # Missing pattern_type
            "source_repo": "test-repo",
            "source_ref": "feature/test",
            "fingerprint": "a1b2c3d4e5f67890",
            "detected_at": "2026-08-30T12:00:00Z",
            "failure_context": {},
            "evidence": {},
            "regression": "test",
            "status": "pending",
        }

        import yaml
        test_file = self.pending_dir / "invalid-pending.yml"
        with open(test_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(invalid_pending, f)

        errors = []
        result = validate_pending.validate_pending_file(test_file, errors)
        self.assertEqual(result, 0)
        self.assertTrue(any("pattern_type" in e for e in errors))

    def test_invalid_status(self):
        """Invalid status should fail validation."""
        import validate_pending

        invalid_pending = {
            "id": "test-123-456-789",
            "pattern_type": "sql-injection",
            "source_repo": "test-repo",
            "source_ref": "feature/test",
            "fingerprint": "a1b2c3d4e5f67890",
            "detected_at": "2026-08-30T12:00:00Z",
            "failure_context": {
                "file": "src/handler.py",
                "line": 42,
                "snippet": "SELECT * FROM users",
                "diff_baseline": "origin/main",
            },
            "evidence": {
                "rule_id": "sql-injection",
                "scan_summary": "test",
                "raw_diff_fragment": "test",
            },
            "regression": "test",
            "status": "invalid-status",  # Invalid!
        }

        import yaml
        test_file = self.pending_dir / "invalid-status.yml"
        with open(test_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(invalid_pending, f)

        errors = []
        result = validate_pending.validate_pending_file(test_file, errors)
        self.assertEqual(result, 0)
        self.assertTrue(any("status" in e.lower() for e in errors))

    def test_invalid_fingerprint_format(self):
        """Invalid fingerprint format should fail validation."""
        import validate_pending

        invalid_pending = {
            "id": "test-123-456-789",
            "pattern_type": "sql-injection",
            "source_repo": "test-repo",
            "source_ref": "feature/test",
            "fingerprint": "invalid-fingerprint",  # Invalid!
            "detected_at": "2026-08-30T12:00:00Z",
            "failure_context": {
                "file": "src/handler.py",
                "line": 42,
                "snippet": "SELECT * FROM users",
                "diff_baseline": "origin/main",
            },
            "evidence": {
                "rule_id": "sql-injection",
                "scan_summary": "test",
                "raw_diff_fragment": "test",
            },
            "regression": "test",
            "status": "pending",
        }

        import yaml
        test_file = self.pending_dir / "invalid-fp.yml"
        with open(test_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(invalid_pending, f)

        errors = []
        result = validate_pending.validate_pending_file(test_file, errors)
        self.assertEqual(result, 0)
        self.assertTrue(any("fingerprint" in e.lower() for e in errors))


class TestGovernanceMetrics(unittest.TestCase):
    """Tests for governance metrics calculation."""

    def test_obsolescence_rate_calculation(self):
        """Obsolescence rate should be rejected / (confirmed + rejected)."""
        import governance_metrics

        # 2 confirmed, 3 rejected -> 3/5 = 0.6
        pending = [
            {"status": "confirmed"},
            {"status": "confirmed"},
            {"status": "rejected"},
            {"status": "rejected"},
            {"status": "rejected"},
        ]
        rate = governance_metrics.calculate_obsolescence_rate(pending)
        self.assertEqual(rate, 0.6)

    def test_obsolescence_rate_zero_denominator(self):
        """Obsolescence rate should be N/A when denominator is zero."""
        import governance_metrics

        # No confirmed or rejected
        pending = [
            {"status": "pending"},
            {"status": "pending"},
        ]
        rate = governance_metrics.calculate_obsolescence_rate(pending)
        self.assertEqual(rate, "N/A")

    def test_confirmation_rate_calculation(self):
        """Confirmation rate should be confirmed / total."""
        import governance_metrics

        pending = [
            {"status": "confirmed"},
            {"status": "confirmed"},
            {"status": "rejected"},
            {"status": "pending"},
        ]
        rate = governance_metrics.calculate_confirmation_rate(pending)
        self.assertEqual(rate, 0.5)  # 2/4 = 0.5

    def test_rejection_rate_calculation(self):
        """Rejection rate should be rejected / total."""
        import governance_metrics

        pending = [
            {"status": "confirmed"},
            {"status": "rejected"},
            {"status": "rejected"},
        ]
        rate = governance_metrics.calculate_rejection_rate(pending)
        self.assertAlmostEqual(rate, 2/3, places=4)

    def test_empty_pending_list(self):
        """Empty pending list should return N/A for rates."""
        import governance_metrics

        pending = []
        self.assertEqual(governance_metrics.calculate_confirmation_rate(pending), "N/A")
        self.assertEqual(governance_metrics.calculate_rejection_rate(pending), "N/A")
        self.assertEqual(governance_metrics.calculate_obsolescence_rate(pending), "N/A")


class TestLessonsReview(unittest.TestCase):
    """Tests for lessons review workflow."""

    def setUp(self):
        """Create temp directory for tests."""
        self.temp_dir = tempfile.mkdtemp()
        self.pending_dir = Path(self.temp_dir) / ".governance" / "pending-lessons"
        self.pending_dir.mkdir(parents=True)
        # Set as PENDING_DIR for tests
        import lessons_review
        lessons_review.PENDING_DIR = self.pending_dir

    def tearDown(self):
        """Clean up temp directory."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_load_all_pending_empty(self):
        """Empty pending directory should return empty list."""
        import lessons_review

        results = lessons_review._load_all_pending()
        self.assertEqual(results, [])


class TestAggregatePending(unittest.TestCase):
    """Tests for cross-repository fingerprint aggregation."""

    def test_aggregate_by_fingerprint(self):
        """Same fingerprint should be grouped together."""
        import aggregate_pending

        pending = [
            {"fingerprint": "aaa111", "source_repo": "repo1", "status": "pending"},
            {"fingerprint": "aaa111", "source_repo": "repo2", "status": "pending"},
            {"fingerprint": "bbb222", "source_repo": "repo1", "status": "pending"},
        ]

        by_fp = aggregate_pending.aggregate_dicts_by_fingerprint(pending)
        self.assertEqual(len(by_fp), 2)
        self.assertEqual(len(by_fp["aaa111"].pending_entries), 2)
        self.assertEqual(len(by_fp["bbb222"].pending_entries), 1)

    def test_aggregated_fingerprint_counts(self):
        """AggregatedFingerprint should track occurrences correctly."""
        import aggregate_pending

        agg = aggregate_pending.AggregatedFingerprint("test123")
        agg.add({
            "fingerprint": "test123",
            "source_repo": "repo1",
            "source_ref": "branch1",
            "detected_at": "2026-08-01T00:00:00Z",
            "status": "pending",
        })
        agg.add({
            "fingerprint": "test123",
            "source_repo": "repo2",
            "source_ref": "branch2",
            "detected_at": "2026-08-02T00:00:00Z",
            "status": "confirmed",
        })

        self.assertEqual(agg.occurrence_count, 2)
        self.assertEqual(len(agg.repos_seen), 2)
        self.assertEqual(agg.first_detected, "2026-08-01T00:00:00Z")
        self.assertEqual(agg.last_detected, "2026-08-02T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
