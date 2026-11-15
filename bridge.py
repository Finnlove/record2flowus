import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config():
    cfg = {}
    for name in ("config.json", "config.local.json"):
        p = os.path.join(BASE_DIR, name)
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    cfg.update(json.load(f) or {})
            except Exception:
                pass
    return cfg


def detect_hex_exe():
    appdata = os.environ.get("APPDATA", "")
    versions_dir = os.path.join(appdata, "hex", "versions")
    if os.path.isdir(versions_dir):
        try:
            vers = sorted([d for d in os.listdir(versions_dir) if os.path.isdir(os.path.join(versions_dir, d))], reverse=True)
            for v in vers:
                exe = os.path.join(versions_dir, v, "hex.exe")
                if os.path.isfile(exe):
                    return exe
        except Exception:
            pass
    return "hex"


CFG = load_config()
HEX_EXE = os.environ.get("HEX_EXE") or CFG.get("hex_exe") or detect_hex_exe()
HEX_MEETINGS = os.environ.get("HEX_MEETINGS_DIR") or CFG.get("hex_meetings_dir") or os.path.join(os.environ.get("APPDATA", ""), "hex", "meetings")
SYNC_SCRIPT = os.environ.get("SYNC_SCRIPT") or CFG.get("sync_script") or os.path.join(BASE_DIR, "scripts", "sync_to_flowus.py")
if not os.path.isabs(SYNC_SCRIPT):
    SYNC_SCRIPT = os.path.join(BASE_DIR, SYNC_SCRIPT)
HOST = os.environ.get("BRIDGE_HOST") or CFG.get("bridge_host") or "127.0.0.1"
PORT = int(os.environ.get("BRIDGE_PORT") or CFG.get("bridge_port") or 27123)

state = {
    "status": "idle",  # idle|starting|recording|stopping|processing|done|error
    "message": "",
    "page_id": None,
    "page_url": None,
    "page_title": None,
    "meeting_id": None,
    "result": None,
    "error": None,
    "started_at": None,
}
lock = threading.Lock()


def run(cmd, timeout=120):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def hex_cmd(args, timeout=60):
    return run([HEX_EXE] + args, timeout=timeout)


def list_meetings():
    try:
        r = hex_cmd(["meetings", "list", "--json"], timeout=30)
        j = json.loads(r.stdout or r.stderr or "{}")
        return j.get("meetings", [])
    except Exception:
        return []


def latest_meeting_id():
    ms = list_meetings()
    return ms[0].get("id") if ms else None


def find_meeting_by_id(mid):
    for m in list_meetings():
        if m.get("id") == mid:
            return m
    return None


def load_meeting_artifacts(mid):
    d = os.path.join(HEX_MEETINGS, mid)
    mf = os.path.join(d, "manifest.json")
    if not os.path.isfile(mf):
        raise RuntimeError("manifest not found")
    with open(mf, "r", encoding="utf-8") as f:
        m = json.load(f)
    export_path = m.get("export_path")
    transcript_md = export_path if (export_path and os.path.isfile(export_path)) else os.path.join(d, "transcript.md")
    if not os.path.isfile(transcript_md):
        raise RuntimeError("transcript not found")
    with open(transcript_md, "r", encoding="utf-8") as f:
        transcript = f.read()
    wav = os.path.join(d, "microphone.wav")
    if not os.path.isfile(wav):
        wav = os.path.join(d, "system.wav")
    return {"id": mid, "dir": d, "manifest": m, "transcript": transcript, "wav": wav, "transcript_path": transcript_md}


def extract_transcript_text(transcript_md):
    body = transcript_md
    m = re.search(r"^## Transcript\s*$", transcript_md, re.M)
    if m:
        body = transcript_md[m.end():].strip()
    lines = []
    for line in body.splitlines():
        s = line.strip()
        if not s or re.match(r"^\*\*.+\*\*\s*·\s*\d", s):
            continue
        lines.append(s)
    return "\n".join(lines).strip()


