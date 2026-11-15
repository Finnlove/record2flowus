import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS archives (
  archive_id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL,
  target_page_id TEXT,
  status TEXT NOT NULL,
  claimed_by TEXT,
  claim_at REAL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  error TEXT,
  result_json TEXT,
  steps_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_archives_meeting ON archives(meeting_id);
CREATE INDEX IF NOT EXISTS idx_archives_status ON archives(status);
"""


def default_db_path(base_dir=None, ensure_dir=False):
    # keep local, outside git-tracked repo by default
    if base_dir:
        root = Path(base_dir)
        if ensure_dir:
            root.mkdir(parents=True, exist_ok=True)
        return str(root / "archive_ledger.sqlite3")
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    root = Path(appdata) / "hex-flowus-voice-note"
    if ensure_dir:
        root.mkdir(parents=True, exist_ok=True)
    return str(root / "archive_ledger.sqlite3")


def make_archive_id(meeting_id, target_page_id=None, mode="append"):
    # stable deterministic id per meeting+target intent
    mid = str(meeting_id or "").strip()
    tid = str(target_page_id or "").strip()
    m = str(mode or "append").strip() or "append"
    if tid:
        return f"vnarc::{m}::{mid}::{tid}"
    return f"vnarc::{m}::{mid}"


class ArchiveStore:
    def __init__(self, db_path=None):
        self.db_path = db_path or default_db_path(ensure_dir=True)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock:
            with self._connect() as conn:
                conn.executescript(SCHEMA)

    def _now(self):
        return time.time()

    def get(self, archive_id):
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM archives WHERE archive_id=?", (archive_id,)).fetchone()
            return dict(row) if row else None

    def get_steps(self, archive_id):
        rec = self.get(archive_id)
        if not rec:
            return {}
        try:
            return json.loads(rec.get("steps_json") or "{}") or {}
        except Exception:
            return {}

    def upsert_pending(self, archive_id, meeting_id, target_page_id=None):
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM archives WHERE archive_id=?", (archive_id,)).fetchone()
            if not row:
                conn.execute(
                    """INSERT INTO archives(archive_id,meeting_id,target_page_id,status,created_at,updated_at,steps_json)
                       VALUES(?,?,?,?,?,?,?)""",
                    (archive_id, meeting_id, target_page_id, "pending", now, now, json.dumps({})),
                )
            else:
                # keep existing terminal/claimed states; refresh target if missing
                conn.execute(
                    "UPDATE archives SET target_page_id=COALESCE(target_page_id, ?), updated_at=? WHERE archive_id=?",
                    (target_page_id, now, archive_id),
                )
            conn.execute("COMMIT")

    def claim(self, archive_id, meeting_id, target_page_id=None, worker_id=None, stale_after_sec=7200):
        now = self._now()
        worker_id = worker_id or str(uuid.uuid4())
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM archives WHERE archive_id=?", (archive_id,)).fetchone()
            if not row:
                conn.execute(
                    """INSERT INTO archives(archive_id,meeting_id,target_page_id,status,claimed_by,claim_at,created_at,updated_at,steps_json)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (archive_id, meeting_id, target_page_id, "running", worker_id, now, now, now, json.dumps({})),
                )
                conn.execute("COMMIT")
                return {"ok": True, "worker_id": worker_id, "status": "running", "created": True}

            status = row["status"]
            # terminal done: do not re-run
            if status == "done":
                conn.execute("COMMIT")
                return {"ok": False, "reason": "already_done", "status": status, "record": dict(row)}

            # if already running by someone else and claim is fresh, reject
            if status == "running" and row["claimed_by"] and row["claimed_by"] != worker_id:
                claim_at = float(row["claim_at"] or 0)
                if claim_at and (now - claim_at) < stale_after_sec:
                    conn.execute("COMMIT")
                    return {"ok": False, "reason": "already_running", "status": status, "claimed_by": row["claimed_by"]}
                # stale claim: allow takeover

            if status == "needs_review":
                conn.execute("COMMIT")
                return {"ok": False, "reason": "needs_review", "status": status, "record": dict(row)}

            conn.execute(
                """UPDATE archives SET status='running', claimed_by=?, claim_at=?, updated_at=?,
                                      target_page_id=COALESCE(?, target_page_id), error=NULL
                   WHERE archive_id=?""",
                (worker_id, now, now, target_page_id, archive_id),
            )
            conn.execute("COMMIT")
            return {"ok": True, "worker_id": worker_id, "status": "running", "created": False, "previous_status": status}

    def update_steps(self, archive_id, patch_steps):
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT steps_json FROM archives WHERE archive_id=?", (archive_id,)).fetchone()
            if not row:
                conn.execute("COMMIT")
                return False
            try:
                steps = json.loads(row["steps_json"] or "{}") or {}
            except Exception:
                steps = {}
            if isinstance(patch_steps, dict):
                steps.update(patch_steps)
            conn.execute("UPDATE archives SET steps_json=?, updated_at=? WHERE archive_id=?", (json.dumps(steps, ensure_ascii=False), now, archive_id))
            conn.execute("COMMIT")
            return True

    def mark_done(self, archive_id, result=None):
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE archives SET status='done', error=NULL, result_json=?, updated_at=?, claimed_by=NULL WHERE archive_id=?",
                (json.dumps(result or {}, ensure_ascii=False), now, archive_id),
            )

    def mark_error(self, archive_id, error, keep_claim=False):
        now = self._now()
        with self._lock, self._connect() as conn:
            if keep_claim:
                conn.execute("UPDATE archives SET status='error', error=?, updated_at=? WHERE archive_id=?",
                             (str(error)[:4000], now, archive_id))
            else:
                conn.execute("UPDATE archives SET status='error', error=?, updated_at=?, claimed_by=NULL WHERE archive_id=?",
                             (str(error)[:4000], now, archive_id))

    def mark_needs_review(self, archive_id, error, result=None):
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE archives SET status='needs_review', error=?, result_json=?, updated_at=?, claimed_by=NULL WHERE archive_id=?",
                (str(error)[:4000], json.dumps(result or {}, ensure_ascii=False), now, archive_id),
            )

    def release(self, archive_id, status="pending"):
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE archives SET status=?, claimed_by=NULL, updated_at=? WHERE archive_id=?",
                         (status, now, archive_id))