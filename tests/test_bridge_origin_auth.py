"""Bridge Origin / Token 鉴权回归测试（纯标准库，无第三方依赖）。

锁定 Chrome 157+ 的关键行为：扩展 service worker 发出的 fetch 不带 Origin，
因此桥接必须"无 Origin 放行、有 Origin 必须精确匹配"，身份始终由 Bearer token 承担。

运行：python -m unittest tests.test_bridge_origin_auth -v
"""

import http.client
import os
import socket
import subprocess
import sys
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT_ID = "a" * 32
ALLOWED_ORIGIN = "chrome-extension://" + EXT_ID
TOKEN = "t" * 40


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class BridgeOriginAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        env = dict(os.environ)
        env.update({
            "BRIDGE_TOKEN": TOKEN,
            "BRIDGE_ALLOWED_ORIGIN": ALLOWED_ORIGIN,
            "BRIDGE_PORT": str(cls.port),
            "PYTHONIOENCODING": "utf-8",
        })
        cls.proc = subprocess.Popen(
            [sys.executable, "bridge.py"],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 15
        ready = False
        while time.time() < deadline:
            if cls.proc.poll() is not None:
                break
            try:
                s = socket.create_connection(("127.0.0.1", cls.port), timeout=0.5)
                s.close()
                ready = True
                break
            except OSError:
                time.sleep(0.15)
        if not ready:
            cls.proc.kill()
            raise RuntimeError("bridge 未能在 15 秒内启动（端口 %d）" % cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)

    def _request(self, method="GET", path="/ping", headers=None, host=None):
        conn = http.client.HTTPConnection(
            host or "127.0.0.1", self.port, timeout=10)
        hdrs = dict(headers or {})
        conn.request(method, path, headers=hdrs)
        resp = conn.getresponse()
        body = resp.read()
        status = resp.status
        conn.close()
        return status, body

    # ---- 核心：扩展真实请求（无 Origin）必须通过 ----
    def test_extension_request_without_origin_is_allowed(self):
        """Chrome 扩展 fetch 不带 Origin：有合法 token 时应 200。"""
        status, _ = self._request(headers={"Authorization": "Bearer " + TOKEN})
        self.assertEqual(status, 200,
                         "无 Origin 的扩展请求被拒绝，说明回归到 Chrome 157 的坑里了")

    def test_state_without_origin_is_allowed(self):
        status, _ = self._request(
            path="/state", headers={"Authorization": "Bearer " + TOKEN})
        self.assertEqual(status, 200)

    # ---- Origin 精确匹配 ----
    def test_matching_origin_is_allowed(self):
        status, _ = self._request(headers={
            "Authorization": "Bearer " + TOKEN, "Origin": ALLOWED_ORIGIN})
        self.assertEqual(status, 200)

    def test_mismatching_origin_is_rejected(self):
        status, _ = self._request(headers={
            "Authorization": "Bearer " + TOKEN, "Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_origin_prefix_does_not_match(self):
        """防止 chrome-extension://<id> 被 ...<id>extra 前缀匹配。"""
        status, _ = self._request(headers={
            "Authorization": "Bearer " + TOKEN, "Origin": ALLOWED_ORIGIN + "x"})
        self.assertEqual(status, 403)

    # ---- token 强制 ----
    def test_missing_token_is_unauthorized(self):
        status, _ = self._request()
        self.assertEqual(status, 401)

    def test_wrong_token_is_unauthorized(self):
        status, _ = self._request(
            headers={"Authorization": "Bearer " + "x" * 40})
        self.assertEqual(status, 401)

    def test_non_bearer_scheme_is_unauthorized(self):
        status, _ = self._request(headers={"Authorization": "Basic " + TOKEN})
        self.assertEqual(status, 401)

    # ---- Host 校验（当前实现比清单更严：只认 127.0.0.1:<port>）----
    def test_non_loopback_host_header_is_rejected(self):
        status, _ = self._request(headers={
            "Authorization": "Bearer " + TOKEN, "Host": "192.168.1.9:%d" % self.port})
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
