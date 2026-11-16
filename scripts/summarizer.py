import json
import os
import re
import urllib.request
import urllib.error


def _safe_json_loads(s):
    try:
        return json.loads(s)
    except Exception:
        return None


def summarize_with_rules(make_auto_summary_fn, meeting):
    # make_auto_summary_fn is bridge.make_auto_summary (kept as source of truth for heuristic behavior)
    summary_md, points = make_auto_summary_fn(meeting)
    return {
        "ok": True,
        "mode": "rules",
        "summary_md": summary_md,
        "points": points or [],
        "fallback": False,
    }


def _extract_transcript_for_prompt(transcript, limit=6000):
    t = re.sub(r"\s+", " ", transcript or "").strip()
    if len(t) > limit:
        t = t[:limit] + "\n...(truncated)"
    return t


def _build_ollama_prompt(meeting, transcript_text):
    m = meeting.get("manifest") or {}
    mid = meeting.get("id") or ""
    title = m.get("title") or "Meeting"
    dur_s = int((m.get("duration_ms") or 0) / 1000)
    mm, ss = divmod(dur_s, 60)
    return (
        "你是语音笔记整理助手。输入是自动语音识别（ASR）的原始转写，可能含口语碎句、"
        "重复词和错误断句。请据此生成结构化中文整理结果。\n"
        "要求：\n"
        "1) 摘要：概括核心主题与结论，用通顺书面语复述，不要逐句照抄原话；不要编造未出现的信息。\n"
        "2) 要点：3-6 条，每条是完整、通顺、可独立阅读的句子（必须有主语和谓语），不要照搬口语碎片。\n"
        "3) 可行动项：仅在转写中有明确后续/待办意图时列出；没有则返回空数组。\n"
        "4) 修复明显的 ASR 重复/断字（如把 \"G G GPT\" 规范为 \"GPT\"），但不要改变原意。\n"
        "5) 只输出 JSON，不要输出其他文字。JSON 格式：{\"summary\":\"...\",\"points\":[\"...\"],\"todos\":[\"...\"]}\n\n"
        f"会议信息：id={mid}, title={title}, duration={mm:02d}:{ss:02d}\n"
        "转写：\n"
        f"{transcript_text}\n"
    )


def _parse_model_result(text):
    if not text:
        return None
    text = text.strip()
    # try direct json
    obj = _safe_json_loads(text)
    if not isinstance(obj, dict):
        # try to extract first json object
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            obj = _safe_json_loads(m.group(0))
    if not isinstance(obj, dict):
        return None
    summary = str(obj.get("summary") or "").strip()
    points = obj.get("points") or []
    todos = obj.get("todos") or []
    if not isinstance(points, list):
        points = [str(points)] if points else []
    if not isinstance(todos, list):
        todos = [str(todos)] if todos else []
    points = [str(x).strip() for x in points if str(x).strip()]
    todos = [str(x).strip() for x in todos if str(x).strip()]
    return {"summary": summary, "points": points[:8], "todos": todos[:8]}


def _format_summary_md(meeting, summary, points, todos):
    m = meeting.get("manifest") or {}
    mid = meeting.get("id") or ""
    dur_s = int((m.get("duration_ms") or 0) / 1000)
    mm, ss = divmod(dur_s, 60)
    from datetime import datetime, timedelta, timezone
    created_ms = m.get("created_at_ms") or 0
    dt = datetime.fromtimestamp(created_ms / 1000, tz=timezone(timedelta(hours=8))) if created_ms else None
    date_str = dt.strftime("%Y-%m-%d %H:%M（+08:00）") if dt else ""
    md = [
        "## 语音笔记整理（模型辅助）",
        "### 基本信息",
        f"- 会议ID：{mid}",
        f"- 时间：{date_str}",
        f"- 时长：{mm:02d}:{ss:02d}",
        "- 转写：Hex 本地 ASR",
        "### 摘要",
        summary or "（模型未生成有效摘要，请参考要点与原始转写）",
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
    return "\n".join(md)


def _preflight_ollama(base, timeout=2.0):
    """Fail fast when the local Ollama server is not reachable.

    Without this, a down server costs the full `ollama_timeout` (minutes) before
    falling back to rules, which looks like a hang.
    """
    try:
        req = urllib.request.Request(base + "/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(64)
        return True
    except Exception as e:
        raise RuntimeError(
            "ollama server not reachable at %s (%s); start it with `ollama serve` "
            "or set summary_mode=rules" % (base, e)
        )


def summarize_with_ollama(meeting, transcript_text, cfg=None):
    cfg = cfg or {}
    base = str(cfg.get("ollama_url") or os.environ.get("OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
    model = str(cfg.get("ollama_model") or os.environ.get("OLLAMA_MODEL") or "qwen2.5:3b").strip()
    timeout = int(cfg.get("ollama_timeout") or 180)
    _preflight_ollama(base)
    prompt = _build_ollama_prompt(meeting, _extract_transcript_for_prompt(transcript_text))
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "keep_alive": "10m",
        "options": {"temperature": 0.2, "num_predict": 900},
    }
    req = urllib.request.Request(
        base + "/api/generate",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    data = _safe_json_loads(body) or {}
    text = data.get("response") or ""
    parsed = _parse_model_result(text)
    if not parsed:
        raise RuntimeError("ollama returned unparseable summary")
    summary = parsed.get("summary") or ""
    points = parsed.get("points") or []
    todos = parsed.get("todos") or []
    if not summary and not points:
        raise RuntimeError("ollama returned empty summary")
    summary_md = _format_summary_md(meeting, summary, points, todos)
    return {
        "ok": True,
        "mode": "ollama",
        "summary_md": summary_md,
        "points": points,
        "todos": todos,
        "fallback": False,
        "model": model,
    }


def generate_summary(meeting, transcript_text, cfg=None, make_auto_summary_fn=None):
    cfg = cfg or {}
    mode = str(cfg.get("summary_mode") or cfg.get("summary_provider") or "rules").strip().lower()
    if mode in ("rule", "rules", "heuristic", ""):
        mode = "rules"
    if mode in ("ollama", "llm", "model"):
        try:
            return summarize_with_ollama(meeting, transcript_text, cfg)
        except Exception as e:
            if make_auto_summary_fn is None:
                raise
            res = summarize_with_rules(make_auto_summary_fn, meeting)
            res["fallback"] = True
            res["fallback_reason"] = str(e)[:300]
            res["requested_mode"] = "ollama"
            return res
    # default rules
    if make_auto_summary_fn is None:
        raise RuntimeError("rules summarizer unavailable")
    return summarize_with_rules(make_auto_summary_fn, meeting)
