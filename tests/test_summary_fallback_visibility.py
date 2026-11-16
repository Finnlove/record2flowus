"""摘要降级可见性测试：模型不可用时，页面上必须能看到降级提示。

背景：bridge 用 pythonw 隐藏窗口运行，stderr 没人看；用户唯一能看到的是
FlowUs 页面。若降级静默发生，用户会以为"要点还是残句=没修好"。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bridge  # noqa: E402

MEETING = {
    "id": "m-test",
    "manifest": {"title": "T", "duration_ms": 60000, "created_at_ms": 1700000000000},
    "transcript": "第一段转写内容。第二段转写内容。",
}


class SummaryFallbackVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_artifacts = bridge.ARCHIVE_ARTIFACTS_DIR
        bridge.ARCHIVE_ARTIFACTS_DIR = self.tmp
        self._patches = [
            mock.patch.object(bridge, "load_meeting_artifacts", return_value=MEETING),
            mock.patch.object(bridge, "make_auto_summary",
                              return_value=("## rules md\n- 要点", ["要点"])),
            mock.patch.object(bridge, "make_auto_mindmap", return_value={"root": {}}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        bridge.ARCHIVE_ARTIFACTS_DIR = self._old_artifacts
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _prepare(self, sres):
        with mock.patch.object(bridge, "generate_summary", return_value=sres):
            _, job_dir, sum_path, _, meta = bridge._prepare_job_artifacts(
                "m-test", "vnarc::append::m-test::p-test", "p-test", force_regen=True)
        with open(sum_path, encoding="utf-8") as f:
            return f.read().strip(), meta

    def test_fallback_is_visible_on_page(self):
        md, meta = self._prepare({
            "ok": True, "mode": "rules", "summary_md": "## rules md\n- 要点",
            "points": ["要点"], "fallback": True,
            "fallback_reason": "ollama server not reachable", "requested_mode": "ollama",
        })
        self.assertIn("规则模式", md, "降级必须写在页面内容里")
        self.assertIn("ollama server not reachable", md, "要写出具体原因，便于自查")
        self.assertIn("regenerate", md, "要告诉用户怎么恢复")
        self.assertTrue(meta["summary_fallback"])
        self.assertEqual(meta["summary_mode"], "rules")
        self.assertTrue(md.endswith("- 要点"), "提示是前缀，不能破坏正文结构")

    def test_success_has_no_warning_banner(self):
        md, meta = self._prepare({
            "ok": True, "mode": "ollama", "summary_md": "## 模型摘要\n- 要点",
            "points": ["要点"], "fallback": False, "model": "qwen2.5:3b",
        })
        self.assertNotIn("规则模式", md)
        self.assertNotIn("⚠️", md)
        self.assertFalse(meta["summary_fallback"])
        self.assertEqual(meta["summary_mode"], "ollama")
        self.assertEqual(meta["summary_model"], "qwen2.5:3b")

    def test_long_reason_is_truncated(self):
        md, _ = self._prepare({
            "ok": True, "mode": "rules", "summary_md": "md", "points": [],
            "fallback": True, "fallback_reason": "x" * 500,
        })
        self.assertLessEqual(len(md.splitlines()[0]), 400)


if __name__ == "__main__":
    unittest.main()
