import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.archive_store import ArchiveStore, make_archive_id, default_db_path


class ArchiveStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = os.path.join(self.tmp.name, "ledger.sqlite3")
        self.store = ArchiveStore(self.db)

    def tearDown(self):
        # release references; Windows SQLite may keep wal/shm briefly
        self.store = None
        import gc
        gc.collect()
        self.tmp.cleanup()

    def test_make_archive_id_is_stable_for_same_meeting_and_page(self):
        a = make_archive_id("m-1", "11111111-1111-1111-1111-111111111111", mode="append")
        b = make_archive_id("m-1", "11111111-1111-1111-1111-111111111111", mode="append")
        c = make_archive_id("m-1", "22222222-2222-2222-2222-222222222222", mode="append")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertIn("m-1", a)

    def test_claim_is_atomic_and_prevents_concurrent_duplicate_run(self):
        aid = make_archive_id("meeting-A", "page-A")
        first = self.store.claim(aid, "meeting-A", target_page_id="page-A", worker_id="w1")
        second = self.store.claim(aid, "meeting-A", target_page_id="page-A", worker_id="w2")
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertIn(second.get("reason"), ("already_running", "already_done", "needs_review"))

    def test_done_archive_is_not_reclaimed(self):
        aid = make_archive_id("meeting-B", "page-B")
        self.assertTrue(self.store.claim(aid, "meeting-B", "page-B", worker_id="w1")["ok"])
        self.store.mark_done(aid, {"ok": True})
        again = self.store.claim(aid, "meeting-B", "page-B", worker_id="w2")
        self.assertFalse(again["ok"])
        self.assertEqual(again.get("reason"), "already_done")

    def test_needs_review_blocks_blind_retry(self):
        aid = make_archive_id("meeting-C", "page-C")
        self.assertTrue(self.store.claim(aid, "meeting-C", "page-C", worker_id="w1")["ok"])
        self.store.mark_needs_review(aid, "append uncertain", {"step": "append"})
        again = self.store.claim(aid, "meeting-C", "page-C", worker_id="w2")
        self.assertFalse(again["ok"])
        self.assertEqual(again.get("reason"), "needs_review")

    def test_error_can_be_reclaimed_and_steps_persist(self):
        aid = make_archive_id("meeting-D", "page-D")
        self.assertTrue(self.store.claim(aid, "meeting-D", "page-D", worker_id="w1")["ok"])
        self.store.update_steps(aid, {"prepare": {"ok": True}})
        self.store.mark_error(aid, "upload failed")
        rec = self.store.get(aid)
        self.assertEqual(rec["status"], "error")
        self.assertEqual(self.store.get_steps(aid).get("prepare", {}).get("ok"), True)
        again = self.store.claim(aid, "meeting-D", "page-D", worker_id="w2")
        self.assertTrue(again["ok"])


