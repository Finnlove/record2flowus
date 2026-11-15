# Hex → FlowUs Voice Note Workflow

A local workflow that turns **Hex** meeting recordings (audio + local ASR transcript) into structured, beautified **FlowUs (息流)** voice-note pages.

It supports:
- **One-click browser button mode**: injects a button on FlowUs pages to capture the current page ID, start/stop Hex meeting recording, and append the processed result back to that page.
- **CLI mode**: sync a completed Hex meeting to a target FlowUs page/database.
- **Auto formatting**: generates a beautified FlowUs-native layout (callouts, columns, toggles, to-dos, file block, optional mind map).
- **Local-first transcription**: uses Hex local meeting artifacts (`.wav` + transcript) so the core capture/transcription step can run without paid cloud transcription.

> This repository contains sanitized/reference implementation code. Configure local paths, IDs, and credentials in your own environment.

## How it works

1. **Trigger**
   - Browser extension button on a FlowUs page (captures current page ID/URL/title), or
   - Manual CLI invocation.
2. **Record & transcribe (Hex)**
   - Starts/stops Hex meeting recording (`hex meetings record/stop`).
   - Waits for completed artifacts: `microphone.wav`, `transcript.md` / `transcript.ndjson`, `manifest.json`, and exported transcript (if available).
3. **Process**
   - Reads transcript and generates a summary/points/action items (heuristic baseline included; pluggable for higher-quality model generation).
   - Optionally generates a FlowUs mind map structure.
4. **Publish to FlowUs**
   - Uploads audio via FlowUs CLI and appends a file block.
   - Appends beautified blocks (overview, summary, key points, todos, collapsible transcript, assets).
   - Optionally creates and links a native FlowUs mind map.

## Repository structure

```text
.
├─ extension/                 # Browser extension (MV3) for FlowUs button UI
│  ├─ manifest.json
│  ├─ background.js
│  ├─ content.js
│  ├─ content.css
│  ├─ options.html            # Extension settings (bridge token/port)
│  └─ options.js
├─ scripts/
│  └─ sync_to_flowus.py       # Sync Hex meeting artifacts to FlowUs (beautified)
├─ bridge.py                  # Local HTTP bridge: extension ↔ Hex ↔ sync
├─ config.example.json        # Configuration template
├─ start-bridge.cmd           # Start bridge (foreground)
├─ start-bridge-silent.vbs.example
├─ requirements.txt
├─ LICENSE
└─ README.md
```

## Prerequisites

- **OS**: Windows (reference implementation; Hex paths/CLI behavior are Windows-oriented)
- **Python**: 3.9+
- **Hex**: installed locally, with:
  - microphone/model configured
  - meetings enabled (`meetings.enabled = true` in Hex settings)
  - `hex` CLI available (or provide full path to `hex.exe`)
- **FlowUs CLI**: installed and authenticated to your workspace (`flowus whoami` works, write access available)
- **Browser**: Chrome/Edge (for loading unpacked MV3 extension)

## Configuration

1. Copy `config.example.json` to `config.local.json` (**recommended**, gitignored). `config.json` is also supported but gitignored to reduce accidental commits — prefer `config.local.json` for personal settings.
2. Adjust values:

```json
{
  "hex_exe": "C:\\Users\\<USER>\\AppData\\Roaming\\hex\\versions\\<VERSION>\\hex.exe",
  "hex_meetings_dir": "",
  "hex_export_dir": "",
  "bridge_port": 27123,
  "sync_script": "scripts/sync_to_flowus.py",
  "bridge_token": "REPLACE_WITH_A_RANDOM_TOKEN_AT_LEAST_32_CHARS",
  "bridge_allowed_origin": "chrome-extension://REPLACE_WITH_YOUR_EXTENSION_ID"
}
```

Notes:
- **`bridge_token` (required):** random secret used to authenticate all bridge API calls. Must be at least 32 characters (high-entropy, randomly generated). Keep it private and set the same value in the browser extension options.
- **`bridge_allowed_origin` (required for browser use):** the extension origin in the form `chrome-extension://<EXTENSION_ID>`. Find the extension ID on `chrome://extensions` after loading it. When configured, requests with missing or mismatched `Origin` are rejected; browser extension calls must match this origin.
- **Security defaults:** the bridge binds only to `127.0.0.1`, validates `Host` and `Origin`, requires `Authorization: Bearer <token>` and `Content-Type: application/json` for API requests, enforces request size/format limits, validates FlowUs page IDs/URLs and meeting/artifact paths, and returns a sanitized `/state`.
- **Config safety:** invalid/unreadable `config.json` or `config.local.json` will stop startup with an explicit error (no silent fallback).
- If `hex_exe` is empty, `bridge.py` attempts auto-detection under `%APPDATA%\hex\versions\*` and falls back to `hex`.
- Environment variables can override config: `HEX_EXE`, `HEX_MEETINGS_DIR`, `HEX_EXPORT_DIR`, `SYNC_SCRIPT`, `BRIDGE_PORT`, `BRIDGE_TOKEN`, `BRIDGE_ALLOWED_ORIGIN`.
- Do **not** commit `config.json`, `config.local.json`, tokens, cookies, or personal workspace IDs.

