"""CARD-R1 / CARD-R2: context + facts + evidence/severity + allowlist.

Fixtures are the REAL false positives that blocked OpenMontage #72/#93/#103:
  - "PyYAML 未声明" (it is declared in requirements.txt / setup.py)
  - "模块缺失" (the module exists on the target branch)
  - "新增编排违反架构" (STRATEGIES already has ~20 entries; this extends it)
  - "可能 KeyError" speculation with no concrete trigger
Each must stop blocking; a genuine defect with a concrete scenario must still block.
"""
from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

ai_review = importlib.import_module("ai_review")


def _facts(deps=("pyyaml", "requests"), files=("lib/lessons.py", "lib/review_strategies.py", "requirements.txt")):
    return {"declared_deps": list(deps), "existing_files": list(files), "changed_files": []}


class ParseDepsTests(unittest.TestCase):
    def test_requirements(self):
        deps = ai_review._parse_requirements("pyyaml>=6.0\nrequests==2.0\n# comment\n-r other.txt\n")
        self.assertIn("pyyaml", deps)
        self.assertIn("requests", deps)

    def test_setup_py_quoted(self):
        deps = ai_review._parse_quoted_deps('install_requires=["pyyaml>=6.0", "requests"]')
        self.assertIn("pyyaml", deps)

    def test_package_json(self):
        facts = {}
        content = '{"dependencies": {"left-pad": "^1.0.0"}}'
        with mock.patch.object(ai_review, "git", side_effect=lambda *a: (
            "package.json\n" if a[0] == "ls-tree" else content
        )):
            facts = ai_review.collect_repo_facts("origin/main", ["package.json"])
        self.assertIn("left-pad", facts["declared_deps"])


class CollectFactsTests(unittest.TestCase):
    def test_facts_come_from_target_branch(self):
        def fake_git(*args):
            if args[0] == "ls-tree":
                return "requirements.txt\nlib/lessons.py\n"
            if args[0] == "show" and args[1] == "origin/main:requirements.txt":
                return "pyyaml>=6.0\n"
            raise ai_review.subprocess.CalledProcessError(1, "git")

        with mock.patch.object(ai_review, "git", side_effect=fake_git):
            facts = ai_review.collect_repo_facts("origin/main", ["lib/lessons.py"])
        self.assertIn("pyyaml", facts["declared_deps"])
        self.assertIn("lib/lessons.py", facts["existing_files"])

    def test_git_failure_yields_empty_not_crash(self):
        with mock.patch.object(ai_review, "git", side_effect=ai_review.subprocess.CalledProcessError(1, "git")):
            facts = ai_review.collect_repo_facts("origin/main", ["a.py"])
        self.assertEqual(facts["declared_deps"], [])


class FactRefutedTests(unittest.TestCase):
    def test_pyyaml_undeclared_is_refuted(self):
        finding = {"title": "lib/lessons.py:1 依赖未声明",
                   "text": "新增模块 import yaml，但 requirements.txt 未包含 pyyaml 依赖"}
        self.assertTrue(ai_review.fact_refuted(finding, _facts()))

    def test_import_alias_refuted(self):
        finding = {"title": "x.py:1 缺少依赖", "text": "import yaml 但依赖文件未声明 yaml"}
        self.assertTrue(ai_review.fact_refuted(finding, _facts()))

    def test_missing_module_refuted_when_file_exists(self):
        finding = {"title": "lib/lessons.py 模块缺失", "text": "引用的 lib/lessons.py 不存在"}
        self.assertTrue(ai_review.fact_refuted(finding, _facts()))

    def test_genuine_logic_bug_not_refuted(self):
        finding = {"title": "lib/x.py:10 空指针", "text": "当 cfg 为 None 时 cfg.get 崩溃"}
        self.assertFalse(ai_review.fact_refuted(finding, _facts()))


class BuildContextTests(unittest.TestCase):
    def test_includes_full_file_and_facts(self):
        with mock.patch.object(ai_review, "_git_show", return_value="print('hi')\n"):
            ctx = ai_review.build_context("HEAD", ["a.py"], _facts(), cap=10000)
        self.assertIn("repo-facts", ctx)
        self.assertIn("pyyaml", ctx)
        self.assertIn("print('hi')", ctx)

    def test_cap_truncates_and_warns(self):
        with mock.patch.object(ai_review, "_git_show", return_value="x" * 5000):
            ctx = ai_review.build_context("HEAD", ["a.py"], _facts(), cap=100)
        self.assertIn("truncated", ctx)


