(async function () {
  const tokenEl = document.getElementById("bridge_token");
  const portEl = document.getElementById("bridge_port");
  const statusEl = document.getElementById("status");
  const saveBtn = document.getElementById("save");
  const testBtn = document.getElementById("test");
  const testResult = document.getElementById("testResult");
  const extOriginEl = document.getElementById("extOrigin");
  const copyOriginBtn = document.getElementById("copyOrigin");
  const copyStatus = document.getElementById("copyStatus");

  const origin = `chrome-extension://${chrome.runtime.id}`;
  extOriginEl.textContent = origin;

  function setStatus(msg, isErr) {
    statusEl.textContent = msg || "";
    statusEl.className = isErr ? "err" : "";
  }

  function setTestResult(msg, ok) {
    testResult.textContent = msg || "";
    testResult.className = ok === true ? "ok" : ok === false ? "fail" : "hint";
  }

  const data = await chrome.storage.local.get({ bridge_token: "", bridge_port: 27123 });
  tokenEl.value = data.bridge_token || "";
  portEl.value = data.bridge_port || 27123;

  function validateInputs(showMsg) {
    const token = (tokenEl.value || "").trim();
    let port = parseInt(portEl.value, 10);
    if (!Number.isFinite(port) || port < 1 || port > 65535) port = NaN;
    if (token && token.length < 32) {
      if (showMsg) setStatus("Token 长度不足（至少 32 字符）", true);
      return { ok: false, token, port };
    }
    if (!Number.isFinite(port)) {
      if (showMsg) setStatus("端口无效（1-65535）", true);
      return { ok: false, token, port };
    }
    return { ok: true, token, port };
  }

  saveBtn.addEventListener("click", async () => {
    const v = validateInputs(true);
    if (!v.ok) return;
    await chrome.storage.local.set({ bridge_token: v.token, bridge_port: v.port });
    if (!v.token) {
      setStatus("已保存（但 Token 为空，扩展无法连接桥接）", true);
    } else {
      setStatus("已保存。请确保 config.local.json 中 token/端口一致，且 bridge_allowed_origin 已设为本扩展 Origin 并重启桥接。", false);
    }
    setTimeout(() => { if (statusEl.textContent.startsWith("已保存")) setStatus("", false); }, 4000);
  });

  copyOriginBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(origin);
      copyStatus.textContent = "已复制";
    } catch (e) {
      copyStatus.textContent = "复制失败，请手动复制";
    }
    setTimeout(() => { copyStatus.textContent = ""; }, 2000);
  });

  testBtn.addEventListener("click", async () => {
    const v = validateInputs(true);
    if (!v.ok) {
      setTestResult("请先修正输入后再测试。", false);
      return;
    }
    await chrome.storage.local.set({ bridge_token: v.token, bridge_port: v.port });
    setTestResult("正在测试连接...", null);
    try {
      const res = await chrome.runtime.sendMessage({ type: "testConnection" });
      if (res && res.ok) {
        setTestResult("✅ 连接成功：桥接可达且认证通过。", true);
      } else {
        const msg = (res && res.message) || (res && res.error) || "连接失败";
        const fix = (res && res.fix) ? `\n建议：${res.fix}` : "";
        setTestResult(`❌ ${msg}${fix}`, false);
      }
    } catch (e) {
      setTestResult("❌ 测试失败：无法与扩展后台通信。请重新加载扩展后重试。", false);
    }
  });
})();