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

# local helper (scripts/archive_store.py)
BASE_DIR_IMPORT = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR_IMPORT not in sys.path:
    sys.path.insert(0, BASE_DIR_IMPORT)
try:
    from scripts.archive_store import ArchiveStore, make_archive_id, default_db_path
except Exception:
    ArchiveStore = None
    make_archive_id = None
    default_db_path = None

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
            print("Fix: correct the JSON syntax/structure in the file above, or run `python scripts/setup.py` to rebuild config.local.json.", file=sys.stderr)
            print("Tip: run `python scripts/doctor.py` for a full diagnostic.", file=sys.stderr)
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
LEDGER_DB = os.environ.get("ARCHIVE_LEDGER_DB") or CFG.get("archive_ledger_db") or (default_db_path() if default_db_path else "")
LEDGER = ArchiveStore(LEDGER_DB) if (ArchiveStore and LEDGER_DB) else None

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


def _strip_transcript_prefix(line):
    s = line.strip()
    if not s:
        return ""
    # drop markdown headings like "# Meeting – 2:23 PM" or "## Transcript"
    if re.match(r"^#{1,6}\s+", s):
        if re.match(r"^#{1,6}\s*(transcript|转写|原始转写)\b", s, re.I):
            return ""
        # meeting title heading - usually noise for summary
        if re.match(r"^#{1,6}\s*meeting\b", s, re.I):
            return ""
    # speaker/timestamp formats: "**00:00 You** text", "00:00 You  text", "**Speaker** · 00:00"
    s = re.sub(r"^\*\*\d{1,2}:\d{2}(?::\d{2})?\s+[^*\n]+\*\*\s*", "", s)
    s = re.sub(r"^\d{1,2}:\d{2}(?::\d{2})?\s+[A-Za-z0-9_\u4e00-\u9fff]+\s*[：:·\-]?\s*", "", s)
    s = re.sub(r"^\*\*[^*\n]+\*\*\s*·\s*\d{1,2}:\d{2}(?::\d{2})?\s*", "", s)
    s = re.sub(r"^\[[^\]\n]{1,20}\]\s*", "", s)
    return s.strip()


def extract_transcript_text(transcript_md):
    body = transcript_md or ""
    m = re.search(r"^##\s*Transcript\s*$", body, re.M | re.I)
    if m:
        body = body[m.end():].strip()
    cleaned, seen = [], set()
    for raw in body.splitlines():
        s = _strip_transcript_prefix(raw)
        if not s:
            continue
        # skip pure separators/metadata remnants
        if re.fullmatch(r"[-_=*~\s]{3,}", s):
            continue
        key = re.sub(r"\s+", "", s)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(s)
    text = "\n".join(cleaned).strip()
    # collapse excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _split_sentences(text):
    if not text:
        return []
    # split on newlines and after sentence-ending punctuation (Chinese text often has no space)
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text)
    out = []
    for p in parts:
        s = re.sub(r"\s+", " ", p).strip(" \t\r\n-•·")
        if not s:
            continue
        out.append(s)
    return out


_FILLER_PHRASES = [
    "我想说的是", "就是说呢", "就是说", "其实呢", "其实", "然后呢", "那么呢", "这个呢", "那个呢",
    "这样子的", "这样的一个", "这么一个", "诸如此类的东西", "之类的东西", "之类的",
]


def _clean_spoken(s):
    if not s:
        return ""
    t = re.sub(r"\s+", " ", str(s)).strip()
    # remove standalone filler tokens (keep meaning, avoid over-deleting inside words)
    t = re.sub(r"\b(?:OK|ok)\b[，,\s]*", "", t)
    t = re.sub(r"(?:^|[，,。\s])(?:嗯+|啊+|呃+|哦+|唉+|哈+|呀+|吧|嘛)(?=[，,。\s]|$)", " ", t)
    for p in _FILLER_PHRASES:
        t = t.replace(p, "")
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"([，,])\s*([，,])+", "，", t)
    t = re.sub(r"[，,]\s*([。！？!?])", r"\1", t)
    t = t.strip(" \t\r\n-•·，,；;：:")
    # drop leading dangling conjunctions/punctuation fragments
    t = re.sub(r"^(?:所以|然后|那么|但是|不过|而且|因为|如果|就是|再一个就是|再一个|还有就是|还有|以及|并且|或者是说|或者)[，,、\s]*", "", t)
    t = re.sub(r"\s+", " ", t).strip(" \t\r\n-•·，,；;：:")
    return t


