from __future__ import annotations

import importlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

ai_review = importlib.import_module("ai_review")


class InternalOnlyTests(unittest.TestCase):
    def test_all_files_must_match(self):
        self.assertTrue(ai_review.is_internal_only(["docs/a.md"], ["**/*.md"]))
        self.assertFalse(ai_review.is_internal_only(["docs/a.md", "src/b.py"], ["**/*.md"]))

    def test_multiple_patterns_allow_any(self):
        self.assertTrue(ai_review.is_internal_only(["docs/a.md", "README.md"], ["docs/*.md", "README*.md"]))
        self.assertFalse(ai_review.is_internal_only(["ops/review-points.md"], ["docs/*.md", "README*.md"]))

    def test_empty_never_internal(self):
        self.assertFalse(ai_review.is_internal_only([], ["**/*.md"]))
        self.assertFalse(ai_review.is_internal_only(["a.py"], []))


class ExtractFindingsTests(unittest.TestCase):
    def test_plain_json(self):
        findings = ai_review.extract_findings('{"findings": [{"title": "a.py:1 空指针", "text": "x 为 None 时崩溃"}]}')
        self.assertEqual(len(findings), 1)

    def test_fenced_json(self):
        text = '结果如下\n```json\n{"findings": []}\n```\n完毕'
        self.assertEqual(ai_review.extract_findings(text), [])

    def test_malformed_raises(self):
        for bad in ("没有 JSON", "[]", '{"findings": "x"}', '{"findings": [{"title": "缺text"}]}'):
            with self.assertRaises(ValueError):
                ai_review.extract_findings(bad)


class CallModelTests(unittest.TestCase):
    def test_posts_openai_compatible_request(self):
        captured = {}

        class Response:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return self.body

        def urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data)
            captured["auth"] = request.headers.get("Authorization")
            return Response(json.dumps({"choices": [{"message": {"content": '{"\"findings\": []}'}}]}))

        with mock.patch.object(ai_review.urllib.request, "urlopen", urlopen):
            text = ai_review.call_model("https://api.example/v1/", "glm-5.3", "prompt", "key", 10)
        self.assertEqual(captured["url"], "https://api.example/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "glm-5.3")
        self.assertEqual(captured["body"]["temperature"], 0)
        self.assertEqual(captured["body"]["response_format"], {"type": "json_object"})
        # 推理型模型的 reasoning 也吃 max_tokens，太小会把正文截断
        self.assertGreaterEqual(captured["body"]["max_tokens"], 16384)
        self.assertEqual(captured["auth"], "Bearer key")
        self.assertTrue(text)

    def test_bad_choice_shape_raises(self):
        with mock.patch.object(
            ai_review.urllib.request,
            "urlopen",
            side_effect=ai_review.urllib.error.URLError("boom"),
        ):
            with self.assertRaises(ai_review.urllib.error.URLError):
                ai_review.call_model("https://x", "m", "p", "k", 1)


class RetryTests(unittest.TestCase):
    """端点偶发超时是真实故障（跨境链路 8 分钟后 timeout），必须退避重试而不是直接 fail-closed。"""

    def _run_main(self, side_effect):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            points = Path(tmp) / "points.md"
            points.write_text("## 审查要点\n- 空指针\n", encoding="utf-8")
            output = Path(tmp) / "out.txt"
            argv = [
                "ai_review.py", "--base", "origin/main", "--head", "HEAD",
                "--points-file", str(points), "--model", "glm-5.3",
                "--output", str(output), "--attempts", "3",
            ]
            with mock.patch.object(ai_review, "branch_diff", return_value=("diff", ["a.py"])), \
                 mock.patch.object(ai_review, "call_model", side_effect=side_effect) as model, \
                 mock.patch.object(ai_review.time, "sleep"), \
                 mock.patch.object(sys, "argv", argv), \
                 mock.patch.dict("os.environ", {"AI_REVIEW_API_KEY": "k"}):
                code = ai_review.main()
            return code, model.call_count, output.read_text(encoding="utf-8")

    def test_timeout_then_success(self):
        code, calls, out = self._run_main([TimeoutError("timed out"), '{"findings": []}'])
        self.assertEqual(code, 0)
        self.assertEqual(calls, 2)
        self.assertIn("review=pass", out)

    def test_gives_up_after_attempts(self):
        code, calls, out = self._run_main([TimeoutError("timed out")] * 3)
        self.assertEqual(code, 1)
        self.assertEqual(calls, 3)
        self.assertIn("review=fail", out)


if __name__ == "__main__":
    unittest.main()