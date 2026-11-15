import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

HEX_MEETINGS_DEFAULT = os.path.join(os.environ.get("APPDATA", ""), "hex", "meetings")
HEX_EXPORT_DIR = os.environ.get("HEX_EXPORT_DIR", "")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _is_inside(path, base):
    try:
        real_path = os.path.realpath(path)
        real_base = os.path.realpath(base)
        return os.path.commonpath([real_path, real_base]) == real_base
    except Exception:
        return False


def _validate_meeting_id(mid):
    return isinstance(mid, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", mid) is not None


def _validate_uuid(value, name):
    if not isinstance(value, str) or UUID_RE.fullmatch(value) is None:
        raise SystemExit(f"error: invalid {name}")
    return value


def _resolve_transcript_path(export_path, meeting_dir, meetings_dir):
    if isinstance(export_path, str) and export_path.strip():
        ep = os.path.normpath(export_path.strip())
        allowed = [meeting_dir, meetings_dir]
        if HEX_EXPORT_DIR:
            allowed.append(HEX_EXPORT_DIR)
        for base in allowed:
            if base and _is_inside(ep, base) and os.path.isfile(ep):
                return ep
    fallback = os.path.join(meeting_dir, "transcript.md")
    if os.path.isfile(fallback):
        return fallback
    return None


def run_flowus(args, body_path=None):
    cmd = ["flowus"] + args
    if body_path:
        cmd += ["--body", body_path]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    try:
        return json.loads(out or err)
    except Exception:
        raise RuntimeError(f"flowus failed: {err or out}")


def load_meeting(meeting_id=None, meetings_dir=None):
    meetings_dir = os.path.normpath(meetings_dir or os.environ.get("HEX_MEETINGS_DIR") or HEX_MEETINGS_DEFAULT)
    if not os.path.isdir(meetings_dir):
        raise RuntimeError(f"meetings dir not found: {meetings_dir}")
    if meeting_id and not _validate_meeting_id(meeting_id):
        raise RuntimeError("invalid meeting id")

    dirs = [os.path.join(meetings_dir, d) for d in os.listdir(meetings_dir) if os.path.isdir(os.path.join(meetings_dir, d))]
    candidates = []
    for d in dirs:
        if not _is_inside(d, meetings_dir):
            continue
        mf = os.path.join(d, "manifest.json")
        if not os.path.isfile(mf):
            continue
        try:
            with open(mf, "r", encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            continue
        mid = m.get("id") or os.path.basename(d)
        if not _validate_meeting_id(str(mid)):
            continue
        if meeting_id and str(mid) != meeting_id:
            continue
        if not meeting_id and m.get("status") != "complete":
            continue
        candidates.append((m.get("created_at_ms") or 0, d, m))
    if not candidates:
        raise RuntimeError("no matching complete meeting found")
    candidates.sort()
    _, d, m = candidates[-1]
    mid = str(m.get("id") or os.path.basename(d))
    if not _validate_meeting_id(mid):
        raise RuntimeError("invalid meeting id")

    transcript_md = _resolve_transcript_path(m.get("export_path"), d, meetings_dir)
    if not transcript_md:
        raise RuntimeError("transcript not found")
    with open(transcript_md, "r", encoding="utf-8") as f:
        transcript = f.read()

    wav = os.path.join(d, "microphone.wav")
    if not os.path.isfile(wav):
        wav = os.path.join(d, "system.wav")
    if not os.path.isfile(wav) or not _is_inside(wav, meetings_dir):
        raise RuntimeError("audio file not found")

    return {"id": mid, "dir": d, "manifest": m, "wav": wav, "transcript_path": transcript_md, "transcript": transcript}


def rt(text, bold=False, italic=False, code=False, color="default"):
    ann = {"bold": bold, "italic": italic, "strikethrough": False, "underline": False, "code": code,
           "color": color, "background_color": "default", "text_color": color}
    return [{"type": "text", "text": {"content": text, "link": None}, "annotations": ann, "plain_text": text, "href": None}]


def para(text, bold=False):
    return {"type": "paragraph", "paragraph": {"rich_text": rt(text, bold=bold), "text_color": "default", "color": "default", "background_color": "default"}}


def bullet(items_rt):
    return [{"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rti, "text_color": "default", "color": "default", "background_color": "default"}} for rti in items_rt]


def todo(text, checked=False):
    return {"type": "to_do", "to_do": {"rich_text": rt(text), "checked": checked, "text_color": "default", "color": "default", "background_color": "default"}}


def heading2(text, color="blue"):
    return {"type": "heading_2", "heading_2": {"rich_text": rt(text, bold=True, color=color), "is_toggleable": False, "text_color": color, "color": color, "background_color": "default"}}


def divider():
    return {"type": "divider", "divider": {}}


def callout(title, icon, bg="gray", title_color="default", title_bold=True, children=None, body_rich=None):
    rich = rt(title, bold=title_bold, color=title_color)
    if body_rich:
        rich = rich + body_rich
    c = {"type": "callout", "callout": {"rich_text": rich, "icon": {"type": "emoji", "emoji": icon},
        "background_color": bg, "text_color": "default", "color": "default"}}
    if children:
        c["children"] = children
    return c


def toggle(title, children):
    return {"type": "toggle", "toggle": {"rich_text": rt(title, bold=True), "text_color": "default", "color": "default", "background_color": "default"}, "children": children}


def column_list(cols):
    return {"type": "column_list", "column_list": {}, "children": [
        {"type": "column", "column": {"width_ratio": w}, "children": ch} for w, ch in cols
    ]}


def file_block(oss_name, size):
    return {"type": "file", "file": {"type": "file", "file": {"oss_name": oss_name, "size": size}, "caption": []}}


def chunk_paras(text, size=1800):
    parts = []
    buf = ""
    for line in text.splitlines():
        if len(buf) + len(line) + 1 > size and buf:
            parts.append(buf.rstrip())
            buf = line + "\n"
        else:
            buf += line + "\n"
    if buf.strip():
        parts.append(buf.rstrip())
    return parts or ([text[:size]] if text else [])


def parse_summary_sections(summary_text):
    out = {"basic": [], "summary": "", "points": [], "todos": []}
    sec = None
    for raw in (summary_text or "").splitlines():
        line = raw.strip()
        if line.startswith("### "):
            h = line[4:].strip()
            if "基本信息" in h: sec = "basic"
            elif "摘要" in h: sec = "summary"
            elif "要点" in h: sec = "points"
            elif "行动" in h or "待办" in h: sec = "todos"
            else: sec = None
            continue
        if not line or line.startswith("## "):
            continue
        if sec == "basic":
            out["basic"].append(line.lstrip("- ").strip())
        elif sec == "summary":
            out["summary"] += (line + " ")
        elif sec == "points":
            if line.startswith("- "): out["points"].append(line[2:].strip())
            elif line: out["points"].append(line)
        elif sec == "todos":
            if line.startswith("- "): out["todos"].append(line[2:].strip())
            elif line: out["todos"].append(line)
    out["summary"] = out["summary"].strip()
    return out


def build_beautified_blocks(meeting, transcript, summary_text, oss_name, size, mindmap_url):
    m = meeting["manifest"]
    mid = meeting["id"]
    created_ms = m.get("created_at_ms") or 0
    dt = datetime.fromtimestamp(created_ms / 1000, tz=timezone(timedelta(hours=8))) if created_ms else None
    date_str = dt.strftime("%Y-%m-%d %H:%M（+08:00）") if dt else ""
    dur_s = int((m.get("duration_ms") or 0) / 1000)
    mm, ss = divmod(dur_s, 60)
    dur_str = f"{mm:02d}:{ss:02d}（{dur_s}s）"
    secs = parse_summary_sections(summary_text)

    meta_items = [
        rt("会议ID：", bold=True) + rt(mid, code=True),
        rt("时间：", bold=True) + rt(date_str),
        rt("时长：", bold=True) + rt(dur_str),
        rt("转写：", bold=True) + rt("本地 ASR（Hex）"),
        rt("来源：", bold=True) + rt(f"Hex 本地会议录音 · {m.get('title') or 'Meeting'}"),
    ]
    blocks = []
    blocks.append(callout("会议速览", "📋", bg="light_blue", title_color="blue", children=bullet(meta_items)))
    blocks.append(divider())
    blocks.append(heading2("摘要与整理", color="blue"))

    basic_items = [rt(x) for x in (secs["basic"] or [f"会议 {mid}", date_str, f"时长 {dur_str}"])]
    left_children = [callout("基本信息", "🗂️", bg="gray", children=bullet(basic_items) if basic_items else [para("（无）")])]
    left_children.append(callout("摘要", "🧾", bg="light_yellow", children=[para(secs["summary"] or "（待补充摘要）")]))
    point_items = [rt(p) for p in (secs["points"] or ["（待补充要点）"])]
    right_children = [callout("要点", "💡", bg="light_green", children=bullet(point_items))]
    blocks.append(column_list([(1, left_children), (1, right_children)]))

    todos = secs["todos"] or ["复核转写与录音并补充关键信息", "提炼可执行行动项并归档"]
    blocks.append(callout("可行动项 / 后续计划", "✅", bg="light_red", children=[todo(t) for t in todos]))
    blocks.append(divider())

    blocks.append(heading2("原始转写", color="blue"))
    blocks.append(callout("说明：", "ℹ️", bg="gray", title_bold=True, body_rich=rt("以下为本地 ASR 原始转写（保留口语与停顿痕迹），折叠存放以保持页面清晰。")))
    t_children = [para(chunk) for chunk in chunk_paras(transcript, 1700)] or [para("（无转写内容）")]
    blocks.append(toggle(f"展开查看完整转写（{dur_str}）", t_children))
    blocks.append(divider())

    blocks.append(heading2("原始资料", color="blue"))
    audio_col = [callout("原始录音", "🎧", bg="light_blue", children=[para(f"microphone.wav（{round((size or 0)/1024/1024, 2)} MB）"), file_block(oss_name, size or 0)])]
    mm_children = [para(mindmap_url or "（未生成思维导图）")]
    mm_col = [callout("思维导图", "🧠", bg="light_purple", children=mm_children)]
    blocks.append(column_list([(1, audio_col), (1, mm_col)]))
    blocks.append(divider())
    blocks.append(callout("排版说明", "🎨", bg="yellow_green", children=[para("本页由自动化工作流生成，使用 FlowUs 原生块排版（Callout/Column/Toggle/To-do/文件块/思维导图）。")]))
    return blocks


def main():
    ap = argparse.ArgumentParser(description="Sync Hex meeting artifacts to FlowUs as a beautified voice note")
    ap.add_argument("--meeting-id", help="Hex meeting id (default: latest complete)")
    ap.add_argument("--meetings-dir", help="Hex meetings directory (default: %%APPDATA%%/hex/meetings)")
    ap.add_argument("--page-id", help="Append into this existing FlowUs page")
    ap.add_argument("--parent-page-id", help="Create a new child page under this page")
    ap.add_argument("--database-id", help="Create a new record/page in this database")
    ap.add_argument("--title", help="Note title (for new pages)")
    ap.add_argument("--summary-file", help="Path to summary markdown/text file")
    ap.add_argument("--mindmap-json", help="Path to FlowUs mindmap create JSON body")
    ap.add_argument("--no-mindmap", action="store_true")
    ap.add_argument("--plain", action="store_true", help="Disable beautified layout")
    args = ap.parse_args()

    meeting = load_meeting(args.meeting_id, args.meetings_dir)
    m = meeting["manifest"]
    created_ms = m.get("created_at_ms") or 0
    dt = datetime.fromtimestamp(created_ms / 1000, tz=timezone(timedelta(hours=8))) if created_ms else None
    date_str = dt.strftime("%Y-%m-%d %H:%M") if dt else ""
    title = args.title or f"语音笔记 {m.get('title') or 'Hex会议'} {date_str}".strip()

    if args.summary_file and os.path.isfile(args.summary_file):
        with open(args.summary_file, "r", encoding="utf-8") as f:
            summary_text = f.read().strip()
    else:
        summary_text = "## 语音笔记整理\n### 摘要\n" + (meeting.get("transcript") or "")[:400]

    if args.page_id:
        page_id = _validate_uuid(args.page_id, "page-id")
        page_url = f"https://flowus.cn/{page_id}"
        print("PAGE_TARGET", page_id, page_url)
    else:
        if not args.parent_page_id and not args.database_id:
            raise SystemExit("error: provide --page-id, or --parent-page-id, or --database-id")
        icon = {"type": "emoji", "emoji": "🎙️"}
        if args.parent_page_id:
            parent_page_id = _validate_uuid(args.parent_page_id, "parent-page-id")
            page_body = {"parent": {"type": "page_id", "page_id": parent_page_id},
                         "title": [{"type": "text", "text": {"content": title}}], "icon": icon}
        else:
            database_id = _validate_uuid(args.database_id, "database-id")
            page_body = {"parent": {"type": "database_id", "database_id": database_id},
                         "title": [{"type": "text", "text": {"content": title}}], "icon": icon}
        tmp = os.path.join(tempfile.gettempdir(), "vn_page.json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(page_body, f, ensure_ascii=False)
        res = run_flowus(["--json", "page", "create"], tmp)
        if not res.get("ok"):
            print(json.dumps(res, ensure_ascii=False, indent=2)); sys.exit(1)
        page_id = res["data"]["id"]
        page_url = res["data"].get("url") or f"https://flowus.cn/{page_id}"
        print("PAGE_OK", page_id, page_url)

    up = run_flowus(["--json", "file", "upload", "--parent-page", page_id, meeting["wav"]])
    if not up.get("ok"):
        print(json.dumps(up, ensure_ascii=False, indent=2)); sys.exit(1)
    oss_name = up["data"]["oss_name"]
    size = up["data"].get("size", 0)
    print("UPLOAD_OK", oss_name, size)

    mindmap_url = None
    mindmap_id = None
    if (not args.no_mindmap) and args.mindmap_json and os.path.isfile(args.mindmap_json):
        with open(args.mindmap_json, "r", encoding="utf-8") as f:
            mm_body = json.load(f)
        mm_body["parent"] = {"page_id": page_id}
        mp = os.path.join(tempfile.gettempdir(), "vn_mm.json")
        with open(mp, "w", encoding="utf-8") as f:
            json.dump(mm_body, f, ensure_ascii=False)
        mr = run_flowus(["--json", "mindmap", "create"], mp)
        if mr.get("ok"):
            md = mr["data"]
            mindmap_id = md.get("id")
            mindmap_url = md.get("url") or (f"https://flowus.cn/{mindmap_id}" if mindmap_id else None)
            print("MINDMAP_OK", mindmap_id, mindmap_url)
        else:
            print("MINDMAP_FAIL", json.dumps(mr, ensure_ascii=False))

    if args.plain:
        children = [para(f"会议 {meeting['id']} {date_str}"), para(summary_text), para((meeting.get("transcript") or "")[:3000]), file_block(oss_name, size)]
    else:
        children = build_beautified_blocks(meeting, meeting.get("transcript") or "", summary_text, oss_name, size, mindmap_url)

    for i in range(0, len(children), 80):
        batch = {"children": children[i:i+80]}
        bp = os.path.join(tempfile.gettempdir(), f"vn_blocks_{i}.json")
        with open(bp, "w", encoding="utf-8") as f:
            json.dump(batch, f, ensure_ascii=False)
        ar = run_flowus(["--json", "block", "append", page_id], bp)
        if not ar.get("ok"):
            print("APPEND_FAIL", json.dumps(ar, ensure_ascii=False)); sys.exit(1)
    print("APPEND_OK", len(children))
    print(json.dumps({"ok": True, "page_id": page_id, "page_url": page_url, "mindmap_id": mindmap_id, "mindmap_url": mindmap_url}, ensure_ascii=False))


if __name__ == "__main__":
    main()