def _sentence_key(s):
    t = _clean_spoken(s)
    t = re.sub(r"[\s`*_~#>\[\]\(\)（）【】\"'“”‘’、，,。.!！？?；;：:·\-—]", "", t)
    return t[:40]


def _is_complete_enough(s):
    t = _clean_spoken(s).strip()
    if len(re.sub(r"[\s\W_]+", "", t, flags=re.UNICODE)) < 8:
        return False
    # avoid obvious fragments ending with dangling connectors
    if re.search(r"(的|了|呢|吗|吧|啊|在|把|和|与|及|或|对|给|向|从|到|是)$", t):
        # short trailing particle can be ok if sentence-like, but be careful
        if len(t) < 18:
            return False
    return True


def _format_point(s, limit=88):
    t = _clean_spoken(s)
    if not t:
        t = re.sub(r"\s+", " ", str(s or "")).strip()
    t = t.strip(" \t\r\n-•·，,；;：:")
    if not t:
        return ""
    # try to keep a complete sentence/clause: cut at nearest boundary before limit
    if len(t) > limit:
        window = t[:limit]
        cut = -1
        for sep in ["。", "！", "？", "；", "，", ",", " "]:
            idx = window.rfind(sep)
            if idx >= max(18, limit - 28):
                cut = idx
                break
        if cut > 0:
            t = window[:cut].rstrip(" \t\r\n-•·，,；;：:")
        else:
            t = window.rstrip(" \t\r\n-•·，,；;：:")
        if t and not t.endswith(("。", "！", "？", "…")):
            t += "…"
    else:
        # ensure it reads like a complete point
        if not t.endswith(("。", "！", "？")):
            t += "。"
    t = re.sub(r"。{2,}", "。", t)
    return t.strip()


