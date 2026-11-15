const BRIDGE = "http://127.0.0.1:27123";

async function bridge(path, method = "GET", body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(BRIDGE + path, opts);
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { ok: false, error: text }; }
  if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (msg.type === "ping") return await bridge("/ping");
      if (msg.type === "state") return await bridge("/state");
      if (msg.type === "start") return await bridge("/start", "POST", msg.payload);
      if (msg.type === "stop") return await bridge("/stop", "POST", {});
      if (msg.type === "reset") return await bridge("/reset", "POST", {});
      throw new Error("unknown message");
    } catch (e) {
      return { ok: false, error: String(e && e.message ? e.message : e) };
    }
  })().then(sendResponse);
  return true;
});