class SyncIdempotencyHelpersTests(unittest.TestCase):
    def test_marker_text_and_unique_temp_dirs(self):
        import scripts.sync_to_flowus as sync

        m = sync.marker_text("vnarc::append::m1::p1")
        self.assertEqual(m, "ARCHIVE_ID:vnarc::append::m1::p1")
        d1 = sync.make_tmp_dir("vn_test")
        d2 = sync.make_tmp_dir("vn_test")
        self.assertNotEqual(d1, d2)
        self.assertTrue(os.path.isdir(d1))
        self.assertTrue(os.path.isdir(d2))

    def test_build_beautified_blocks_embeds_archive_marker(self):
        import scripts.sync_to_flowus as sync

        meeting = {
            "id": "m-xyz",
            "manifest": {"duration_ms": 1000, "created_at_ms": 1763187800000, "title": "Meeting"},
        }
        blocks = sync.build_beautified_blocks(
            meeting,
            transcript="hello world",
            summary_text="## 语音笔记整理\n### 摘要\nhello world\n### 要点\n- hello world\n### 可行动项\n- （未从转写中识别到明确待办，可根据内容自行补充）",
            oss_name="oss/x.wav",
            size=123,
            mindmap_url=None,
            archive_id="vnarc::append::m-xyz::p-1",
        )
        blob = json.dumps(blocks, ensure_ascii=False)
        self.assertIn("ARCHIVE_ID:vnarc::append::m-xyz::p-1", blob)
        self.assertIn("归档标识", blob)

    def test_page_contains_marker_via_markdown(self):
        import scripts.sync_to_flowus as sync

        archive_id = "vnarc::append::m1::p1"
        with mock.patch("scripts.sync_to_flowus.run_flowus_ok") as rf:
            rf.return_value = ({"ok": True, "data": {"markdown": f"... ARCHIVE_ID:{archive_id} ..."}}, True)
            found, where = sync.page_contains_marker("page-1", archive_id)
        self.assertTrue(found)
        self.assertEqual(where, "markdown")

    def test_main_skips_when_marker_already_present(self):
        import scripts.sync_to_flowus as sync

        meeting = {
            "id": "m-1",
            "dir": "d",
            "manifest": {"duration_ms": 1000, "created_at_ms": 1763187800000, "title": "T"},
            "wav": "C:/tmp/fake.wav",
            "transcript_path": "C:/tmp/t.md",
            "transcript": "hello",
        }
        argv = [
            "sync",
            "--meeting-id", "m-1",
            "--page-id", "11111111-1111-1111-1111-111111111111",
            "--archive-id", "vnarc::append::m-1::11111111-1111-1111-1111-111111111111",
            "--skip-if-present",
        ]
        with mock.patch.object(sync, "load_meeting", return_value=meeting), \
             mock.patch.object(sync, "page_contains_marker", return_value=(True, "markdown")), \
             mock.patch.object(sync, "run_flowus_ok") as rf, \
             mock.patch("sys.argv", argv), \
             mock.patch("sys.stdout"):
            # should return without doing upload/append
            sync.main()
        # run_flowus_ok must not be called for upload/mindmap/append when skipped
        self.assertEqual(rf.call_count, 0)

    def test_main_reports_uncertain_on_append_failure(self):
        import scripts.sync_to_flowus as sync

        meeting = {
            "id": "m-2",
            "dir": "d",
            "manifest": {"duration_ms": 1000, "created_at_ms": 1763187800000, "title": "T"},
            "wav": "C:/tmp/fake.wav",
            "transcript_path": "C:/tmp/t.md",
            "transcript": "hello",
        }
        # sequence: upload ok, (no mindmap), append fails
        def side_effect(args, body_path=None):
            if args[:4] == ["--json", "file", "upload", "--parent-page"]:
                return ({"ok": True, "data": {"oss_name": "oss/x", "size": 1}}, True)
            if args[:4] == ["--json", "block", "append", "11111111-1111-1111-1111-111111111111"]:
                return ({"ok": False, "error": "boom"}, False)
            return ({"ok": True, "data": {}}, True)

        argv = [
            "sync",
            "--meeting-id", "m-2",
            "--page-id", "11111111-1111-1111-1111-111111111111",
            "--no-mindmap",
            "--plain",
            "--archive-id", "vnarc::append::m-2::11111111-1111-1111-1111-111111111111",
        ]
        buf = []
        with mock.patch.object(sync, "load_meeting", return_value=meeting), \
             mock.patch.object(sync, "page_contains_marker", return_value=(False, None)), \
             mock.patch.object(sync, "run_flowus_ok", side_effect=side_effect), \
             mock.patch("sys.argv", argv), \
             mock.patch("sys.stdout.write", side_effect=lambda s: buf.append(s) or len(s)):
            with self.assertRaises(SystemExit) as cm:
                sync.main()
        self.assertEqual(cm.exception.code, 2)


class DefaultPathTests(unittest.TestCase):
    def test_default_db_path_is_outside_repo_by_default(self):
        # when APPDATA is set, default path should be under APPDATA/hex-flowus-voice-note
        with mock.patch.dict(os.environ, {"APPDATA": r"C:\Users\Test\AppData\Roaming"}):
            p = default_db_path(ensure_dir=False)
        self.assertTrue(p.replace("\\", "/").endswith("hex-flowus-voice-note/archive_ledger.sqlite3"))


if __name__ == "__main__":
    unittest.main()