import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_BODY_SIZE = 8 * 1024
READ_TIMEOUT = 10
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def load_config():
    cfg = {}
    for name in ("config.json", "config.local.json"):
        p = os.path.join(BASE_DIR, name)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data is None:
                data = {}
            if not isinstance(data, dict):
                raise ValueError("top-level JSON value must be an object")
            cfg.update(data)
        except Exception as e:
            print(f"ERROR: failed to load config file '{p}': {e}", file=sys.stderr)
            raise SystemExit(2)
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
HEX_EXPORT_DIR = os.environ.get("HEX_EXPORT_DIR") or CFG.get("hex_export_dir") or ""
SYNC_SCRIPT = os.environ.get("SYNC_SCRIPT") or CFG.get("sync_script") or os.path.join(BASE_DIR, "scripts", "sync_to_flowus.py")
if not os.path.isabs(SYNC_SCRIPT):
    SYNC_SCRIPT = os.path.join(BASE_DIR, SYNC_SCRIPT)
HOST = "127.0.0.1"
PORT = int(os.environ.get("BRIDGE_PORT") or CFG.get("bridge_port") or 27123)
BRIDGE_TOKEN = str(os.environ.get("BRIDGE_TOKEN") or CFG.get("bridge_token") or "").strip()
ALLOWED_ORIGIN = str(os.environ.get("BRIDGE_ALLOWED_ORIGIN") or CFG.get("bridge_allowed_origin") or "").strip()
EXPECTED_HOST = f"127.0.0.1:{PORT}"

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


def _is_inside(path, base):
    try:
        real_path = os.path.realpath(path)
        real_base = os.path.realpath(base)
        return os.path.commonpath([real_path, real_base]) == real_base
    except Exception:
        return False


