"""归档任务目录（_job_dir）文件系统安全回归测试。

背景：真实归档曾在 Windows 上 100% 失败于
    [WinError 123] 文件名、目录名或卷标语法不正确
因为 archive_id 形如 ``vnarc::append::<meeting>::<page>``，而旧白名单
``[^A-Za-z0-9._:-]`` 把 ``:`` 放行了 —— Windows 目录名不允许冒号。

本套用例锁定：目录名跨平台合法、不同 archive_id 不复用同一目录。

运行：python -m unittest tests.test_job_dir -v
"""

import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import bridge


REAL_ARCHIVE_ID = (
    "vnarc::append::1794797556810-1424::e2f5c558-f8ef-42c8-ae93-08c7df701742"
)


class JobDirTests(unittest.TestCase):
    def setUp(self):
        self._orig = bridge.ARCHIVE_ARTIFACTS_DIR
        self.tmp = tempfile.mkdtemp(prefix="jobdir-")
        bridge.ARCHIVE_ARTIFACTS_DIR = self.tmp

    def tearDown(self):
        bridge.ARCHIVE_ARTIFACTS_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _name(self, archive_id):
        d = bridge._job_dir(archive_id)
        self.assertTrue(os.path.isdir(d), "_job_dir 未创建目录: %r" % d)
        self.assertEqual(os.path.dirname(os.path.abspath(d)),
                         os.path.abspath(self.tmp),
                         "任务目录必须直接位于 artifacts 根目录下")
        return os.path.basename(d)

    def test_real_archive_id_creates_directory(self):
        """真实 archive_id（含 '::'）必须能建成目录 —— 防止 WinError 123 回归。"""
        name = self._name(REAL_ARCHIVE_ID)
        self.assertNotIn(":", name)
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)

    def test_name_has_no_windows_illegal_chars(self):
        illegal = set('<>:"/\\|?*')
        name = self._name(REAL_ARCHIVE_ID)
        self.assertFalse(illegal & set(name),
                         "目录名含 Windows 非法字符: %r" % name)

    def test_distinct_archive_ids_do_not_share_directory(self):
        """清洗后可能同名（'a::b' 与 'a__b'），必须靠哈希后缀区分。"""
        d1 = bridge._job_dir("vnarc::append::m1::p1")
        d2 = bridge._job_dir("vnarc__append__m1__p1")
        self.assertNotEqual(d1, d2)

    def test_same_archive_id_is_stable(self):
        self.assertEqual(bridge._job_dir(REAL_ARCHIVE_ID),
                         bridge._job_dir(REAL_ARCHIVE_ID))

    def test_empty_archive_id_still_works(self):
        self._name("")
        self._name(None)

    def test_name_length_is_bounded(self):
        name = self._name("vnarc::append::" + "x" * 500)
        self.assertLessEqual(len(name), 140, "目录名过长，可能超出路径限制")

    def test_nested_traversal_is_neutralised(self):
        name = self._name("vnarc::append::../../etc::passwd")
        self.assertNotIn("..", name)
        self.assertNotIn("/", name)


if __name__ == "__main__":
    unittest.main()
