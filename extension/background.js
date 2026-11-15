const BRIDGE_DEFAULT_PORT = 27123;

async function getConfig() {
  const data = await chrome.storage.local.get({ bridge_token: "", bridge_port: BRIDGE_DEFAULT_PORT });
  let port = Number(data.bridge_port);
  if (!Number.isFinite(port) || port < 1 || port > 65535) port = BRIDGE_DEFAULT_PORT;
  return {
    token: (data.bridge_token || "").trim(),
    base: `http://127.0.0.1:${port}`,
    port,
  };
}

function classifyBridgeError(err, status, dataError) {
  const msg = String((err && err.message) || err || "");
  const de = String(dataError || "");
  if (msg.includes("未配置 bridge token") || de.includes("未配置 bridge token")) {
    return { category: "not_configured", message: "未配置 bridge token，请在扩展选项中设置", fix: "打开扩展选项，填写与 config.local.json 相同的 bridge_token 并保存。" };
  }
  if (msg === "unreachable" || msg.includes("无法连接本地桥接")) {
    return { category: "unreachable", message: "无法连接本地桥接", fix: "确认 bridge.py 已启动、端口与扩展选项一致，且本机未阻止本地连接。" };
  }
  if (status === 401 || de === "unauthorized" || msg === "unauthorized") {
    return { category: "unauthorized", message: "认证失败（Token 无效或不匹配）", fix: "检查扩展选项中的 bridge_token 是否与 config.local.json 完全一致，修改后重启 bridge.py。" };
  }
  if (status === 403 && (de === "forbidden origin" || msg === "forbidden origin")) {
    return { category: "forbidden_origin", message: "Origin 被拒绝（扩展 ID 与桥接配置不匹配）", fix: "将 config.local.json 的 bridge_allowed_origin 设置为本扩展 Origin（chrome-extension://<扩展ID>）并重启 bridge.py。" };
  }
  if (status === 403 && (de === "origin not allowed" || msg === "origin not allowed")) {
    return { category: "forbidden_origin", message: "桥接未配置允许的扩展 Origin", fix: "在 config.local.json 设置 bridge_allowed_origin 为本扩展 Origin，并重启 bridge.py。" };
  }
  if (status === 403) {
    return { category: "forbidden", message: de || "请求被拒绝", fix: "检查桥接配置（Host/Origin）与扩展设置是否一致。" };
  }
  if (status === 503 || de === "bridge token not configured") {
    return { category: "bridge_not_configured", message: "桥接未配置 Token", fix: "在 config.local.json 设置 bridge_token（>=32字符）后重启 bridge.py。" };
  }
  if (status === 400) {
    return { category: "bad_request", message: de || "请求参数无效", fix: "检查请求参数与桥接版本是否匹配。" };
  }
  if (status === 409) {
    return { category: "conflict", message: de || "当前状态冲突", fix: "等待当前任务结束或在空闲状态重试。" };
  }
  return { category: "error", message: de || msg || "未知错误", fix: "查看 bridge.py 控制台输出与扩展设置。" };
}

async function bridge(path, method = "GET", body) {
  const cfg = await getConfig();
  if (!cfg.token) {
    const err = new Error("未配置 bridge token，请在扩展选项中设置");
    err.code = "not_configured";
    throw err;
  }
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
    const err = new Error("unreachable");
    err.cause = e;
    throw err;
  }
  try { data = JSON.parse(text); } catch { data = { ok: false, error: text || "invalid response" }; }
  if (!res.ok || data.ok === false) {
    const err = new Error((data && data.error) || ("HTTP " + res.status));
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

async function testConnection() {
  const cfg = await getConfig();
  if (!cfg.token) {
    return { ok: false, ...classifyBridgeError(new Error("未配置 bridge token，请在扩展选项中设置")) };
  }
  if (cfg.token.length < 32) {
    return { ok: false, category: "not_configured", message: "bridge token 长度不足（需 >= 32 字符）", fix: "在扩展选项中填写与 config.local.json 相同的高强度 token。" };
  }
  try {
    const data = await bridge("/ping", "GET");
    return { ok: true, category: "ok", message: "连接成功，认证通过", fix: "", data };
  } catch (e) {
    const info = classifyBridgeError(e, e && e.status, e && e.data && e.data.error);
    return { ok: false, ...info };
  }
}

function isFlowusSender(sender) {
  if (!sender || sender.id !== chrome.runtime.id) return false;
  const url = sender.url || "";
  return url.startsWith("https://flowus.cn/") || /^https:\/\/([a-z0-9-]+\.)*flowus\.cn\//i.test(url);
}

function isOptionsSender(sender) {
  if (!sender || sender.id !== chrome.runtime.id) return false;
  const url = sender.url || "";
  const opt = chrome.runtime.getURL("options.html");
  return url === opt || url.startsWith(opt + "?") || url.startsWith(opt + "#");
}

function toUserError(e) {
  const info = classifyBridgeError(e, e && e.status, e && e.data && e.data.error);
  const err = new Error(info.message);
  err.category = info.category;
  err.fix = info.fix;
  return err;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (!msg || typeof msg.type !== "string") throw new Error("invalid message");

      if (isOptionsSender(sender)) {
        if (msg.type === "testConnection") return await testConnection();
        if (msg.type === "getExtensionInfo") {
          return { ok: true, id: chrome.runtime.id, origin: `chrome-extension://${chrome.runtime.id}` };
        }
        return { ok: false, error: "unknown message" };
      }

      if (!isFlowusSender(sender)) {
        return { ok: false, error: "unauthorized sender" };
      }

      if (msg.type === "ping") return await bridge("/ping");
      if (msg.type === "state") return await bridge("/state");
      if (msg.type === "start") return await bridge("/start", "POST", msg.payload || {});
      if (msg.type === "stop") return await bridge("/stop", "POST", {});
      if (msg.type === "reset") return await bridge("/reset", "POST", {});
      if (msg.type === "retry") return await bridge("/retry", "POST", msg.payload || {});
      if (msg.type === "archives") return await bridge("/archives", "GET");
      throw new Error("unknown message");
    } catch (e) {
      const info = classifyBridgeError(e, e && e.status, e && e.data && e.data.error);
      return { ok: false, error: info.message, category: info.category, fix: info.fix };
    }
  })().then(sendResponse);
  return true;
});
