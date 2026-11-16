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

    def test_max_tokens_override_is_honoured(self):
        """--max-tokens / config ai_review.max_tokens 要真正透传进请求体。"""
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": '{"findings": []}'}}]})

        def urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data)
            return Response()

        with mock.patch.object(ai_review.urllib.request, "urlopen", urlopen):
            ai_review.call_model("https://api.example/v1/", "m", "p", "k", 10, 32768)
        self.assertEqual(captured["body"]["max_tokens"], 32768)

    def test_bad_choice_shape_raises(self):
        with mock.patch.object(
            ai_review.urllib.request,
            "urlopen",
            side_effect=ai_review.urllib.error.URLError("boom"),
        ):
            with self.assertRaises(ai_review.urllib.error.URLError):
                ai_review.call_model("https://x", "m", "p", "k", 1)


class GitDecodeTests(unittest.TestCase):
    def test_git_decodes_as_utf8(self):
        """Windows 默认 GBK 解码，中文 commit/文件名会直接崩；必须显式 utf-8。"""
        import types

        with mock.patch.object(
            ai_review.subprocess, "run",
            return_value=types.SimpleNamespace(stdout="中文 diff"),
        ) as run:
            out = ai_review.git("log", "-1")
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs.get("encoding"), "utf-8")
        self.assertEqual(kwargs.get("errors"), "replace")
        self.assertEqual(out, "中文 diff")


class RetryTests(unittest.TestCase):
    """端点偶发超时是真实故障（跨境链路 8 分钟后 timeout），必须退避重试而不是直接 fail-closed。"""

    def _run_main(self, side_effect, extra=()):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            points = Path(tmp) / "points.md"
            points.write_text("## 审查要点\n- 空指针\n", encoding="utf-8")
            output = Path(tmp) / "out.txt"
            argv = [
                "ai_review.py", "--base", "origin/main", "--head", "HEAD",
                "--points-file", str(points), "--model", "glm-5.3",
                "--output", str(output), "--attempts", "3",
            ] + list(extra)
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


class DeadlineTests(unittest.TestCase):
    """服务端慢慢吐 token 时 socket 超时不会触发，必须有墙钟上限。"""

    def test_slow_call_is_abandoned(self):
        def slow(*args, **kwargs):
            import time as _t
            _t.sleep(5)
            return "too late"

        with mock.patch.object(ai_review, "call_model", side_effect=slow):
            with self.assertRaises(TimeoutError):
                ai_review.call_with_deadline("u", "m", "p", "k", 0.05)

    def test_worker_error_surfaces(self):
        def boom(*args, **kwargs):
            raise ValueError("bad json")

        with mock.patch.object(ai_review, "call_model", side_effect=boom):
            with self.assertRaises(ValueError):
                ai_review.call_with_deadline("u", "m", "p", "k", 1)


class FallbackModelTests(unittest.TestCase):
    """主力模型在大 diff 上会截断（finish_reason=length），第二次必须换备用模型。"""

    def test_second_attempt_uses_fallback_model(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            points = Path(tmp) / "points.md"
            points.write_text("## 审查要点\n- 空指针\n", encoding="utf-8")
            argv = [
                "ai_review.py", "--base", "origin/main", "--head", "HEAD",
                "--points-file", str(points), "--model", "glm-5.3",
                "--fallback-model", "deepseek-v4-pro", "--attempts", "2",
            ]
            seen = []

            def fake_call(base_url, model, prompt, key, timeout, max_tokens=16384):
                seen.append(model)
                if len(seen) == 1:
                    raise ValueError("输出被截断（finish_reason=length）")
                return '{"findings": []}'

            with mock.patch.object(ai_review, "branch_diff", return_value=("diff", ["a.py"])),                  mock.patch.object(ai_review, "call_model", side_effect=fake_call),                  mock.patch.object(ai_review.time, "sleep"),                  mock.patch.object(sys, "argv", argv),                  mock.patch.dict("os.environ", {"AI_REVIEW_API_KEY": "k"}):
                code = ai_review.main()
        self.assertEqual(code, 0)
        self.assertEqual(seen, ["glm-5.3", "deepseek-v4-pro"])


class Utf8StdioTests(unittest.TestCase):
    """Windows 控制台默认 GBK，打印含特殊字符的 findings 会崩：main() 必须先把输出切成 UTF-8。"""

    def test_reconfigures_both_streams(self):
        calls = []

        class Fake:
            def reconfigure(self, **kwargs):
                calls.append(kwargs)

        with mock.patch.object(ai_review.sys, "stdout", Fake()),              mock.patch.object(ai_review.sys, "stderr", Fake()):
            ai_review.use_utf8_stdio()
        self.assertEqual(len(calls), 2)
        for kwargs in calls:
            self.assertEqual(kwargs["encoding"], "utf-8")
            self.assertEqual(kwargs["errors"], "replace")

    def test_stream_without_reconfigure_is_tolerated(self):
        # 被重定向到不支持 reconfigure 的流时不应抛异常
        class Bare:
            pass

        with mock.patch.object(ai_review.sys, "stdout", Bare()),              mock.patch.object(ai_review.sys, "stderr", Bare()):
            ai_review.use_utf8_stdio()


if __name__ == "__main__":
    unittest.main()