"""Ollama 自愈测试：server 没起来时 bridge 应该主动拉起它。

为什么重要：Ollama 是独立进程，会被关掉（或随崩溃的终端一起死）。
它一死，summary 就静默降级成 rules —— 用户看到的正是"要点还是残句"。
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bridge  # noqa: E402


class EnsureOllamaTests(unittest.TestCase):
    def setUp(self):
        self._old_cfg = dict(bridge.SUMMARY_CFG)

    def tearDown(self):
        bridge.SUMMARY_CFG.clear()
        bridge.SUMMARY_CFG.update(self._old_cfg)

    def _set_mode(self, mode):
        bridge.SUMMARY_CFG["summary_mode"] = mode

    def test_rules_mode_is_noop(self):
        """rules 模式不该碰网络、也不该启动进程。"""
        self._set_mode("rules")
        with mock.patch.object(bridge.subprocess, "Popen",
                               side_effect=AssertionError("rules 模式不应启动进程")):
            self.assertIsNone(bridge.ensure_ollama())

    def test_already_running(self):
        self._set_mode("ollama")
        with mock.patch("scripts.summarizer._preflight_ollama", return_value=True):
            self.assertEqual(bridge.ensure_ollama(), "running")

    def test_starts_ollama_when_down(self):
        self._set_mode("ollama")
        calls = {"n": 0}

        def flaky(base, timeout=2.0):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("not reachable")
            return True

        fake_exe = mock.MagicMock()
        with mock.patch("scripts.summarizer._preflight_ollama", side_effect=flaky), \
             mock.patch.object(bridge, "_ollama_exe_candidates", return_value=[fake_exe]), \
             mock.patch.object(bridge.os.path, "isfile", return_value=True), \
             mock.patch.object(bridge.subprocess, "Popen") as popen:
            state = bridge.ensure_ollama(wait_sec=3)
        self.assertEqual(state, "started")
        popen.assert_called_once()
        argv = popen.call_args[0][0]
        self.assertEqual(argv[1], "serve")
        self.assertEqual(popen.call_args[1]["stdin"], bridge.subprocess.DEVNULL,
                         "必须脱离父进程 stdio，否则隐藏窗口会一直持有管道")

    def test_missing_binary_is_reported_not_raised(self):
        self._set_mode("ollama")
        with mock.patch("scripts.summarizer._preflight_ollama",
                        side_effect=RuntimeError("not reachable")), \
             mock.patch.object(bridge, "_ollama_exe_candidates", return_value=["nope"]), \
             mock.patch.object(bridge.os.path, "isfile", return_value=False):
            self.assertEqual(bridge.ensure_ollama(wait_sec=1), "missing")

    def test_never_answers_is_reported(self):
        self._set_mode("ollama")
        fake_exe = mock.MagicMock()
        with mock.patch("scripts.summarizer._preflight_ollama",
                        side_effect=RuntimeError("not reachable")), \
             mock.patch.object(bridge, "_ollama_exe_candidates", return_value=[fake_exe]), \
             mock.patch.object(bridge.os.path, "isfile", return_value=True), \
             mock.patch.object(bridge.subprocess, "Popen"), \
             mock.patch.object(bridge.time, "sleep"):
            self.assertEqual(bridge.ensure_ollama(wait_sec=1), "failed")

    def test_llm_alias_also_needs_ollama(self):
        self._set_mode("llm")
        with mock.patch("scripts.summarizer._preflight_ollama", return_value=True):
            self.assertEqual(bridge.ensure_ollama(), "running")


if __name__ == "__main__":
    unittest.main()
