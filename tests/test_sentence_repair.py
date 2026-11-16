# -*- coding: utf-8 -*-
"""断句修复 / 后续任务识别 回归测试（纯标准库）。

背景：ASR 在停顿处插入句号，于是"他会进行他的一个。"这种悬空句被当成完整句子，
直接进了要点；同时跨段的后续任务被切成两半，两半都匹配不到线索词，导致待办为空。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bridge  # noqa: E402


class HangingTailTests(unittest.TestCase):
    def test_measure_word_tail_is_truncated_even_with_period(self):
        """句末是量词壳（一个/这个）时，句号不能把它变成完整句。"""
        self.assertTrue(bridge._looks_truncated("完了之后记录之后呢，他会进行他的一个。"))
        self.assertTrue(bridge._looks_truncated("那么等一下就是第四轮的结果我会发回给这个。"))
        self.assertTrue(bridge._looks_truncated("让这个智能体直接去。"))

    def test_complete_sentence_is_not_truncated(self):
        """回归：正则缺 $ 锚点时，句中任意"这个/一个"都会让所有句子被判成残句。"""
        for s in (
            "这是一个重要的讨论要点。",
            "另一个不同的要点内容。",
            "第四轮是ED的实测，我们随便说一点什么东西，让它进行记录分析。",
            "现在是实测下来已经到了这个可以使用的这一步了。",
        ):
            self.assertFalse(bridge._looks_truncated(s), s)

    def test_fragment_never_becomes_a_point(self):
        self.assertFalse(bridge._is_complete_enough("完了之后记录之后呢，他会进行他的一个。"))


class SentenceJoinTests(unittest.TestCase):
    def test_hanging_tail_merges_with_next_clause(self):
        text = "他会进行他的一个。提高我们的工作效率。"
        sents = bridge._split_sentences(text)
        self.assertEqual(len(sents), 1)
        self.assertIn("提高我们的工作效率", sents[0])
        # 量词已经绑定，不该硬塞逗号
        self.assertNotIn("一个，提高", sents[0])

    def test_latin_start_drops_orphaned_determiner(self):
        text = "我会发回给这个。GPT，让他继续进行审计。"
        sents = bridge._split_sentences(text)
        self.assertEqual(len(sents), 1)
        self.assertIn("发回给GPT", sents[0])
        self.assertNotIn("这个。", sents[0])

    def test_complete_sentences_stay_separate(self):
        text = "这是一个重要的讨论要点。另一个不同的要点内容。"
        self.assertEqual(len(bridge._split_sentences(text)), 2)

    def test_merged_sentence_is_not_broken_by_period(self):
        """合并后句中的旧句号必须被去掉，否则读起来仍是断句。"""
        sents = bridge._split_sentences("让这个智能体直接去。只给他一个仓库地址。")
        self.assertEqual(len(sents), 1)
        self.assertNotIn("去。只给他", sents[0])


class ActionItemTests(unittest.TestCase):
    def test_next_step_intent_without_keyword_is_detected(self):
        """真实案例：整句被 ASR 切成两半时，待办曾整条丢失。"""
        text = (
            "目前来说一切良好，以上的语音已经说完了，那么等一下就是第四轮的结果我会发回给这个。"
            "GPT，让他跟我继续进行这个项目的审计，看看还有什么需要完善的地方。"
        )
        todos = bridge._extract_action_items(text)
        self.assertEqual(len(todos), 1, todos)
        self.assertIn("审计", todos[0])
        self.assertIn("GPT", todos[0])

    def test_narration_is_not_an_action_item(self):
        text = "但是目前来说，本仓库的完成度已经比较高了。"
        self.assertEqual(bridge._extract_action_items(text), [])

    def test_lead_in_narration_is_trimmed_from_item(self):
        text = "目前来说一切良好。那么等一下就是结果我会发回给 GPT，让他继续进行审计。"
        todos = bridge._extract_action_items(text)
        self.assertEqual(len(todos), 1)
        self.assertTrue(todos[0].startswith("等一下"), todos[0])


class PointFormattingTests(unittest.TestCase):
    def test_clause_cut_is_marked_open_not_closed(self):
        """从句级截断必须留"…"，不能补句号假装成完整的一句话。"""
        long_s = "这是一段很长的转写内容，" * 8
        p = bridge._format_point(long_s, limit=40)
        self.assertTrue(p.endswith("…"), p)

    def test_full_sentence_keeps_period(self):
        p = bridge._format_point("第四轮实验，是换了一个地址，就是换了一台电脑。", limit=88)
        self.assertTrue(p.endswith("。"), p)


if __name__ == "__main__":
    unittest.main()
