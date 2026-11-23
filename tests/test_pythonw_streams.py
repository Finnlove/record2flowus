"""pythonw.exe (autostart) 下 stdout/stderr 为 None 的兜底测试。

背景：自启 VBS 用 pythonw.exe 启动 bridge，无控制台 → sys.stderr=None。
BaseHTTPRequestHandler.log_message() 每次请求都写 stderr → AttributeError →
连接被直接关闭 → 扩展报「本地桥接失败」。_ensure_streams() 把 None 替换成
devnull，使 pythonw 与正常启动行为一致。
"""

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bridge  # noqa: E402


class EnsureStreamsTests(unittest.TestCase):
    def test_replaces_none_streams(self):
        old_out, old_err = sys.stdout, sys.stderr
        try:
            sys.stdout = None
            sys.stderr = None
            bridge._ensure_streams()
            self.assertIsNotNone(sys.stdout, "stdout=None 必须被替换")
            self.assertIsNotNone(sys.stderr, "stderr=None 必须被替换")
            # 必须可写，模拟 log_message 的写入
            sys.stderr.write("[bridge] test\n")
            sys.stdout.write("test\n")
            new_out, new_err = sys.stdout, sys.stderr
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        new_out.close()
        new_err.close()

    def test_keeps_existing_streams(self):
        old_out, old_err = sys.stdout, sys.stderr
        sentinel_out = io.StringIO()
        sentinel_err = io.StringIO()
        try:
            sys.stdout, sys.stderr = sentinel_out, sentinel_err
            bridge._ensure_streams()
            self.assertIs(sys.stdout, sentinel_out, "已有 stdout 不能被换掉")
            self.assertIs(sys.stderr, sentinel_err, "已有 stderr 不能被换掉")
        finally:
            sys.stdout, sys.stderr = old_out, old_err

    def test_only_none_gets_replaced(self):
        """一个为 None、一个正常时，只替换 None 的那个。"""
        old_out, old_err = sys.stdout, sys.stderr
        sentinel_out = io.StringIO()
        new_err = None
        try:
            sys.stdout = sentinel_out
            sys.stderr = None
            bridge._ensure_streams()
            self.assertIs(sys.stdout, sentinel_out)
            self.assertIsNotNone(sys.stderr)
            new_err = sys.stderr
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        if new_err is not None:
            new_err.close()


class HandlerLoggingTests(unittest.TestCase):
    def test_log_message_survives_missing_stderr(self):
        """核心回归：stderr=None 时 log_message 不能抛异常（=请求不会被掐断）。"""
        old_err = sys.stderr
        new_err = None
        try:
            sys.stderr = None
            bridge._ensure_streams()
            new_err = sys.stderr
            # 直接调 Handler.log_message，不应抛
            bridge.Handler.log_message(
                type("H", (), {})(),  # self 只需是个对象
                '"GET /ping HTTP/1.1" 200 -',
            )
        finally:
            sys.stderr = old_err
        if new_err is not None:
            new_err.close()


if __name__ == "__main__":
    unittest.main()
