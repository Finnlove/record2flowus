# Hex → FlowUs Voice Note Workflow

A local Windows workflow that turns **Hex** meeting recordings (audio + local ASR transcript) into structured, beautified **FlowUs (息流)** voice-note pages.

- **One-click browser button:** start/stop Hex recording from a FlowUs page and append the processed note back to the **current page**.
- **Local-first capture:** uses Hex local meeting artifacts (`.wav` + transcript/manifest).
- **Automated formatting:** FlowUs-native blocks (overview, summary, points, todos, collapsible transcript, audio/file block, optional mind map).
- **CLI mode:** sync completed Hex meetings to an existing page or create new pages/records.

> **Current milestone:** `v0.2.0-milestone` (security-hardened bridge + E2E verified). Milestone 2 focuses on onboarding/productization so a new user can complete a first archive without reading source.

---

## Quickstart (new users)

**Goal:** complete your first successful archive on Windows.

1. **Prereqs:** Python 3.9+, Hex (meetings enabled), FlowUs CLI (`flowus --json whoami` OK), Chrome/Edge.
2. **Configure:** run `setup.cmd` then `doctor.cmd` (fix any FAILs).
3. **Extension:** load unpacked `extension/`, set the same token/port in **Options**, copy **Extension Origin** into `config.local.json` as `bridge_allowed_origin`, restart bridge.
4. **Start bridge:** `start.cmd` (recommended; runs preflight then starts).
5. **Archive:** open an editable FlowUs page → click **“🎙️ 语音笔记”** to start → click again to stop/archive → confirm appended content.

👉 **Full step-by-step guide:** [docs/quickstart-windows.md](docs/quickstart-windows.md)  
👉 **Troubleshooting:** [docs/troubleshooting.md](docs/troubleshooting.md)

---

## How it works

1. **Trigger:** browser extension button on a FlowUs page (captures page ID/URL/title) or CLI.
2. **Record & transcribe (Hex):** `hex meetings record/stop` produces `microphone.wav`, transcript, and `manifest.json` (plus export when available).
3. **Process:** bridge/sync reads artifacts, generates summary/points/todos (heuristic baseline), and optional mind-map payload.
4. **Publish to FlowUs:** uploads audio via FlowUs CLI, appends beautified blocks (and mind map) to the target page.

---

## Prerequisites

- **OS:** Windows
- **Python:** 3.9+
- **Hex:** installed locally, microphone/model configured, **meetings enabled** (`meetings.enabled = true`), CLI available (`hex.exe` path or auto-detected)
- **FlowUs CLI:** installed and authenticated (`flowus whoami` works, write access)
- **Browser:** Chrome/Edge (unpacked MV3 extension)

Verify quickly:
```powershell
python --version
flowus --json whoami
```

---

## Setup & configuration

### 1) Create local config
```powershell
setup.cmd
# or: python scripts/setup.py
doctor.cmd
# or: python scripts/doctor.py
```
- `setup` creates/updates `config.local.json` (gitignored), detects Hex where possible, generates a strong `bridge_token` when needed, and guides port/origin settings.
- `doctor` reports actionable PASS/WARN/FAIL checks without printing secrets.

**Manual alternative:** copy `config.example.json` to `config.local.json` and edit values.

Key settings (`config.local.json`):
```json
{
  "hex_exe": "C:\\Users\\<USER>\\AppData\\Roaming\\hex\\versions\\<VERSION>\\hex.exe",
  "hex_meetings_dir": "",
  "hex_export_dir": "",
  "bridge_port": 27123,
  "sync_script": "scripts/sync_to_flowus.py",
  "bridge_token": "<RANDOM_TOKEN_32+_CHARS>",
  "bridge_allowed_origin": "chrome-extension://<EXTENSION_ID>"
}
```
- **`bridge_token` (required):** high-entropy random secret (>= 32 chars). Must match the extension Options value.
- **`bridge_allowed_origin` (required for browser use):** must match the loaded extension origin; restart bridge after changes.
- Bridge binds to `127.0.0.1` only and validates Host/Origin, auth, and request inputs.
- Config parse errors stop startup with explicit file/reason. Do **not** commit `config.json`/`config.local.json`.

