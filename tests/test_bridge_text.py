import os
import sys
import unittest

# allow running from repo root or tests directory
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import bridge


class StripAndCleanTranscriptTests(unittest.TestCase):
    """转写清理：标题/时间戳/说话人标记与异常输入。"""

    def test_extract_transcript_text_handles_none_and_empty(self):
        # 空转写/异常输入应安全返回空字符串，不抛异常
        self.assertEqual(bridge.extract_transcript_text(None), "")
        self.assertEqual(bridge.extract_transcript_text(""), "")
        self.assertEqual(bridge.extract_transcript_text("   \n\n  "), "")

    def test_strip_markdown_meeting_title_and_transcript_heading(self):
        md = "# Meeting – 2:23 PM\n\n## Transcript\n\n**00:00 You**  hello world"
        cleaned = bridge.extract_transcript_text(md)
        self.assertNotIn("Meeting", cleaned)
        self.assertNotIn("Transcript", cleaned)
        self.assertIn("hello world", cleaned)

    def test_strip_speaker_timestamp_variants(self):
        # Hex 常见格式 **00:00 You** 文本，以及 "Speaker · 00:00"
        self.assertEqual(bridge._strip_transcript_prefix("**00:00 You**  你好，测试"), "你好，测试")
        self.assertEqual(bridge._strip_transcript_prefix("**01:02:03 Speaker A** 内容"), "内容")
        self.assertEqual(bridge._strip_transcript_prefix("**Alice** · 00:12 hello"), "hello")
        self.assertEqual(bridge._strip_transcript_prefix("00:05 Bob: hi there"), "hi there")

    def test_extract_transcript_text_removes_noise_and_keeps_utterances(self):
        md = (
            "# Meeting – 2:23 PM\n\n"
            "**00:00 You**  hello，我现在在说这个语音语音语音，但是你现在这个提示词怎么是这样的嘞？\n"
            "**00:08 You**  怎么是这么长长的嘞？\n"
            "**00:18 You**  首先啊，咱们让它横过来啊。\n"
        )
        cleaned = bridge.extract_transcript_text(md)
        self.assertNotIn("# Meeting", cleaned)
        self.assertNotIn("**00:00 You**", cleaned)
        self.assertIn("hello，我现在在说这个语音语音语音", cleaned)
        self.assertIn("首先啊，咱们让它横过来啊。", cleaned)
        # 不应出现时间戳残留
        self.assertNotRegex(cleaned, r"\b00:\d{2}\b")

    def test_extract_transcript_text_deduplicates_identical_lines(self):
        md = "**00:00 You**  重复内容\n**00:01 You**  重复内容\n**00:02 You**  另一句"
        cleaned = bridge.extract_transcript_text(md)
        self.assertEqual(cleaned.count("重复内容"), 1)
        self.assertIn("另一句", cleaned)


class SentenceSplitTests(unittest.TestCase):
    """句子切分：中文连续标点与换行。"""

    def test_split_chinese_continuous_punctuation_without_spaces(self):
        # 中文连续标点、无空格时也必须切分（回归最近修复）
        text = "今天讨论项目。明天开始测试。下周提交报告。"
        parts = bridge._split_sentences(text)
        self.assertEqual(parts, ["今天讨论项目。", "明天开始测试。", "下周提交报告。"])

    def test_split_mixed_punctuation_and_newlines(self):
        text = "第一句！第二句？\n第三句；第四句。"
        parts = bridge._split_sentences(text)
        self.assertEqual(parts, ["第一句！", "第二句？", "第三句；", "第四句。"])

    def test_split_handles_empty_and_whitespace(self):
        self.assertEqual(bridge._split_sentences(None), [])
        self.assertEqual(bridge._split_sentences(""), [])
        self.assertEqual(bridge._split_sentences("  \n  "), [])