## Setup

> **First diagnostic:** run `doctor.cmd` (or `python scripts/doctor.py`) to check dependencies and configuration. It reports actionable fixes without printing secrets.

### 1) Start the local bridge

```powershell
python bridge.py
# or
.\start-bridge.cmd
```

The bridge listens on `http://127.0.0.1:27123` by default and exposes authenticated endpoints: `/ping`, `/state`, `/start`, `/stop`, `/reset`. Requests require `Authorization: Bearer <bridge_token>`, valid `Host`, and (for browser calls) the configured allowed extension origin.

### 2) Load the browser extension

1. Open `chrome://extensions` (or Edge extensions page) and enable **Developer mode**.
2. Click **Load unpacked** and select the `extension/` folder.
3. Copy the extension ID and set `bridge_allowed_origin` in `config.local.json` to `chrome-extension://<EXTENSION_ID>`, then restart `bridge.py`.
4. Open the extension **Options** page and set the same `bridge_token` (and port if changed) used by `bridge.py`. Settings are stored in local extension storage.
5. Ensure site access is allowed for FlowUs pages.

### 3) (Optional) Run bridge at login

- Copy `start-bridge-silent.vbs.example` to `start-bridge-silent.vbs`, adjust if needed, and place it in your Startup folder (e.g. `shell:startup`).
- This avoids manually starting the bridge each session. (A web extension cannot directly spawn local OS processes.)

## Usage

### Button mode (recommended)

1. Open a FlowUs page you can edit.
2. Click the floating **“🎙️ 语音笔记”** button to start recording (binds the current page ID).
3. Click again to stop: the bridge waits for Hex transcript completion, processes content, and appends the beautified note (audio + transcript + summary + optional mind map) back to the **current page**.
4. Button/status shows progress: recording → processing → done/error.

### CLI mode

Append a completed meeting to an existing FlowUs page:

```powershell
python scripts/sync_to_flowus.py --page-id <FLOWUS_PAGE_ID>
```

Create a new page/record:

```powershell
python scripts/sync_to_flowus.py --parent-page-id <PARENT_PAGE_ID>
python scripts/sync_to_flowus.py --database-id <DATABASE_ID>
```

Useful options:

```text
--meeting-id <id>         Sync a specific Hex meeting (default: latest complete)
--meetings-dir <path>     Hex meetings directory
--summary-file <path>     Provide pre-generated summary markdown/text
--mindmap-json <path>     Provide FlowUs mind map create JSON body
--no-mindmap              Skip mind map creation
--plain                   Disable beautified block layout
--title <text>            Title when creating a new page
```

## Data & privacy notes (de-sensitization)

- Audio/transcripts may contain sensitive information. Review before uploading to FlowUs.
- Keep credentials and personal workspace data out of git:
  - FlowUs auth is handled by your local FlowUs CLI login/config (not stored in this repo).
  - Hex local API tokens, meeting contents, exports, and IDs are local artifacts—do not commit them.
  - Use `config.local.json` (gitignored) for machine-specific paths/IDs.
- This repo intentionally avoids embedding personal absolute paths, private page/database IDs, meeting IDs, or workspace URLs.

## Limitations

- Baseline summarization/mind-map generation in `bridge.py` is heuristic; integrate a stronger model/agent step for higher quality output.
- No built-in idempotency/deduplication by meeting ID yet (re-running may append duplicates).
- Bridge relies on local Hex CLI behavior and FlowUs CLI/API availability.
- Browser extension cannot start the local bridge by itself; use auto-start or a Native Messaging host for on-demand launch.

## Security notes

This project controls local recording and writes to your FlowUs workspace. To reduce risk, the bridge enforces token authentication (min 32 chars), loopback-only binding/host checks, strict extension-origin checks (including missing Origin rejection when configured), strict input validation (Content-Type/body size/format, FlowUs page ID/URL, meeting ID and artifact path confinement), and sanitized status responses. `scripts/sync_to_flowus.py` also validates target IDs and constrains manifest/export paths when run directly from the CLI. Keep your `bridge_token` secret, do not expose the bridge beyond localhost, and only load the extension from a trusted local copy.

## Troubleshooting

- **Start with diagnostics**: run `doctor.cmd` (or `python scripts/doctor.py`) to identify missing dependencies or configuration mismatches.
- **Button not visible**: reload the extension, hard-refresh the FlowUs page, check extension errors/site access, and confirm you are on a FlowUs page URL.
- **Bridge not connected / unauthorized**: ensure `bridge.py` is running, `bridge_token` matches extension options, `bridge_allowed_origin` matches the extension ID, and host/port are correct.
- **Recording fails**: verify Hex is installed/configured and `meetings.enabled` is true.
- **Upload/append fails**: verify `flowus whoami`/permissions and that the target page is editable.
- **Processing fails**: check the local bridge console/log output for details (the UI shows sanitized status).

## License

MIT (see [LICENSE](LICENSE)).
