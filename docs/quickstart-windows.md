# Quickstart (Windows): First Successful Archive

This guide walks a new user from download to the **first successful voice-note archive** in FlowUs. No source-code knowledge required.

> **Scope:** Windows, unpacked browser extension, local Hex + FlowUs CLI.  
> **Core principle:** Local audio/transcript are primary assets; FlowUs is the archive destination.

---

## 0. What you need before starting

| Requirement | How to verify | Notes |
|---|---|---|
| Windows | — | Reference implementation targets Windows. |
| Python 3.9+ | `python --version` | Needed to run bridge and helper scripts. |
| Hex (with meetings enabled) | Hex installed; `hex.exe` available; meetings enabled in Hex settings (`meetings.enabled=true`) | Hex provides local recording + ASR artifacts. |
| FlowUs CLI (authenticated) | `flowus --json whoami` succeeds | Must have write access to the target FlowUs page/space. |
| Chrome or Edge | — | For loading the unpacked MV3 extension. |
| A FlowUs page you can edit | Open it in browser | The extension archives to the **current** page. |

If anything is missing, install/configure it first, then continue.

---

## 1. Get the project

- **Option A:** Clone the repo: `git clone https://github.com/Finnlove/record2flowus.git`
- **Option B:** Download ZIP from GitHub and extract it.

Open PowerShell in the project folder (the one containing `bridge.py`, `setup.cmd`, `extension/`).

---

## 2. Run setup and doctor

1. **Create/update local config (recommended):**
   ```powershell
   .\setup.cmd
   # or: python scripts/setup.py
   ```
   - Accept/confirm detected `hex.exe` path (or enter it manually).
   - Let setup generate a strong `bridge_token` (>= 32 chars) when prompted/missing. **Copy this token** — you must paste the same value into the extension Options.
   - Set `bridge_port` (default `27123`) unless you have a conflict.
   - If you already know your extension ID, enter it as origin (`chrome-extension://<ID>`). If not, you can complete this after loading the extension (step 3) and re-run setup / edit `config.local.json`.

2. **Run diagnostics:**
   ```powershell
   .\doctor.cmd
   # or: python scripts/doctor.py
   ```
   Fix any **FAIL** items. Warnings are guidance (e.g. bridge not started yet, origin placeholder until extension is loaded).

> `config.local.json` is gitignored and stores your local settings/token. Do not commit it.

---

## 3. Load and configure the browser extension

1. Open `chrome://extensions` (or Edge equivalent) and enable **Developer mode**.
2. Click **Load unpacked** and select the project’s `extension/` folder.
3. Open the extension **Options** (Details → Extension options):
   - Paste the **same** `bridge_token` saved in `config.local.json`.
   - Set **Bridge port** to match `bridge_port` (default `27123`).
   - Copy the shown **Extension Origin** (`chrome-extension://<ID>`).
4. Put that Origin into `config.local.json` as `bridge_allowed_origin` (via `setup.cmd` or manual edit).
5. Click **Save settings**, then use **Test connection** in Options after the bridge is running (step 4). It reports actionable fixes for token/origin/port/unreachable issues.
6. Ensure the extension has site access to FlowUs pages.

> If you reload/remove and re-add the unpacked extension, confirm the extension ID/origin still matches and update config if needed.

---

## 4. Start the local bridge

```powershell
.\start.cmd
# recommended: runs config preflight, then starts bridge
```

Alternatives: `python bridge.py --check`, `python bridge.py`, `.\start-bridge.cmd`.

- Bridge listens on `http://127.0.0.1:27123` by default (loopback only).
- Leave this process running while using the extension.
- If startup fails, read the printed fix and/or run `doctor.cmd`. After changing `config.local.json`, restart the bridge.

(Optional) For auto-start at login, copy `start-bridge-silent.vbs.example` to `start-bridge-silent.vbs`, adjust if needed, and place it in `shell:startup`.

---

## 5. First archive (end-to-end test)

1. Confirm **Hex** is ready (microphone/model configured, meetings enabled) and **FlowUs CLI** is authenticated (`flowus --json whoami`).
2. Open a **specific FlowUs page you can edit** in Chrome/Edge.
3. Click the floating **“🎙️ 语音笔记”** button to start recording (status should show recording/starting).
4. Speak briefly (a short test is enough), then click again to **stop and archive**.
5. Wait for processing/upload to complete (button/status progresses through stopping → processing → done).
6. Verify on the **same FlowUs page**: archived content is appended (overview/summary/points/todos, collapsible transcript, audio/file block, and mind map if enabled).

**Success criteria:** the page contains the newly appended voice-note content from your test recording.

---

## 6. If something goes wrong

- Run `doctor.cmd` and/or `python bridge.py --check`.
- Use the extension Options **Test connection** for auth/origin/connectivity diagnosis.
- See [troubleshooting.md](troubleshooting.md) for common symptoms and fixes.
- Check the bridge console output for detailed processing/upload errors (UI shows sanitized status).

**Important:** local Hex artifacts (audio/transcript/manifest under your Hex meetings/export directories) are your primary records. If FlowUs upload/append fails, keep local artifacts until the issue is resolved (retry/idempotent recovery tooling is planned in later milestones).

---

## Quick reference: config alignment

These three must match:

1. `config.local.json` → `bridge_token`, `bridge_port`, `bridge_allowed_origin`
2. Extension Options → same `bridge_token`, same port
3. Running bridge → restarted after any config change

Bridge is localhost-only and authenticated; never expose it beyond `127.0.0.1`.