### 2) Load the extension
1. `chrome://extensions` → Developer mode → **Load unpacked** → select `extension/`.
2. Open **Options**: save the same token/port, copy the shown **Extension Origin**.
3. Set `bridge_allowed_origin` in `config.local.json` to that origin and restart bridge.
4. Use **Test connection** in Options to confirm auth/origin/reachability.

### 3) Start the bridge
```powershell
start.cmd                  # recommended: preflight check, then start
python bridge.py --check   # validate config without starting
python bridge.py
```
Authenticated endpoints: `/ping`, `/state`, `/start`, `/stop`, `/reset` (loopback only). Keep it running during use.

(Optional auto-start) copy `start-bridge-silent.vbs.example` to `start-bridge-silent.vbs`, adjust if needed, and place in `shell:startup`.

---

## Usage

### Browser button (recommended)
1. Open an editable FlowUs page.
2. Click **“🎙️ 语音笔记”** to start (binds current page).
3. Click again to stop; bridge waits for Hex transcript, processes, and appends audio + transcript + summary (+ optional mind map) to the page.

### CLI
Append to an existing page:
```powershell
python scripts/sync_to_flowus.py --page-id <FLOWUS_PAGE_ID>
```
Create a new page/record:
```powershell
python scripts/sync_to_flowus.py --parent-page-id <PARENT_PAGE_ID>
python scripts/sync_to_flowus.py --database-id <DATABASE_ID>
```
Useful options: `--meeting-id`, `--meetings-dir`, `--summary-file`, `--mindmap-json`, `--no-mindmap`, `--plain`, `--title`.

---

## Troubleshooting

- Run `doctor.cmd` and `python bridge.py --check`.
- Use extension Options **Test connection**.
- See **[docs/troubleshooting.md](docs/troubleshooting.md)** for symptoms, causes, and fixes.

**Data safety note:** local Hex audio/transcripts are primary assets. If upload/append fails, keep local artifacts until resolved (retry/idempotency tooling planned in M3).

---

## Repository structure

```text
.
├─ extension/                 # Browser extension (MV3) + Options/Test connection
├─ scripts/
│  ├─ sync_to_flowus.py       # Hex artifacts → FlowUs (beautified)
│  ├─ doctor.py               # Preflight diagnostics
│  └─ setup.py                # Config bootstrap wizard
├─ docs/
│  ├─ quickstart-windows.md
│  ├─ troubleshooting.md
│  └─ milestone-2-productization.md
├─ bridge.py                  # Local authenticated HTTP bridge
├─ config.example.json
├─ start.cmd / start-bridge.cmd / doctor.cmd / setup.cmd
├─ start-bridge-silent.vbs.example
├─ requirements.txt
├─ LICENSE
└─ README.md
```

## Limitations

- Baseline summarization/mind-map generation is heuristic (pluggable).
- No built-in idempotency/dedup by meeting ID yet (retries may duplicate appends).
- Recovery/retry for interrupted runs and upload failures is planned (M3); preserve local artifacts.
- Bridge depends on local Hex/FlowUs CLI behavior and availability.
- Extension cannot spawn the bridge process by itself.

## Security notes

- Controls local recording and writes to your FlowUs workspace.
- Enforced: token auth (>=32 chars), loopback-only binding + Host validation, strict Origin checks (incl. missing Origin rejection when configured), `Content-Type` and request/input validation, FlowUs ID/URL checks, meeting/artifact path confinement, sanitized `/state`.
- CLI sync validates target IDs and constrains manifest/export paths.
- Keep tokens/secrets and local IDs out of git; use only trusted local extension copies.

## Roadmap

- **M1 (`v0.2.0-milestone`):** core workflow, security hardening, E2E verified.
- **M2 (in progress):** onboarding/productization — doctor/setup, diagnostics UX, quickstart docs, stranger first-archive validation. See [docs/milestone-2-productization.md](docs/milestone-2-productization.md).
- **M3:** stability, recovery, idempotency, data/temp management, tests.
- **M4:** UX polish and release packaging.

## Contributors

- **[Finnlove](https://github.com/Finnlove)** — author
- **[OpenCode](https://opencode.ai/)** — contributor

## License

MIT (see [LICENSE](LICENSE)).