class ClassifyTests(unittest.TestCase):
    def test_blocking_with_scenario_blocks(self):
        f = [{"title": "a.py:1 bug", "severity": "blocking",
              "evidence": {"scenario": "cfg=None 时崩溃"}, "text": "x"}]
        blocking, advisory = ai_review.classify_findings(f, _facts(), [])
        self.assertEqual(len(blocking), 1)
        self.assertEqual(len(advisory), 0)

    def test_blocking_without_scenario_is_advisory(self):
        f = [{"title": "a.py:1 可能 KeyError", "severity": "blocking", "text": "未来若 reason_code 为 None 可能出错"}]
        blocking, advisory = ai_review.classify_findings(f, _facts(), [])
        self.assertEqual(len(blocking), 0)
        self.assertEqual(len(advisory), 1)

    def test_old_schema_is_advisory(self):
        f = [{"title": "a.py:1 问题", "text": "没有 severity 的旧格式"}]
        blocking, advisory = ai_review.classify_findings(f, _facts(), [])
        self.assertEqual(len(blocking), 0)

    def test_refuted_finding_is_advisory(self):
        f = [{"title": "lib/lessons.py:1 依赖未声明", "severity": "blocking",
              "evidence": {"scenario": "import 失败"}, "text": "pyyaml 未声明"}]
        blocking, _ = ai_review.classify_findings(f, _facts(), [])
        self.assertEqual(len(blocking), 0)

    def test_allowlisted_finding_is_advisory(self):
        f = [{"title": "lib/review_strategies.py:156 新增编排", "severity": "blocking",
              "evidence": {"scenario": "x"}, "text": "在 Python 新增编排决策"}]
        allow = [{"file": "lib/review_strategies.py", "keyword": "编排"}]
        blocking, _ = ai_review.classify_findings(f, _facts(), allow)
        self.assertEqual(len(blocking), 0)

    def test_advisory_stays_advisory(self):
        f = [{"title": "a.py:1 建议", "severity": "advisory", "text": "风格"}]
        blocking, advisory = ai_review.classify_findings(f, _facts(), [])
        self.assertEqual(len(blocking), 0)
        self.assertEqual(len(advisory), 1)


class AllowlistTests(unittest.TestCase):
    def test_file_and_keyword_match(self):
        allow = [{"file": "lib/*.py", "keyword": "编排"}]
        self.assertTrue(ai_review.allowlist_hit({"title": "lib/x.py:1 新增编排", "text": ""}, allow))
        self.assertFalse(ai_review.allowlist_hit({"title": "lib/x.py:1 空指针", "text": ""}, allow))
        self.assertFalse(ai_review.allowlist_hit({"title": "other/x.py:1 编排", "text": ""}, allow))

    def test_empty_entry_never_matches_everything(self):
        self.assertFalse(ai_review.allowlist_hit({"title": "a.py:1 x", "text": ""}, [{}]))


class MainIntegrationTests(unittest.TestCase):
    def _run(self, model_response):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            points = Path(tmp) / "points.md"
            points.write_text("## 审查要点\n- 空指针\n", encoding="utf-8")
            output = Path(tmp) / "out.txt"
            argv = ["ai_review.py", "--base", "origin/main", "--head", "HEAD",
                    "--points-file", str(points), "--model", "m",
                    "--allowlist", str(Path(tmp) / "none.yml"),
                    "--output", str(output), "--attempts", "1"]
            with mock.patch.object(ai_review, "branch_diff", return_value=("diff", ["a.py"])), \
                 mock.patch.object(ai_review, "collect_repo_facts", return_value=_facts()), \
                 mock.patch.object(ai_review, "build_context", return_value="ctx"), \
                 mock.patch.object(ai_review, "call_model", return_value=model_response), \
                 mock.patch.object(ai_review.time, "sleep"), \
                 mock.patch.object(sys, "argv", argv), \
                 mock.patch.dict("os.environ", {"AI_REVIEW_API_KEY": "k"}):
                code = ai_review.main()
            return code, output.read_text(encoding="utf-8")

    def test_speculation_only_passes(self):
        resp = '{"findings":[{"title":"a.py:1 可能 KeyError","severity":"blocking","text":"若未来 None 可能出错"}]}'
        code, out = self._run(resp)
        self.assertEqual(code, 0)
        self.assertIn("review=pass", out)

    def test_real_blocking_still_fails(self):
        resp = '{"findings":[{"title":"a.py:1 空指针","severity":"blocking","evidence":{"scenario":"cfg=None 崩溃"},"text":"x"}]}'
        code, out = self._run(resp)
        self.assertEqual(code, 1)
        self.assertIn("review=fail", out)

    def test_pyyaml_false_positive_passes(self):
        resp = '{"findings":[{"title":"lib/lessons.py:1 依赖未声明","severity":"blocking","evidence":{"scenario":"import 失败"},"text":"pyyaml 未声明"}]}'
        code, out = self._run(resp)
        self.assertEqual(code, 0)
        self.assertIn("review=pass", out)


if __name__ == "__main__":
    unittest.main()