(function () {
  if (window.__flowusVoiceNoteBooted) return;
  window.__flowusVoiceNoteBooted = true;

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

  let btn, statusEl, polling;

  function ensureUI() {
    let root = document.getElementById("fvn-root");
    if (!root) {
      root = document.createElement("div");
      root.id = "fvn-root";
      root.innerHTML = `
        <button id="fvn-btn" title="语音笔记：点击开始/停止">🎙️ 语音笔记</button>
        <div id="fvn-status" class="fvn-hidden"></div>
      `;
      (document.body || document.documentElement).appendChild(root);
      btn = root.querySelector("#fvn-btn");
      statusEl = root.querySelector("#fvn-status");
      btn.addEventListener("click", onClick);
    } else {
      btn = root.querySelector("#fvn-btn");
      statusEl = root.querySelector("#fvn-status");
    }
    return !!(btn && statusEl);
  }

  function setUI(st) {
    if (!ensureUI()) return;
    const s = (st && st.status) || "idle";
    const msg = (st && st.message) || "";
    const err = (st && st.error) || (st && st.has_error ? "处理失败，请查看本地 bridge 日志" : "");
    btn.className = "";
    statusEl.classList.remove("fvn-hidden");
    if (s === "idle") {
      btn.textContent = "🎙️ 语音笔记";
      btn.classList.add("fvn-idle");
      const pid = getPageId();
      statusEl.textContent = pid ? "就绪：点击开始录音并归档到当前页" : "就绪：请打开具体 FlowUs 页面后使用";
    } else if (s === "recording" || s === "starting") {
      btn.textContent = "⏹ 停止并归档";
      btn.classList.add("fvn-rec");
      statusEl.textContent = msg || "录音中…";
    } else if (s === "stopping" || s === "processing") {
      btn.textContent = "⏳ 处理中";
      btn.classList.add("fvn-busy");
      statusEl.textContent = msg || "处理中…";
      btn.disabled = true;
      return;
    } else if (s === "done") {
      btn.textContent = "🎙️ 语音笔记";
      btn.classList.add("fvn-done");
      statusEl.textContent = "完成：已回传到当前页";
    } else if (s === "error") {
      btn.textContent = "🎙️ 语音笔记";
      btn.classList.add("fvn-err");
      statusEl.textContent = "失败：" + (err || msg || "未知错误");
    } else {
      btn.textContent = "🎙️ 语音笔记";
      statusEl.textContent = msg || s;
    }
    btn.disabled = false;
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

  async function onClick() {
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
