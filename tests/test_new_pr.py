from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

new_pr = importlib.import_module("new_pr")


class AiUsageTests(unittest.TestCase):
    def test_detects_trailer(self):
        self.assertTrue(new_pr.has_ai_usage("feat: x\n\nAI-Usage: heavy\n"))
        self.assertTrue(new_pr.has_ai_usage("fix: y\n\nai-usage: none"))

    def test_missing_trailer(self):
        self.assertFalse(new_pr.has_ai_usage("feat: x\n\nbody only"))


class BodyTests(unittest.TestCase):
    def test_template_sections_present(self):
        body = new_pr.build_body("为什么", "- 改了什么", "- 无", "- [x] 跑了测试", "- 风险点：无", "- #1")
        for section in ("## 背景", "## 变更内容", "## 不包含的内容", "## 自测确认", "## 风险与回滚", "## 关联"):
            self.assertIn(section, body)

    def test_defaults_fill(self):
        body = new_pr.build_body("", "", "", "", "", "-")
        self.assertIn("见变更内容", body)
        self.assertIn("待补", body)


if __name__ == "__main__":
    unittest.main()