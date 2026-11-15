(async function () {
  const tokenEl = document.getElementById("bridge_token");
  const portEl = document.getElementById("bridge_port");
  const statusEl = document.getElementById("status");
  const saveBtn = document.getElementById("save");

  const data = await chrome.storage.local.get({ bridge_token: "", bridge_port: 27123 });
  tokenEl.value = data.bridge_token || "";
  portEl.value = data.bridge_port || 27123;

  saveBtn.addEventListener("click", async () => {
    const bridge_token = (tokenEl.value || "").trim();
    let bridge_port = parseInt(portEl.value, 10);
    if (!Number.isFinite(bridge_port) || bridge_port < 1 || bridge_port > 65535) bridge_port = 27123;
    await chrome.storage.local.set({ bridge_token, bridge_port });
    statusEl.textContent = "已保存";
    setTimeout(() => { statusEl.textContent = ""; }, 2000);
  });
})();