(function () {
  if (window.__flowusVoiceNoteBooted) return;
  window.__flowusVoiceNoteBooted = true;

  const POS_KEY = "fvn_button_pos";
  const DEFAULT_POS = { right: 16, bottom: 88 }; // avoid common FlowUs bottom-right controls
  const TIP_AUTO_HIDE_MS = 3500;

  function getPageId() {
    const p = location.pathname || "";
    let m = p.match(/\/luna\/([0-9a-fA-F-]{36})/);
    if (m) return m[1];
    m = p.match(/\/share\/([0-9a-fA-F-]{36})/);
    if (m) return m[1];
    m = p.match(/\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:\/|$)/);
    if (m) return m[1];
    return null;
  }

  function send(type, payload) {
    return new Promise((resolve) => {
      try {
        if (typeof chrome === "undefined" || !chrome.runtime || typeof chrome.runtime.sendMessage !== "function") {
          resolve({ ok: false, error: "扩展连接已失效", fix: "请刷新当前 FlowUs 页面（如刚重载/更新了扩展）后重试。" });
          return;
        }
        chrome.runtime.sendMessage({ type, payload }, (res) => {
          const lastErr = chrome.runtime && chrome.runtime.lastError ? chrome.runtime.lastError.message : "";
          if (lastErr) {
            const msg = String(lastErr);
            if (msg.includes("Extension context invalidated") || msg.includes("Receiving end does not exist")) {
              resolve({ ok: false, error: "扩展连接已失效", fix: "请刷新当前 FlowUs 页面；若仍失败，请在 chrome://extensions 重载扩展后再刷新页面。" });
            } else {
              resolve({ ok: false, error: msg });
            }
            return;
          }
          resolve(res || { ok: false, error: "no response" });
        });
      } catch (e) {
        const msg = String(e && e.message ? e.message : e);
        if (msg.includes("Extension context invalidated")) {
          resolve({ ok: false, error: "扩展连接已失效", fix: "请刷新当前 FlowUs 页面后重试。" });
        } else {
          resolve({ ok: false, error: msg });
        }
      }
    });
  }

  function storageGet(key) {
    return new Promise((resolve) => {
      try {
        if (!chrome?.storage?.local) return resolve(null);
        chrome.storage.local.get([key], (data) => resolve(data ? data[key] : null));
      } catch (e) { resolve(null); }
    });
  }

  function storageSet(obj) {
    try { chrome?.storage?.local?.set(obj); } catch (e) {}
  }

  let root, btn, tipEl, polling;
  let tipTimer = null;
  let dragState = null;
  let suppressClick = false;

  function clamp(val, min, max) { return Math.min(Math.max(val, min), max); }

  async function loadPosition() {
    const pos = await storageGet(POS_KEY);
    if (pos && Number.isFinite(pos.left) && Number.isFinite(pos.top)) {
      return { left: pos.left, top: pos.top };
    }
    return null;
  }

  function applyPosition(pos) {
    if (!root) return;
    const w = 28, h = 28, margin = 4;
    const maxLeft = Math.max(margin, window.innerWidth - w - margin);
    const maxTop = Math.max(margin, window.innerHeight - h - margin);
    if (pos && Number.isFinite(pos.left) && Number.isFinite(pos.top)) {
      const left = clamp(pos.left, margin, maxLeft);
      const top = clamp(pos.top, margin, maxTop);
      root.style.left = left + "px";
      root.style.top = top + "px";
      root.style.right = "auto";
      root.style.bottom = "auto";
    } else {
      root.style.right = DEFAULT_POS.right + "px";
      root.style.bottom = DEFAULT_POS.bottom + "px";
      root.style.left = "auto";
      root.style.top = "auto";
    }
  }

  function ensureUI() {
    root = document.getElementById("fvn-root");
    if (!root) {
      root = document.createElement("div");
      root.id = "fvn-root";
      root.innerHTML = `
        <button id="fvn-btn" type="button" aria-label="语音笔记">🎙️</button>
        <div id="fvn-tip"></div>
      `;
      (document.body || document.documentElement).appendChild(root);
      btn = root.querySelector("#fvn-btn");
      tipEl = root.querySelector("#fvn-tip");
      btn.addEventListener("click", onClick, true);
      btn.addEventListener("pointerdown", onPointerDown);
      window.addEventListener("resize", onResize);
      loadPosition().then(applyPosition);
    } else {
      btn = root.querySelector("#fvn-btn");
      tipEl = root.querySelector("#fvn-tip");
    }
    return !!(btn && tipEl);
  }

  function onResize() {
    if (!root) return;
    if (root.style.left && root.style.left !== "auto") {
      const left = parseFloat(root.style.left) || 0;
      const top = parseFloat(root.style.top) || 0;
      applyPosition({ left, top });
    }
  }

  function onPointerDown(e) {
    if (e.button !== 0) return;
    ensureUI();
    const rect = root.getBoundingClientRect();
    dragState = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      origLeft: rect.left,
      origTop: rect.top,
      moved: false,
    };
    try { btn.setPointerCapture(e.pointerId); } catch (_) {}
    btn.addEventListener("pointermove", onPointerMove);
    btn.addEventListener("pointerup", onPointerUp);
    btn.addEventListener("pointercancel", onPointerUp);
  }

  function onPointerMove(e) {
    if (!dragState || e.pointerId !== dragState.pointerId) return;
    const dx = e.clientX - dragState.startX;
    const dy = e.clientY - dragState.startY;
    if (!dragState.moved && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) {
      dragState.moved = true;
      suppressClick = true;
    }
    if (dragState.moved) {
      applyPosition({ left: dragState.origLeft + dx, top: dragState.origTop + dy });
    }
  }

  function onPointerUp(e) {
    if (!dragState || e.pointerId !== dragState.pointerId) return;
    btn.removeEventListener("pointermove", onPointerMove);
    btn.removeEventListener("pointerup", onPointerUp);
    btn.removeEventListener("pointercancel", onPointerUp);
    try { btn.releasePointerCapture(e.pointerId); } catch (_) {}
    if (dragState.moved) {
      const rect = root.getBoundingClientRect();
      storageSet({ [POS_KEY]: { left: rect.left, top: rect.top } });
      setTimeout(() => { suppressClick = false; }, 0);
    }
    dragState = null;
  }

  function showTip(text, sticky) {
    if (!ensureUI()) return;
    tipEl.textContent = text || "";
    if (tipTimer) { clearTimeout(tipTimer); tipTimer = null; }
    if (text) {
      root.classList.add("fvn-show-tip");
      if (!sticky) {
        tipTimer = setTimeout(() => {
          root.classList.remove("fvn-show-tip");
          // keep text for hover/title context? clear to reduce clutter
          tipEl.textContent = "";
        }, TIP_AUTO_HIDE_MS);
      }
    } else {
      root.classList.remove("fvn-show-tip");
    }
  }

  function setUI(st) {
    if (!ensureUI()) return;
    const s = (st && st.status) || "idle";
    const msg = (st && st.message) || "";
    const err = (st && st.error) || (st && st.has_error ? "处理失败，请查看本地 bridge 日志" : "");
    btn.className = "";
    btn.disabled = false;

    const pid = getPageId();
    if (s === "idle") {
      btn.textContent = "🎙️";
      btn.classList.add("fvn-idle");
      const hint = pid ? "就绪：点击开始录音并归档到当前页（可拖动位置）" : "就绪：请打开具体 FlowUs 页面后使用（可拖动位置）";
      btn.title = "语音笔记 - " + hint;
      showTip("", false);
    } else if (s === "recording" || s === "starting") {
      btn.textContent = "⏺";
      btn.classList.add("fvn-rec");
      const hint = msg || "录音中…点击停止并归档";
      btn.title = "语音笔记 - " + hint;
      showTip(hint, true);
    } else if (s === "stopping" || s === "processing") {
      btn.textContent = "⏳";
      btn.classList.add("fvn-busy");
      btn.disabled = true;
      const hint = msg || "处理中…";
      btn.title = "语音笔记 - " + hint;
      showTip(hint, true);
    } else if (s === "done") {
      btn.textContent = "✓";
      btn.classList.add("fvn-done");
      const hint = "完成：已回传到当前页";
      btn.title = "语音笔记 - " + hint;
      showTip(hint, false);
    } else if (s === "error") {
      btn.textContent = "⚠";
      btn.classList.add("fvn-err");
      const hint = "失败：" + (err || msg || "未知错误");
      btn.title = "语音笔记 - " + hint;
      showTip(hint, true);
    } else {
      btn.textContent = "🎙️";
      const hint = msg || s;
      btn.title = "语音笔记 - " + hint;
      showTip(hint, false);
    }
  }

  function formatError(res, fallback) {
    if (!res) return fallback || "未知错误";
    const msg = res.error || fallback || "未知错误";
    const fix = res.fix ? `（${res.fix}）` : "";
    return msg + fix;
  }

  async function refresh() {
    if (!ensureUI()) return;
    const res = await send("state");
    if (res && res.ok) setUI(res.state);
    else setUI({ status: "error", error: formatError(res, "桥接未连接（请启动 bridge.py）") });
  }

  async function onClick(e) {
    if (suppressClick) { e.preventDefault(); e.stopPropagation(); return; }
    const res = await send("state");
    const st = res && res.ok ? res.state : { status: "idle" };
    const s = st.status || "idle";
    if (s === "idle" || s === "done" || s === "error") {
      if (s === "done" || s === "error") await send("reset");
      const pageId = getPageId();
      if (!pageId) {
        setUI({ status: "error", error: "未识别到当前 FlowUs 页面 ID，请在具体页面上使用" });
        return;
      }
      setUI({ status: "starting", message: "正在唤起 Hex 录音…" });
      const r = await send("start", { pageId, url: location.href, title: document.title || "" });
      if (!r || !r.ok) setUI({ status: "error", error: formatError(r, "启动失败") });
    } else if (s === "recording" || s === "starting") {
      setUI({ status: "stopping", message: "停止并整理上传…" });
      const r = await send("stop");
      if (!r || !r.ok) setUI({ status: "error", error: formatError(r, "停止失败") });
    }
    setTimeout(refresh, 600);
  }

  function boot() {
    ensureUI();
    refresh();
    if (!polling) polling = setInterval(refresh, 2000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
  // SPA resilience
  setInterval(ensureUI, 2000);
  window.addEventListener("popstate", () => setTimeout(boot, 300));
  const _push = history.pushState;
  history.pushState = function () { _push.apply(this, arguments); setTimeout(boot, 300); };
  const _replace = history.replaceState;
  history.replaceState = function () { _replace.apply(this, arguments); setTimeout(boot, 300); };
})();