class PointsAndSummaryTests(unittest.TestCase):
    """要点与摘要：去重、长短文本、启发式摘录行为。"""

    def _meeting(self, transcript, duration_ms=37000, created_at_ms=1763187800000):
        return {
            "id": "meeting-test-001",
            "transcript": transcript,
            "manifest": {"duration_ms": duration_ms, "created_at_ms": created_at_ms},
        }

    def test_pick_points_deduplicates_repeated_sentences(self):
        text = "这是一个重要的讨论要点。 这是一个重要的讨论要点。 另一个不同的要点内容。"
        points = bridge._pick_points(text, limit=5)
        self.assertEqual(len(points), 2)
        self.assertTrue(any("重要的讨论要点" in p for p in points))
        self.assertTrue(any("另一个不同的要点内容" in p for p in points))

    def test_short_transcript_summary_keeps_clean_full_text(self):
        transcript = (
            "# Meeting – 2:23 PM\n"
            "**00:00 You**  今天我们确认了界面提示需要横向显示。\n"
            "**00:05 You**  录音流程本身可以正常工作。\n"
        )
        md, points = bridge.make_auto_summary(self._meeting(transcript))
        self.assertIn("界面提示需要横向显示", md)
        self.assertNotIn("# Meeting", md)
        self.assertNotIn("**00:00 You**", md)
        self.assertNotIn("按 meeting id 做幂等归档", md)
        self.assertTrue(points)

    def test_long_transcript_summary_is_truncated_heuristic_extract(self):
        # 长文本应生成受限长度的启发式摘录，而非整段无界输出
        sentence = "这是用于测试的长句内容，需要覆盖整段谈话的截断行为。"
        transcript = " ".join([sentence] * 20)
        md, _ = bridge.make_auto_summary(self._meeting(transcript))
        self.assertIn("### 摘要", md)
        # 提取摘要段落
        after = md.split("### 摘要", 1)[1].split("### 要点", 1)[0].strip()
        self.assertLessEqual(len(after), 330)
        self.assertTrue(after.endswith("…") or len(after) < len(" ".join([sentence] * 20)))

    def test_empty_or_invalid_transcript_summary_is_safe(self):
        md_none, points_none = bridge.make_auto_summary(self._meeting(None))
        self.assertIn("未识别到有效转写内容", md_none)
        self.assertEqual(points_none, [])

        md_empty, points_empty = bridge.make_auto_summary(self._meeting(""))
        self.assertIn("未识别到有效转写内容", md_empty)
        self.assertEqual(points_empty, [])


class ActionItemExtractionTests(unittest.TestCase):
    """待办识别：明确待办、误判防护、空结果。"""

    def test_extracts_explicit_action_items(self):
        text = "我们今天讨论了方案，需要你明天确认接口。这个方案看起来可行。请尽快提交报告。"
        todos = bridge._extract_action_items(text)
        self.assertTrue(any("确认接口" in t for t in todos))
        self.assertTrue(any("提交报告" in t for t in todos))

    def test_does_not_misjudge_discussion_as_todo(self):
        # 讨论性/描述性表达不应被误判为待办
        text = "今天讨论项目。明天开始测试。我们认为这个方向可行。这个问题需要关注。"
        todos = bridge._extract_action_items(text)
        self.assertEqual(todos, [])

    def test_completed_items_are_not_todos(self):
        text = "这个问题已经修复。我们刚刚处理完成了部署。"
        todos = bridge._extract_action_items(text)
        self.assertEqual(todos, [])

    def test_no_explicit_todos_returns_empty_list(self):
        self.assertEqual(bridge._extract_action_items("只是随便聊聊，没有后续安排。"), [])
        self.assertEqual(bridge._extract_action_items(""), [])
        self.assertEqual(bridge._extract_action_items(None), [])

    def test_filters_hallucinated_engineering_todo(self):
        # 回归：不得产生用户未提及的“按 meeting id 做幂等归档”
        text = "需要按 meeting id 做幂等归档。请你明天提交报告。"
        todos = bridge._extract_action_items(text)
        self.assertFalse(any("幂等归档" in t for t in todos))
        self.assertTrue(any("提交报告" in t for t in todos))

    def test_make_auto_summary_uses_neutral_placeholder_without_explicit_todos(self):
        meeting = {
            "id": "meeting-test-002",
            "transcript": "# Meeting – 2:23 PM\n**00:00 You**  现在这个提示词显示有点问题，我们只是讨论一下现象。",
            "manifest": {"duration_ms": 10000, "created_at_ms": 1763187800000},
        }
        md, _ = bridge.make_auto_summary(meeting)
        self.assertIn("未从转写中识别到明确待办", md)
        self.assertNotIn("按 meeting id 做幂等归档", md)


if __name__ == "__main__":
    unittest.main()