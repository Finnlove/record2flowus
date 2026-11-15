const BRIDGE = "http://127.0.0.1:27123";

async function getConfig() {
  const data = await chrome.storage.local.get({ bridge_token: "", bridge_port: 27123 });
  const port = Number(data.bridge_port) || 27123;
  return {
    token: (data.bridge_token || "").trim(),
    base: `http://127.0.0.1:${port}`,
  };
}

async function bridge(path, method = "GET", body) {
  const cfg = await getConfig();
  if (!cfg.token) throw new Error("未配置 bridge token，请在扩展选项中设置");
  const opts = {
    method,
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${cfg.token}`,
    },
  };
  if (body !== undefined) opts.body = JSON.stringify(body || {});
  let res, text, data;
  try {
    res = await fetch(cfg.base + path, opts);
    text = await res.text();
  } catch (e) {
    throw new Error("无法连接本地桥接（请确认 bridge.py 已启动且 token/端口正确）");
  }
  try { data = JSON.parse(text); } catch { data = { ok: false, error: text || "invalid response" }; }
  if (!res.ok || data.ok === false) {
    throw new Error((data && data.error) || ("HTTP " + res.status));
  }
  return data;
}

function isAllowedSender(sender) {
  if (!sender || sender.id !== chrome.runtime.id) return false;
  const url = sender.url || "";
  return url.startsWith("https://flowus.cn/") || url.startsWith("https://");
}

function isFlowusSender(sender) {
  if (!sender || sender.id !== chrome.runtime.id) return false;
  const url = sender.url || "";
  return url.startsWith("https://flowus.cn/") || /^https:\/\/([a-z0-9-]+\.)*flowus\.cn\//i.test(url);
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (!isFlowusSender(sender)) {
        return { ok: false, error: "unauthorized sender" };
      }
      if (!msg || typeof msg.type !== "string") throw new Error("invalid message");
      if (msg.type === "ping") return await bridge("/ping");
      if (msg.type === "state") return await bridge("/state");
      if (msg.type === "start") return await bridge("/start", "POST", msg.payload || {});
      if (msg.type === "stop") return await bridge("/stop", "POST", {});
      if (msg.type === "reset") return await bridge("/reset", "POST", {});
      throw new Error("unknown message");
    } catch (e) {
      return { ok: false, error: String(e && e.message ? e.message : e) };
    }
  })().then(sendResponse);
  return true;
});
