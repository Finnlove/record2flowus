"""Ollama 摘要路径回归测试（纯标准库，全部 mock，无网络依赖）。

锁定三件事：
1. Ollama 未启动时必须**快速失败**并给出可操作提示，而不是白等完整超时。
2. prompt 必须显式要求「通顺书面语 / 必须有主语和谓语 / 修复 ASR 断字」——
   否则模型会照抄口语碎片，要点又是残句（这是 §7.3 的复发点）。
3. 默认模型是 qwen2.5:3b（7b 在 8GB 内存机器上跑不动，已实测超时）。

运行：python -m unittest tests.test_summarizer_ollama -v
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import summarizer  # noqa: E402


def _meeting(mid="m1", title="语音测试", duration_ms=185000, created_at_ms=1700000000000):
    return {
        "id": mid,
        "manifest": {
            "title": title,
            "duration_ms": duration_ms,
            "created_at_ms": created_at_ms,
        },
    }


class _FakeResp:
    def __init__(self, payload):
        self._data = payload

    def read(self, n=None):
        return self._data if n is None else self._data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PreflightTests(unittest.TestCase):
    def test_unreachable_raises_fast_with_actionable_hint(self):
        with mock.patch.object(summarizer.urllib.request, "urlopen",
                               side_effect=OSError("connection refused")):
            with self.assertRaises(RuntimeError) as ctx:
                summarizer._preflight_ollama("http://127.0.0.1:11434")
        msg = str(ctx.exception)
        self.assertIn("not reachable", msg)
        self.assertIn("ollama serve", msg, "提示必须告诉用户怎么修")
        self.assertIn("summary_mode=rules", msg, "必须给出降级选项")

    def test_reachable_returns_true(self):
        with mock.patch.object(summarizer.urllib.request, "urlopen",
                               return_value=_FakeResp(b'{"models":[]}')):
            self.assertTrue(summarizer._preflight_ollama("http://127.0.0.1:11434"))

    def test_preflight_probes_api_tags(self):
        seen = {}

        def fake(req, timeout=None):
            seen["url"] = getattr(req, "full_url", req)
            return _FakeResp(b"{}")

        with mock.patch.object(summarizer.urllib.request, "urlopen", side_effect=fake):
            summarizer._preflight_ollama("http://127.0.0.1:11434")
        self.assertTrue(seen["url"].endswith("/api/tags"))


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.prompt = summarizer._build_ollama_prompt(_meeting(), "测试转写内容")

    def test_demands_written_chinese(self):
        self.assertIn("书面语", self.prompt)
        self.assertIn("不要逐句照抄", self.prompt)

    def test_demands_complete_sentences(self):
        self.assertIn("主语和谓语", self.prompt,
                      "缺少这条，模型会输出残句要点（§7.3 复发）")
        self.assertIn("不要照搬口语碎片", self.prompt)

    def test_demands_asr_repair(self):
        self.assertIn("ASR", self.prompt)
        self.assertIn("G G GPT", self.prompt)
        self.assertIn("不要改变原意", self.prompt, "修复不能变成改写原文")

    def test_requests_json_only(self):
        self.assertIn("只输出 JSON", self.prompt)
        self.assertIn('"points"', self.prompt)
        self.assertIn('"todos"', self.prompt)

    def test_carries_meeting_meta_and_transcript(self):
        self.assertIn("m1", self.prompt)
        self.assertIn("语音测试", self.prompt)
        self.assertIn("03:05", self.prompt)
        self.assertIn("测试转写内容", self.prompt)


class ParseResultTests(unittest.TestCase):
    def test_plain_json(self):
        got = summarizer._parse_model_result(
            '{"summary":"摘要","points":["要点一"],"todos":[]}')
        self.assertEqual(got["summary"], "摘要")
        self.assertEqual(got["points"], ["要点一"])
        self.assertEqual(got["todos"], [])

    def test_json_inside_prose(self):
        """模型常加解释文字，必须能捞出来。"""
        got = summarizer._parse_model_result(
            '好的，结果如下：\n{"summary":"S","points":["P"],"todos":["T"]}\n以上。')
        self.assertEqual(got["summary"], "S")
        self.assertEqual(got["todos"], ["T"])

    def test_garbage_returns_none(self):
        self.assertIsNone(summarizer._parse_model_result("完全不是 JSON"))
        self.assertIsNone(summarizer._parse_model_result(""))

    def test_string_instead_of_list_is_wrapped(self):
        got = summarizer._parse_model_result(
            '{"summary":"S","points":"单条要点","todos":"单条待办"}')
        self.assertEqual(got["points"], ["单条要点"])
        self.assertEqual(got["todos"], ["单条待办"])

    def test_items_are_capped(self):
        got = summarizer._parse_model_result(json.dumps({
            "summary": "S", "points": ["p%d" % i for i in range(20)], "todos": []}))
        self.assertEqual(len(got["points"]), 8)


class OllamaCallTests(unittest.TestCase):
    def _capture(self, response_text=None):
        captured = {}
        text = response_text if response_text is not None else json.dumps({
            "summary": "模型摘要",
            "points": ["模型要点一", "模型要点二"],
            "todos": ["模型待办"],
        })

        def fake(req, timeout=None):
            if getattr(req, "data", None) is None:   # GET /api/tags preflight
                return _FakeResp(b'{"models":[]}')
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeResp(json.dumps({"response": text}).encode("utf-8"))

        return captured, fake

    def test_default_model_is_3b(self):
        """7b 在 8GB 内存机器上实测 215s 超时，默认必须是 3b。"""
        captured, fake = self._capture()
        with mock.patch.object(summarizer.urllib.request, "urlopen", side_effect=fake):
            summarizer.summarize_with_ollama(_meeting(), "转写")
        self.assertEqual(captured["body"]["model"], "qwen2.5:3b")

    def test_default_timeout_is_180(self):
        captured, fake = self._capture()
        with mock.patch.object(summarizer.urllib.request, "urlopen", side_effect=fake):
            summarizer.summarize_with_ollama(_meeting(), "转写")
        self.assertEqual(captured["timeout"], 180)

    def test_payload_is_kept_warm_and_bounded(self):
        captured, fake = self._capture()
        with mock.patch.object(summarizer.urllib.request, "urlopen", side_effect=fake):
            summarizer.summarize_with_ollama(_meeting(), "转写")
        body = captured["body"]
        self.assertEqual(body["keep_alive"], "10m", "避免每次冷加载 ~7s")
        self.assertEqual(body["options"]["num_predict"], 900)
        self.assertEqual(body["format"], "json")
        self.assertFalse(body["stream"])

    def test_config_overrides_model_and_timeout(self):
        captured, fake = self._capture()
        with mock.patch.object(summarizer.urllib.request, "urlopen", side_effect=fake):
            summarizer.summarize_with_ollama(
                _meeting(), "转写", {"ollama_model": "custom:1b", "ollama_timeout": 42})
        self.assertEqual(captured["body"]["model"], "custom:1b")
        self.assertEqual(captured["timeout"], 42)

    def test_success_shape(self):
        _, fake = self._capture()
        with mock.patch.object(summarizer.urllib.request, "urlopen", side_effect=fake):
            res = summarizer.summarize_with_ollama(_meeting(), "转写")
        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "ollama")
        self.assertFalse(res["fallback"])
        self.assertEqual(res["points"], ["模型要点一", "模型要点二"])
        self.assertIn("### 摘要", res["summary_md"])
        self.assertIn("模型摘要", res["summary_md"])


class GenerateSummaryFallbackTests(unittest.TestCase):
    def setUp(self):
        self.rules_result = {"ok": True, "mode": "rules", "summary_md": "RULES", "points": [], "todos": []}
        self.rules_calls = []
        def fake_rules(meeting):
            self.rules_calls.append(meeting.get("id"))
            return ("RULES-MD", ["要点"])

        self.fake_rules = fake_rules

    def test_ollama_down_falls_back_to_rules_with_reason(self):
        with mock.patch.object(summarizer.urllib.request, "urlopen",
                               side_effect=OSError("connection refused")):
            res = summarizer.generate_summary(
                _meeting(), "转写", {"summary_mode": "ollama"},
                make_auto_summary_fn=self.fake_rules)
        self.assertEqual(res["mode"], "rules")
        self.assertTrue(res["fallback"])
        self.assertEqual(res["requested_mode"], "ollama")
        self.assertIn("not reachable", res["fallback_reason"])
        self.assertEqual(len(self.rules_calls), 1)

    def test_rules_mode_does_not_touch_network(self):
        def boom(*a, **k):
            raise AssertionError("rules 模式不应该请求网络")

        with mock.patch.object(summarizer.urllib.request, "urlopen", side_effect=boom):
            res = summarizer.generate_summary(
                _meeting(), "转写", {"summary_mode": "rules"},
                make_auto_summary_fn=self.fake_rules)
        self.assertEqual(res["mode"], "rules")
        self.assertFalse(res.get("fallback"))

    def test_llm_mode_alias_uses_ollama(self):
        called = {}

        def fake_ollama(meeting, transcript, cfg=None):
            called["hit"] = True
            return {"ok": True, "mode": "ollama", "summary_md": "X", "points": [], "todos": []}

        with mock.patch.object(summarizer, "summarize_with_ollama", side_effect=fake_ollama):
            res = summarizer.generate_summary(
                _meeting(), "转写", {"summary_mode": "llm"},
                make_auto_summary_fn=self.fake_rules)
        self.assertTrue(called.get("hit"))
        self.assertEqual(res["mode"], "ollama")


if __name__ == "__main__":
    unittest.main()