def make_auto_summary(meeting):
    transcript = meeting["transcript"]
    text = extract_transcript_text(transcript)
    m = meeting["manifest"]
    dur_s = int((m.get("duration_ms") or 0) / 1000)
    mm, ss = divmod(dur_s, 60)
    created_ms = m.get("created_at_ms") or 0
    dt = datetime.fromtimestamp(created_ms / 1000, tz=timezone(timedelta(hours=8))) if created_ms else None
    date_str = dt.strftime("%Y-%m-%d %H:%M（+08:00）") if dt else ""
    raw = re.split(r"[。！？!?]\s*", text)
    raw = [x.strip() for x in raw if len(x.strip()) >= 6]
    points, seen = [], set()
    for s in raw:
        k = s[:20]
        if k in seen:
            continue
        seen.add(k)
        points.append(s[:80])
        if len(points) >= 5:
            break
    summary_para = (text[:320] + ("…" if len(text) > 320 else "")) if text else "（无有效转写内容）"
    todos = ["复核转写要点与原始录音，补充关键信息", "根据本次内容提炼可执行行动项", "按 meeting id 做幂等归档"]
    md = ["## 语音笔记整理（自动生成）", "### 基本信息", f"- 会议ID：{meeting['id']}", f"- 时间：{date_str}", f"- 时长：{mm:02d}:{ss:02d}", "- 转写：Hex 本地 ASR", "### 摘要", summary_para, "### 要点"]
    for p in (points or ["（待补充要点）"]):
        md.append(f"- {p}")
    md.append("### 可行动项")
    for t in todos:
        md.append(f"- {t}")
    return "\n".join(md), points


def rt_node(text):
    return [{"type": "text", "text": {"content": text, "link": None},
             "annotations": {"bold": False, "italic": False, "strikethrough": False, "underline": False,
                             "code": False, "color": "default", "background_color": "default", "text_color": "default"},
             "plain_text": text, "href": None}]


def make_auto_mindmap(meeting, points):
    m = meeting["manifest"]
    dur_s = int((m.get("duration_ms") or 0) / 1000)
    mm, ss = divmod(dur_s, 60)
    title_text = f"语音笔记导图 {meeting['id']}（{mm:02d}:{ss:02d}）"
    point_children = [{"rich_text": rt_node(p[:40])} for p in (points[:6] or ["待补充要点"])]
    return {
        "title": rt_node(title_text),
        "settings": {"layout": "both", "line_style": "curved", "branch_color": "gray", "sibling_node_alignment": True},
        "nodes": [
            {"rich_text": rt_node("基本信息"), "children": [
                {"rich_text": rt_node(f"会议ID: {meeting['id']}")},
                {"rich_text": rt_node(f"时长: {mm:02d}:{ss:02d}")},
                {"rich_text": rt_node("来源: Hex 本地录音+ASR")},
            ]},
            {"rich_text": rt_node("核心要点"), "children": point_children},
            {"rich_text": rt_node("行动与归档"), "children": [
                {"rich_text": rt_node("复核转写并补充关键信息")},
                {"rich_text": rt_node("提炼待办与结论")},
                {"rich_text": rt_node("归档录音/转写/摘要")},
            ]},
        ]
    }


def wait_for_meeting_complete(mid, timeout_s=360):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        info = find_meeting_by_id(mid)
        if info and info.get("status") == "complete" and not info.get("in_progress"):
            mf = os.path.join(HEX_MEETINGS, mid, "manifest.json")
            if os.path.isfile(mf):
                try:
                    with open(mf, "r", encoding="utf-8") as f:
                        m = json.load(f)
                    if m.get("status") == "complete":
                        return m
                except Exception:
                    pass
        time.sleep(2)
    raise RuntimeError("timeout waiting for meeting completion/transcript")


def detect_new_meeting(before_ids, timeout_s=20):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        for m in list_meetings():
            if m.get("id") not in before_ids:
                return m.get("id")
        time.sleep(1)
    return None