def _validate_meeting_id(mid):
    return isinstance(mid, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", mid) is not None


def _resolve_transcript_path(export_path, meeting_dir):
    candidates = []
    if isinstance(export_path, str) and export_path.strip():
        ep = os.path.normpath(export_path.strip())
        allowed = [meeting_dir, HEX_MEETINGS]
        if HEX_EXPORT_DIR:
            allowed.append(HEX_EXPORT_DIR)
        for base in allowed:
            if base and _is_inside(ep, base) and os.path.isfile(ep):
                candidates.append(ep)
    fallback = os.path.join(meeting_dir, "transcript.md")
    if os.path.isfile(fallback):
        candidates.append(fallback)
    return candidates[0] if candidates else None


def load_meeting_artifacts(mid):
    if not _validate_meeting_id(mid):
        raise RuntimeError("invalid meeting id")
    d = os.path.normpath(os.path.join(HEX_MEETINGS, mid))
    if not _is_inside(d, HEX_MEETINGS):
        raise RuntimeError("invalid meeting path")
    mf = os.path.join(d, "manifest.json")
    if not os.path.isfile(mf):
        raise RuntimeError("manifest not found")
    with open(mf, "r", encoding="utf-8") as f:
        m = json.load(f)
    transcript_md = _resolve_transcript_path(m.get("export_path"), d)
    if not transcript_md:
        raise RuntimeError("transcript not found")
    with open(transcript_md, "r", encoding="utf-8") as f:
        transcript = f.read()
    wav = os.path.join(d, "microphone.wav")
    if not os.path.isfile(wav):
        wav = os.path.join(d, "system.wav")
    if not os.path.isfile(wav) or not _is_inside(wav, HEX_MEETINGS):
        raise RuntimeError("audio file not found")
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
        if not mid or not _validate_meeting_id(mid):
            raise RuntimeError("cannot determine meeting id")
        with lock:
            state["meeting_id"] = mid
            state["message"] = "recording in progress"
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
        if not _validate_meeting_id(mid or ""):
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


def _is_flowus_url(url):
    if not isinstance(url, str) or not url:
        return False
    try:
        u = urlparse(url)
    except Exception:
        return False
    if u.scheme != "https":
        return False
    host = (u.hostname or "").lower()
    return host == "flowus.cn" or host.endswith(".flowus.cn")


def _valid_page_id(pid):
    return isinstance(pid, str) and UUID_RE.fullmatch(pid) is not None


def _public_state():
    with lock:
        return {
            "status": state.get("status"),
            "message": state.get("message") or "",
            "has_error": bool(state.get("error")),
            "page_bound": bool(state.get("page_id")),
        }


def _authorized(handler):
    auth = handler.headers.get("Authorization", "")
    expected = "Bearer " + BRIDGE_TOKEN if BRIDGE_TOKEN else ""
    if not BRIDGE_TOKEN or not auth.startswith("Bearer "):
        return False
    return hmac.compare_digest(auth, expected)


class Handler(BaseHTTPRequestHandler):
    server_version = "FlowUsVoiceBridge/1.0"

    def _cors_origin(self):
        origin = self.headers.get("Origin", "")
        if ALLOWED_ORIGIN and origin == ALLOWED_ORIGIN:
            return origin
        return None

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            vary = "Origin"
            if self.headers.get("Access-Control-Request-Private-Network"):
                vary = "Origin, Access-Control-Request-Private-Network"
            self.send_header("Vary", vary)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _reject(self, code, msg):
        self._send(code, {"ok": False, "error": msg})

    def _read_json(self):
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ValueError("content-type must be application/json")
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            raise ValueError("missing content length")
        try:
            length = int(raw_len)
        except Exception:
            raise ValueError("invalid content length")
        if length < 0 or length > MAX_BODY_SIZE:
            raise ValueError("request too large")
        try:
            self.connection.settimeout(READ_TIMEOUT)
        except Exception:
            pass
        raw = self.rfile.read(length) if length else b"{}"
        if len(raw) != length:
            raise ValueError("incomplete request body")
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            raise ValueError("invalid json")
        if not isinstance(data, dict):
            raise ValueError("json object required")
        return data

    def _send_preflight(self):
        origin = self._cors_origin()
        self.send_response(204)
        self.send_header("Content-Length", "0")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            vary = "Origin"
            if self.headers.get("Access-Control-Request-Private-Network"):
                vary = "Origin, Access-Control-Request-Private-Network"
            self.send_header("Vary", vary)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header("Access-Control-Max-Age", "600")
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def _preflight(self):
        host = (self.headers.get("Host") or "").lower()
        if host != EXPECTED_HOST.lower():
            self._reject(403, "invalid host")
            return False
        origin = self.headers.get("Origin")
        if ALLOWED_ORIGIN:
            if not origin or origin != ALLOWED_ORIGIN:
                self._reject(403, "forbidden origin")
                return False
        elif origin:
            # No allowed origin configured: reject cross-origin browser requests by default.
            self._reject(403, "origin not allowed")
            return False
        return True

    def _authorize(self):
        if not BRIDGE_TOKEN:
            self._reject(503, "bridge token not configured")
            return False
        if not _authorized(self):
            self._reject(401, "unauthorized")
            return False
        return True

    def do_OPTIONS(self):
        if not self._preflight():
            return
        self._send_preflight()

    def do_GET(self):
        if not self._preflight():
            return
        if not self._authorize():
            return
        path = urlparse(self.path).path
        if path == "/state":
            self._send(200, {"ok": True, "state": _public_state()})
        elif path == "/ping":
            self._send(200, {"ok": True, "status": "ok"})
        else:
            self._reject(404, "not found")

    def do_POST(self):
        if not self._preflight():
            return
        if not self._authorize():
            return
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
        except ValueError as e:
            self._reject(400, str(e))
            return
        except Exception:
            self._reject(400, "bad request")
            return

        if path == "/start":
            with lock:
                if state.get("status") in ("recording", "stopping", "processing", "starting"):
                    self._send(409, {"ok": False, "error": "busy", "state": _public_state()})
                    return
            page_id = payload.get("pageId") or payload.get("page_id")
            page_title = str(payload.get("title") or "")[:300]
            page_url = str(payload.get("url") or "")[:1000]
            if not _valid_page_id(page_id):
                self._reject(400, "invalid page id")
                return
            if page_url and not _is_flowus_url(page_url):
                self._reject(400, "invalid page url")
                return
            with lock:
                state.update({"started_at": time.time(), "status": "starting", "message": "starting", "result": None, "error": None,
                              "page_id": page_id, "page_title": page_title, "page_url": page_url})
            threading.Thread(target=worker_start, args=(page_id, page_title, page_url), daemon=True).start()
            self._send(200, {"ok": True})

        elif path == "/stop":
            with lock:
                if state.get("status") not in ("recording", "starting"):
                    self._send(409, {"ok": False, "error": "not recording", "state": _public_state()})
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
            self._reject(404, "not found")

    def log_message(self, fmt, *args):
        sys.stderr.write("[bridge] " + (fmt % args) + "\n")


if __name__ == "__main__":
    if HOST != "127.0.0.1":
        raise RuntimeError("Bridge must bind to 127.0.0.1")
    if not BRIDGE_TOKEN:
        print("ERROR: bridge_token is not configured. Set it in config.local.json (or BRIDGE_TOKEN env).", file=sys.stderr)
        sys.exit(2)
    if len(BRIDGE_TOKEN) < 32:
        print("ERROR: bridge_token is too short. Use a high-entropy random token with at least 32 characters.", file=sys.stderr)
        sys.exit(2)
    if not ALLOWED_ORIGIN:
        print("WARNING: bridge_allowed_origin is not configured; browser requests with Origin will be rejected.", file=sys.stderr)
    print(f"Bridge listening on http://{HOST}:{PORT}")
    print(f"HEX_EXE={HEX_EXE}")
    print(f"SYNC_SCRIPT={SYNC_SCRIPT}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