def _select_representative_sentences(text, limit=6):
    # pick diverse, information-bearing sentences from across the transcript (not just the first few)
    sents = _split_sentences(text)
    scored = []
    for idx, s in enumerate(sents):
        cleaned = _clean_spoken(s)
        plain = re.sub(r"[\s\W_]+", "", cleaned, flags=re.UNICODE)
        if len(plain) < 8:
            continue
        score = len(plain)
        if re.search(r"(因为|所以|为了|希望|想要|可以|能够|需要|问题|困难|方法|工具|目的|记录|整理|总结|提高|帮助|使用|插件|语音|转写|归档|学习)", cleaned):
            score += 10
        if _is_complete_enough(cleaned):
            score += 4
        scored.append((idx, s, cleaned, score))
    if not scored:
        return []
    n = len(scored)
    picks = []
    if n <= limit:
        candidates = sorted(scored, key=lambda x: x[0])
    else:
        third = max(1, n // 3)
        buckets = [scored[:third], scored[third:2*third], scored[2*third:]]
        candidates = []
        for b in buckets:
            if b:
                candidates.extend(sorted(b, key=lambda x: -x[3])[:2])
        for item in sorted(scored, key=lambda x: (-x[3], x[0])):
            if item not in candidates:
                candidates.append(item)
        candidates = sorted(candidates[: limit * 3], key=lambda x: x[0])
    used = set()
    for idx, s, cleaned, _ in candidates:
        k = _sentence_key(s)
        if not k or k in used:
            continue
        if any(k[:18] and k[:18] == u[:18] for u in used):
            continue
        used.add(k)
        picks.append((idx, s, cleaned))
        if len(picks) >= limit:
            break
    picks.sort(key=lambda x: x[0])
    return [p[1] for p in picks]


def _pick_points(text, limit=5):
    # heuristic "analysis" baseline: select representative points across the talk and rewrite as readable points
    reps = _select_representative_sentences(text, limit=max(limit + 2, 7))
    points, seen = [], set()
    source = reps or _split_sentences(text)
    for s in source:
        cleaned = _clean_spoken(s)
        if not _is_complete_enough(cleaned):
            continue
        key = _sentence_key(s)
        if not key or key in seen:
            continue
        # skip near-duplicates
        if any(key[:18] and key[:18] == u[:18] for u in seen):
            continue
        seen.add(key)
        p = _format_point(s, limit=88)
        if p:
            points.append(p)
        if len(points) >= limit:
            break
    # fallback: if too few, relax completeness constraint
    if len(points) < min(2, limit):
        for s in _split_sentences(text):
            key = _sentence_key(s)
            if not key or key in seen:
                continue
            seen.add(key)
            p = _format_point(s, limit=88)
            plain = re.sub(r"[\s\W_]+", "", _clean_spoken(s), flags=re.UNICODE)
            if p and len(plain) >= 8:
                points.append(p)
            if len(points) >= limit:
                break
    return points


def _extract_action_items(text):
    if not text:
        return []
    # conservative cues: explicit assignment/reminder/next-step intent; prefer under-extraction over false positives
    strong_cues = (
        "待办", "TODO", "todo", "下一步要", "下一步需要", "需要你", "需要我", "需要我们", "请你", "请大家",
        "记得", "务必", "尽快", "别忘", "麻烦", "负责", "跟进一下", "安排一下", "去确认", "去修复", "去处理",
    )
    weak_cues = ("需要", "确认", "修复", "处理", "提交", "发送", "补充", "联系", "安排", "跟进")
    todos, seen = [], set()
    for s in _split_sentences(text):
        has_strong = any(k in s for k in strong_cues)
        has_weak = any(k in s for k in weak_cues)
        if not (has_strong or has_weak):
            continue
        # weak cues are easy to false-positive in discussion; require action-oriented phrasing
        if not has_strong:
            if not re.search(r"(需要|请|记得|务必|尽快|负责).{0,8}(做|改|修|补|发|提|联|确|处|查|写|测|安|跟)", s):
                continue
        # avoid meta/system phrases and pure narration
        if "幂等" in s and "meeting id" in s.lower():
            continue
        if re.search(r"(已经|已|刚刚|之前).{0,6}(完成|处理|修复|确认)", s):
            continue
        item = s.strip(" 。！？!?；;")
        if len(item) < 6:
            continue
        if len(item) > 60:
            item = item[:58].rstrip() + "…"
        key = re.sub(r"\s+", "", item)
        if not key or key in seen:
            continue
        seen.add(key)
        todos.append(item)
        if len(todos) >= 5:
            break
    return todos


def make_auto_summary(meeting):
    transcript = meeting.get("transcript") or ""
    text = extract_transcript_text(transcript)
    m = meeting.get("manifest") or {}
    dur_s = int((m.get("duration_ms") or 0) / 1000)
    mm, ss = divmod(dur_s, 60)
    created_ms = m.get("created_at_ms") or 0
    dt = datetime.fromtimestamp(created_ms / 1000, tz=timezone(timedelta(hours=8))) if created_ms else None
    date_str = dt.strftime("%Y-%m-%d %H:%M（+08:00）") if dt else ""
    points = _pick_points(text, limit=5)
    if text:
        # heuristic baseline (not LLM understanding): synthesize a compact overview from representative parts
        reps = _select_representative_sentences(text, limit=5)
        parts = []
        for s in (reps or _split_sentences(text)):
            c = _format_point(s, limit=56)
            c = c.rstrip("。…")
            if c and len(re.sub(r"[\s\W_]+", "", c, flags=re.UNICODE)) >= 8:
                parts.append(c)
            if len(parts) >= 4:
                break
        deduped, seen_k = [], set()
        for p in parts:
            k = re.sub(r"[\s\W_]+", "", p, flags=re.UNICODE)[:24]
            if not k or k in seen_k:
                continue
            seen_k.add(k)
            deduped.append(p)
        core = "；".join(deduped[:3]).strip("； ")
        if core:
            summary_para = f"本次语音主要围绕：{core}。"
            if len(text) > 180:
                summary_para += "（启发式整理，详见要点与原始转写）"
        else:
            norm = _clean_spoken(text)
            summary_para = (norm[:200] + "…") if len(norm) > 200 else (norm or "（未识别到有效转写内容，请查看原始转写/录音）")
        if len(summary_para) > 320:
            summary_para = summary_para[:318].rstrip() + "…"
    else:
        summary_para = "（未识别到有效转写内容，请查看原始转写/录音）"
    todos = _extract_action_items(text)
    md = [
        "## 语音笔记整理（自动生成）",
        "### 基本信息",
        f"- 会议ID：{meeting.get('id', '')}",
        f"- 时间：{date_str}",
        f"- 时长：{mm:02d}:{ss:02d}",
        "- 转写：Hex 本地 ASR",
        "### 摘要",
        summary_para,
        "### 要点",
    ]
    for p in (points or ["（未识别到明确要点，请参考原始转写）"]):
        md.append(f"- {p}")
    md.append("### 可行动项")
    if todos:
        for t in todos:
            md.append(f"- {t}")
    else:
        md.append("- （未从转写中识别到明确待办，可根据内容自行补充）")
    return "\n".join(md), points


def rt_node(text):
    return [{"type": "text", "text": {"content": text, "link": None},
             "annotations": {"bold": False, "italic": False, "strikethrough": False, "underline": False,
                             "code": False, "color": "default", "background_color": "default", "text_color": "default"},
             "plain_text": text, "href": None}]


def make_auto_mindmap(meeting, points):
    m = meeting.get("manifest") or {}
    dur_s = int((m.get("duration_ms") or 0) / 1000)
    mm, ss = divmod(dur_s, 60)
    title_text = f"语音笔记导图 {meeting.get('id', '')}（{mm:02d}:{ss:02d}）"
    point_children = [{"rich_text": rt_node(p[:40])} for p in (points[:6] or ["（未识别到明确要点）"])]
    return {
        "title": rt_node(title_text),
        "settings": {"layout": "both", "line_style": "curved", "branch_color": "gray", "sibling_node_alignment": True},
        "nodes": [
            {"rich_text": rt_node("基本信息"), "children": [
                {"rich_text": rt_node(f"会议ID: {meeting.get('id', '')}")},
                {"rich_text": rt_node(f"时长: {mm:02d}:{ss:02d}")},
                {"rich_text": rt_node("来源: Hex 本地录音+ASR")},
            ]},
            {"rich_text": rt_node("核心要点"), "children": point_children},
            {"rich_text": rt_node("后续"), "children": [
                {"rich_text": rt_node("复核原始转写与录音")},
                {"rich_text": rt_node("按需补充待办与结论")},
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


def process_and_upload(meeting_id, page_id, archive_id=None):
    meeting = load_meeting_artifacts(meeting_id)
    summary_md, points = make_auto_summary(meeting)
    mm_body = make_auto_mindmap(meeting, points)
    # per-run isolated temp workspace
    run_id = (archive_id or meeting_id or "run").replace(":", "_").replace("/", "_")
    tmp = os.path.join(tempfile.gettempdir(), "hex-flowus-voice-note", run_id + "_" + uuid.uuid4().hex[:8])
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
           "--summary-file", sum_path, "--mindmap-json", mm_path, "--meetings-dir", HEX_MEETINGS,
           "--work-dir", tmp]
    if archive_id:
        cmd += ["--archive-id", archive_id, "--skip-if-present"]

    if LEDGER and archive_id:
        LEDGER.update_steps(archive_id, {"prepare": {"ok": True, "work_dir": tmp}, "sync_started": {"ts": time.time()}})

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

    # classify result
    if result and result.get("skipped") and result.get("reason") == "marker_present":
        if LEDGER and archive_id:
            LEDGER.update_steps(archive_id, {"remote_marker": {"present": True}, "sync": {"skipped": True}})
        return {"ok": True, "skipped": True, "archive_id": archive_id, "result": result}

    if r.returncode == 0 and result and result.get("ok"):
        if LEDGER and archive_id:
            LEDGER.update_steps(archive_id, {
                "upload": result.get("audio"),
                "mindmap": {"id": result.get("mindmap_id"), "url": result.get("mindmap_url")},
                "append": {"ok": True},
                "verify": {"marker_verified": result.get("marker_verified")},
                "sync": {"ok": True},
            })
        return result

    # uncertain cases: append failed mid-way or process interrupted after partial writes
    uncertain = bool(result and (result.get("uncertain") or result.get("step") == "append"))
    if LEDGER and archive_id:
        LEDGER.update_steps(archive_id, {"sync": {"ok": False, "uncertain": uncertain, "returncode": r.returncode, "tail": out[-1500:]}})
    err = RuntimeError(out[-2000:] or "sync failed")
    setattr(err, "uncertain", uncertain)
    setattr(err, "result", result)
    raise err


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
    archive_id = None
    claim_worker = None
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

        if not page_id:
            raise RuntimeError("missing target page_id")
        if not _valid_page_id(str(page_id)):
            raise RuntimeError("invalid target page_id")

        # idempotency: archive identity per meeting+target page (append mode)
        if make_archive_id:
            archive_id = make_archive_id(mid, page_id, mode="append")
        if LEDGER and archive_id:
            claim = LEDGER.claim(archive_id, mid, target_page_id=page_id)
            if not claim.get("ok"):
                reason = claim.get("reason")
                if reason == "already_done":
                    with lock:
                        state.update({"status": "done", "message": "already archived (skipped)", "result": {"ok": True, "skipped": True, "reason": reason, "archive_id": archive_id}})
                    return
                if reason == "needs_review":
                    with lock:
                        state.update({"status": "error", "error": "archive needs review before retry (possible prior partial write)", "message": "needs review"})
                    return
                if reason == "already_running":
                    with lock:
                        state.update({"status": "error", "error": "same archive is already running", "message": "busy"})
                    return
                # default: treat as non-runnable
                with lock:
                    state.update({"status": "error", "error": f"archive claim rejected: {reason}", "message": "busy"})
                return
            claim_worker = claim.get("worker_id")

        with lock:
            state["message"] = "uploading to FlowUs"
            if archive_id:
                state["archive_id"] = archive_id

        result = process_and_upload(mid, page_id, archive_id=archive_id)
        with lock:
            state.update({"status": "done", "message": "uploaded", "result": result})
        if LEDGER and archive_id:
            LEDGER.mark_done(archive_id, result=result)
    except Exception as e:
        uncertain = bool(getattr(e, "uncertain", False))
        if LEDGER and archive_id:
            if uncertain:
                LEDGER.mark_needs_review(archive_id, str(e), result=getattr(e, "result", None))
            else:
                LEDGER.mark_error(archive_id, str(e))
        with lock:
            if uncertain:
                state.update({"status": "error", "error": "write result uncertain; manual review required before retry: " + str(e), "message": "needs review"})
            else:
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
        st = {
            "status": state.get("status"),
            "message": state.get("message") or "",
            "has_error": bool(state.get("error")),
            "page_bound": bool(state.get("page_id")),
        }
        if state.get("archive_id"):
            st["archive_id"] = state.get("archive_id")
        if state.get("meeting_id") and state.get("status") in ("recording", "stopping", "processing", "done"):
            st["meeting_bound"] = True
        return st


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
                state.update({"status": "idle", "message": "", "error": None, "result": None, "meeting_id": None, "archive_id": None})
            self._send(200, {"ok": True})
        else:
            self._reject(404, "not found")

    def log_message(self, fmt, *args):
        sys.stderr.write("[bridge] " + (fmt % args) + "\n")


def _print_startup_help(msg, fix=None):
    print(f"ERROR: {msg}", file=sys.stderr)
    if fix:
        print(f"Fix: {fix}", file=sys.stderr)
    print("Tip: run `python scripts/doctor.py` for diagnostics, or `python scripts/setup.py` to configure.", file=sys.stderr)


if __name__ == "__main__":
    if "--check" in sys.argv:
        # lightweight config/startup checks without serving
        start_ok = True
        browser_ok = True
        origin_re = re.compile(r"^chrome-extension://[a-p]{32}$")

        if not BRIDGE_TOKEN:
            print("[FAIL] bridge.start: bridge_token is not configured.", file=sys.stderr)
            print("       fix: Run `python scripts/setup.py` to generate a token, save it in config.local.json, and set the SAME token in the extension Options.", file=sys.stderr)
            start_ok = False
            browser_ok = False
        elif len(BRIDGE_TOKEN) < 32:
            print("[FAIL] bridge.start: bridge_token is too short (must be >= 32 characters).", file=sys.stderr)
            print("       fix: Generate a new high-entropy token via `python scripts/setup.py` and update extension Options.", file=sys.stderr)
            start_ok = False
            browser_ok = False
        else:
            print(f"[PASS] bridge.start: 配置可启动（已通过 token/基础配置检查，监听地址 http://{HOST}:{PORT}）。")

        if start_ok:
            if not ALLOWED_ORIGIN:
                print("[FAIL] bridge.browser_connect: bridge_allowed_origin 未配置，浏览器扩展无法连接（带 Origin 的请求会被拒绝）。", file=sys.stderr)
                print("       fix: 在 chrome://extensions 复制扩展 ID，设置 bridge_allowed_origin=chrome-extension://<ID> 到 config.local.json，并重启 bridge。", file=sys.stderr)
                browser_ok = False
            elif not origin_re.fullmatch(ALLOWED_ORIGIN):
                print(f"[FAIL] bridge.browser_connect: bridge_allowed_origin 格式无效：{ALLOWED_ORIGIN}", file=sys.stderr)
                print("       fix: 使用 chrome-extension://<32个小写字母a-p>，并与已加载扩展 ID 一致；修改后重启 bridge。", file=sys.stderr)
                browser_ok = False
            else:
                print(f"[PASS] bridge.browser_connect: 浏览器扩展可连接的桥接侧前提已满足（Origin 已配置且格式有效）。请确保扩展选项中的 token/端口与配置一致。")

        print("[INFO] bridge.e2e: 端到端归档可用性请运行 `python scripts/doctor.py`（检查 Hex、FlowUs CLI 认证、扩展文件等）。")

        if not start_ok:
            print("Tip: run `python scripts/doctor.py` for diagnostics, or `python scripts/setup.py` to configure.", file=sys.stderr)
            sys.exit(2)
        if not browser_ok:
            print("Tip: bridge 可启动，但浏览器扩展尚不可连接；修复上述问题后重启 bridge 并重测。", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    if HOST != "127.0.0.1":
        raise RuntimeError("Bridge must bind to 127.0.0.1")
    if not BRIDGE_TOKEN:
        _print_startup_help(
            "bridge_token is not configured.",
            "Run `python scripts/setup.py` to generate a token, save it in config.local.json, and set the SAME token in the extension Options page.",
        )
        sys.exit(2)
    if len(BRIDGE_TOKEN) < 32:
        _print_startup_help(
            "bridge_token is too short (must be >= 32 characters).",
            "Generate a new high-entropy token via `python scripts/setup.py`, update config.local.json, and update the extension Options.",
        )
        sys.exit(2)
    if not ALLOWED_ORIGIN:
        print("WARNING: bridge_allowed_origin is not configured; browser requests with Origin will be rejected.", file=sys.stderr)
        print("Fix: open chrome://extensions, copy the extension ID, set bridge_allowed_origin=chrome-extension://<ID> in config.local.json, then restart bridge.", file=sys.stderr)
    elif not re.fullmatch(r"chrome-extension://[a-p]{32}", ALLOWED_ORIGIN):
        print(f"WARNING: bridge_allowed_origin format looks invalid: {ALLOWED_ORIGIN}", file=sys.stderr)
        print("Fix: use chrome-extension://<32 lowercase letters a-p> matching your loaded extension ID.", file=sys.stderr)

    print(f"Bridge listening on http://{HOST}:{PORT}")
    print(f"HEX_EXE={HEX_EXE}")
    print(f"SYNC_SCRIPT={SYNC_SCRIPT}")
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        if getattr(e, "errno", None) in (98, 10048) or "address already in use" in str(e).lower() or "only one usage" in str(e).lower():
            _print_startup_help(
                f"port {PORT} is already in use on 127.0.0.1.",
                "Stop the process using this port, or change bridge_port in config.local.json (and set the SAME port in extension Options), then start again.",
            )
            sys.exit(2)
        _print_startup_help(f"failed to start server: {e}", "Check host/port permissions and configuration, then retry.")
        sys.exit(2)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.server_close()
        except Exception:
            pass