def process_and_upload(meeting_id, page_id):
    meeting = load_meeting_artifacts(meeting_id)
    summary_md, points = make_auto_summary(meeting)
    mm_body = make_auto_mindmap(meeting, points)
    tmp = os.path.join(tempfile.gettempdir(), "hex-flowus-voice-note")
    os.makedirs(tmp, exist_ok=True)
    sum_path = os.path.join(tmp, f"auto_summary_{meeting_id}.md")
    mm_path = os.path.join(tmp, f"auto_mindmap_{meeting_id}.json")
    with open(sum_path, "w", encoding="utf-8") as f:
        f.write(summary_md)
    with open(mm_path, "w", encoding="utf-8") as f:
        json.dump(mm_body, f, ensure_ascii=False)
    if not os.path.isfile(SYNC_SCRIPT):
        raise RuntimeError(f"sync script not found: {SYNC_SCRIPT}")
    cmd = [sys.executable, SYNC_SCRIPT, "--meeting-id", meeting_id, "--page-id", page_id,
           "--summary-file", sum_path, "--mindmap-json", mm_path, "--meetings-dir", HEX_MEETINGS]
    r = run(cmd, timeout=600)
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    result = None
    for line in reversed(out.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                result = json.loads(line)
                break
            except Exception:
                pass
    if r.returncode != 0 and not (result and result.get("ok")):
        raise RuntimeError(out[-2000:])
    return result or {"ok": True, "raw": out[-1000:]}


def worker_start(page_id, page_title, page_url):
    try:
        with lock:
            state.update({"status": "recording", "message": "recording started", "page_id": page_id,
                          "page_title": page_title, "page_url": page_url, "error": None, "result": None})
        before = {m.get("id") for m in list_meetings()}
        r = hex_cmd(["meetings", "record", "--json"], timeout=30)
        mid = None
        try:
            j = json.loads(r.stdout or "{}")
            mid = (j.get("meeting") or {}).get("id") or j.get("id") or j.get("meeting_id")
        except Exception:
            pass
        if not mid:
            mid = detect_new_meeting(before, timeout_s=20)
        if not mid:
            for m in list_meetings():
                if m.get("in_progress"):
                    mid = m.get("id"); break
        if not mid:
            raise RuntimeError(f"cannot determine meeting id. output: {(r.stdout or r.stderr)[:500]}")
        with lock:
            state["meeting_id"] = mid
            state["message"] = f"recording meeting {mid}"
    except Exception as e:
        with lock:
            state.update({"status": "error", "error": str(e), "message": "start failed"})


def worker_stop_and_process():
    try:
        with lock:
            mid = state.get("meeting_id")
            page_id = state.get("page_id")
            state.update({"status": "stopping", "message": "stopping recording"})
        hex_cmd(["meetings", "stop", "--json"], timeout=60)
        if not mid:
            for m in list_meetings():
                if m.get("in_progress"):
                    mid = m.get("id"); break
            if not mid:
                mid = latest_meeting_id()
        if not mid:
            raise RuntimeError("no meeting to stop/process")
        with lock:
            state.update({"meeting_id": mid, "status": "processing", "message": "waiting for transcript"})
        wait_for_meeting_complete(mid, timeout_s=360)
        with lock:
            state["message"] = "uploading to FlowUs"
        if not page_id:
            raise RuntimeError("missing target page_id")
        result = process_and_upload(mid, page_id)
        with lock:
            state.update({"status": "done", "message": "uploaded", "result": result})
    except Exception as e:
        with lock:
            state.update({"status": "error", "error": str(e), "message": "processing failed"})


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/state":
            with lock:
                self._send(200, {"ok": True, "state": dict(state)})
        elif path == "/ping":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        if path == "/start":
            with lock:
                if state.get("status") in ("recording", "stopping", "processing", "starting"):
                    self._send(409, {"ok": False, "error": "busy", "state": dict(state)})
                    return
                state.update({"started_at": time.time(), "status": "starting", "message": "starting", "result": None, "error": None})
            page_id = payload.get("pageId") or payload.get("page_id")
            page_title = payload.get("title")
            page_url = payload.get("url")
            if not page_id:
                with lock:
                    state.update({"status": "error", "error": "missing pageId"})
                self._send(400, {"ok": False, "error": "missing pageId"})
                return
            threading.Thread(target=worker_start, args=(page_id, page_title, page_url), daemon=True).start()
            self._send(200, {"ok": True})
        elif path == "/stop":
            with lock:
                if state.get("status") not in ("recording", "starting"):
                    self._send(409, {"ok": False, "error": "not recording", "state": dict(state)})
                    return
            threading.Thread(target=worker_stop_and_process, daemon=True).start()
            self._send(200, {"ok": True})
        elif path == "/reset":
            with lock:
                if state.get("status") in ("recording", "stopping", "processing", "starting"):
                    self._send(409, {"ok": False, "error": "busy"})
                    return
                state.update({"status": "idle", "message": "", "error": None, "result": None, "meeting_id": None})
            self._send(200, {"ok": True})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def log_message(self, fmt, *args):
        sys.stderr.write("[bridge] " + (fmt % args) + "\n")


if __name__ == "__main__":
    print(f"Bridge listening on http://{HOST}:{PORT}")
    print(f"HEX_EXE={HEX_EXE}")
    print(f"SYNC_SCRIPT={SYNC_SCRIPT}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
