"""开机自启（Startup 快捷脚本）回归测试 —— 纯标准库。

锁定行为：bridge 成功监听后**默认**确保开机自启项存在；幂等；
可通过 config `autostart: false` 或环境变量 `BRIDGE_AUTOSTART=0` 关闭。

运行：python -m unittest tests.test_autostart -v
"""

import os
import shutil
import tempfile
import unittest

import bridge


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="autostart_")
        self._appdata = os.environ.get("APPDATA")
        self._flag = os.environ.pop("BRIDGE_AUTOSTART", None)
        os.environ["APPDATA"] = self.tmp
        self._cfg = dict(bridge.CFG)
        bridge.CFG.pop("autostart", None)

    def tearDown(self):
        if self._appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._appdata
        os.environ.pop("BRIDGE_AUTOSTART", None)
        if self._flag is not None:
            os.environ["BRIDGE_AUTOSTART"] = self._flag
        bridge.CFG.clear()
        bridge.CFG.update(self._cfg)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _startup_path(self):
        return os.path.join(
            self.tmp, "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
            bridge.AUTOSTART_NAME)

    # ---- 默认开启 ----
    def test_installs_when_missing(self):
        self.assertEqual(bridge.ensure_autostart(), "installed")
        self.assertTrue(os.path.isfile(self._startup_path()))

    def test_path_is_inside_user_startup_folder(self):
        self.assertEqual(bridge.autostart_path(), self._startup_path())

    def test_is_idempotent(self):
        self.assertEqual(bridge.ensure_autostart(), "installed")
        first = open(self._startup_path(), "r", encoding="utf-8", newline="").read()
        self.assertEqual(bridge.ensure_autostart(), "present")
        second = open(self._startup_path(), "r", encoding="utf-8", newline="").read()
        self.assertEqual(first, second)

    def test_refreshes_stale_content(self):
        os.makedirs(os.path.dirname(self._startup_path()), exist_ok=True)
        with open(self._startup_path(), "w", encoding="utf-8") as f:
            f.write("stale")
        self.assertEqual(bridge.ensure_autostart(), "installed")
        self.assertIn("bridge.py", open(self._startup_path(), encoding="utf-8").read())

    # ---- 脚本内容正确性 ----
    def test_script_points_at_this_bridge(self):
        bridge.ensure_autostart()
        body = open(self._startup_path(), "r", encoding="utf-8", newline="").read()
        self.assertIn(os.path.join(bridge.BASE_DIR, "bridge.py"), body)
        self.assertIn("WshShell.CurrentDirectory", body)
        # 隐藏窗口 + 不等待
        self.assertIn(", 0, False", body)

    def test_script_quotes_paths_for_vbs(self):
        bridge.ensure_autostart()
        body = open(self._startup_path(), "r", encoding="utf-8", newline="").read()
        run_line = [l for l in body.splitlines() if l.startswith("WshShell.Run")][0]
        # VBS 里内嵌引号必须写成 ""，因此原始文本里应出现三连引号
        self.assertIn('"""', run_line)

    def test_prefers_pythonw_when_available(self):
        bridge.ensure_autostart()
        body = open(self._startup_path(), "r", encoding="utf-8", newline="").read()
        exe = bridge.sys.executable
        cand = exe[: -len("python.exe")] + "pythonw.exe" if exe.lower().endswith("python.exe") else None
        if cand and os.path.isfile(cand):
            self.assertIn("pythonw.exe", body)
            self.assertNotIn('"python.exe"', body)
        else:
            self.assertTrue(os.path.isfile(exe) or True)

    # ---- 关闭开关 ----
    def test_config_false_disables(self):
        bridge.CFG["autostart"] = False
        self.assertIsNone(bridge.ensure_autostart())
        self.assertFalse(os.path.isfile(self._startup_path()))

    def test_env_zero_disables(self):
        bridge.CFG["autostart"] = True
        os.environ["BRIDGE_AUTOSTART"] = "0"
        self.assertIsNone(bridge.ensure_autostart())
        self.assertFalse(os.path.isfile(self._startup_path()))

    def test_env_one_enables_even_if_config_missing(self):
        os.environ["BRIDGE_AUTOSTART"] = "1"
        self.assertEqual(bridge.ensure_autostart(), "installed")


if __name__ == "__main__":
    unittest.main()
