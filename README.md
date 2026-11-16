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
  "bridge_allowed_origin": "chrome-extension://<EXTENSION_ID>",
  "summary_mode": "rules",
  "ollama_url": "http://127.0.0.1:11434",
  "ollama_model": "qwen2.5:3b",
  "ollama_timeout": 180,
  "archive_artifacts_dir": ""
}
```
- **`bridge_token` (required):** high-entropy random secret (>= 32 chars). Must match the extension Options value.
- **`bridge_allowed_origin` (required for browser use):** must match the loaded extension origin; restart bridge after changes.
- **`summary_mode` (optional):** `rules` (default, heuristic) or `ollama` (local LLM via Ollama). When `ollama` is selected, the bridge calls `ollama_url`/`ollama_model` and **falls back to rules on failure**. Two things to know:
  - **Model sizing:** `qwen2.5:3b` is the recommended default (~45 s per meeting on a 8 GB machine). `qwen2.5:7b` needs **≥16 GB RAM**; on 8 GB it swaps to pagefile and times out (measured 215 s → fallback).
  - **Fallback is visible:** when the model is unavailable the generated page starts with a `⚠️ 本次摘要由规则模式生成` banner naming the reason, so a degraded archive is never mistaken for a code bug. The bridge also tries to start a local `ollama serve` when it starts serving (skipped when `summary_mode=rules`).
- **`archive_artifacts_dir` (optional):** persistent directory for generated summary/mindmap artifacts and retry context (default: `%APPDATA%/hex-flowus-voice-note/artifacts`).
- **`autostart` (optional, default `true`):** on Windows, when the bridge starts serving it installs/refreshes a `record2flowus-bridge.vbs` launcher in the user Startup folder so the bridge comes back automatically after reboot. Set `false` (or env `BRIDGE_AUTOSTART=0`) to opt out; `--check` reports the current state.
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
Authenticated endpoints: `/ping`, `/state`, `/start`, `/stop`, `/retry`, `/archives`, `/reset` (loopback only). Keep it running during use.

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

## Recovery & retry

- Failed/uncertain archives are recorded in a local SQLite ledger (`%APPDATA%/hex-flowus-voice-note/archive_ledger.sqlite3`) with step/status and preserved generated artifacts for retry.
- **Extension:** when an archive fails and is retryable, the button shows a retry state — click to re-run the archive. If a write was uncertain, review the target page before retrying.
- **Bridge API:** `POST /retry` (authenticated) retries the latest/current retryable archive (or a specified `archive_id`). Add `"regenerate": true` to the body to **regenerate the summary/mindmap from the transcript** instead of reusing cached artifacts (use this after changing `summary_mode` or summary logic); the uploaded audio is still reused.
- **CLI:** `python scripts/retry_archive.py --list` / `python scripts/retry_archive.py [--archive-id ...]` (or `--call-bridge`).
- Source Hex artifacts remain in the Hex meetings directory; generated summaries/mindmaps are kept in the archive artifacts directory until you choose to clean them.

## Troubleshooting

- Run `doctor.cmd` and `python bridge.py --check`.
- Use extension Options **Test connection**.
- See **[docs/troubleshooting.md](docs/troubleshooting.md)** for symptoms, causes, and fixes.

**Data safety note:** local Hex audio/transcripts are primary assets. Retries are best-effort and duplicate side effects (e.g. re-uploads) are minimized where possible, but uncertain partial writes may still require manual review.

---

## Repository structure

```text
.
├─ extension/                 # Browser extension (MV3) + Options/Test connection
├─ scripts/
│  ├─ sync_to_flowus.py       # Hex artifacts → FlowUs (beautified)
│  ├─ archive_store.py        # Local archive ledger (idempotency/retry state)
│  ├─ summarizer.py           # Summary modes (rules / Ollama) with fallback
│  ├─ retry_archive.py        # Retry failed/needs-review archives
│  ├─ doctor.py               # Preflight diagnostics
│  └─ setup.py                # Config bootstrap wizard
├─ docs/
│  ├─ quickstart-windows.md
│  ├─ troubleshooting.md
│  └─ milestone-2-productization.md
├─ tests/                     # Text-processing regression tests (unittest)
│  ├─ __init__.py
│  └─ test_bridge_text.py
├─ AGENTS.md                  # Agent-first usage and safety/specification
├─ bridge.py                  # Local authenticated HTTP bridge
├─ config.example.json
├─ start.cmd / start-bridge.cmd / doctor.cmd / setup.cmd
├─ start-bridge-silent.vbs.example
├─ requirements.txt
├─ LICENSE
└─ README.md
```

## Development

Run text-processing regression tests (stdlib `unittest`, no third-party deps, no real tokens/IDs/audio):

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

## Limitations

- Summarization/mind-map generation is heuristic by default; optional local Ollama mode is supported (`summary_mode=ollama`) with automatic fallback to rules.
- Idempotency/dedup is **best-effort**: archives are keyed by meeting+target page in a local ledger and verified via a remote archive marker before writes (skip if already present). FlowUs has no server-side idempotency keys and "update existing blocks" is not supported, so uncertain partial writes may still require manual review.
- Recovery/retry is supported (ledger + preserved artifacts + `/retry`/CLI/extension), but fine-grained resume of every remote side effect is not guaranteed.
- Bridge depends on local Hex/FlowUs CLI behavior and availability.
- Extension cannot spawn the bridge process by itself.

## Security notes

- Controls local recording and writes to your FlowUs workspace.
- Enforced: token auth (>=32 chars), loopback-only binding + Host validation, Origin checks (**mismatching Origin rejected; requests without `Origin` are allowed** — Chrome 157+ extension service-worker fetches carry no `Origin`, so identity is enforced by the Bearer token), `Content-Type` and request/input validation, FlowUs ID/URL checks, meeting/artifact path confinement, sanitized `/state`.
- CLI sync validates target IDs and constrains manifest/export paths.
- Keep tokens/secrets and local IDs out of git; use only trusted local extension copies.

## Roadmap

- **M1 (`v0.2.0-milestone`):** core workflow, security hardening, E2E verified.
- **M2 (in progress):** onboarding/productization — doctor/setup, diagnostics UX, quickstart docs, stranger first-archive validation. See [docs/milestone-2-productization.md](docs/milestone-2-productization.md).
- **M3 (in progress):** best-effort idempotency/dedup, recovery/retry, artifact retention, summary mode pluggability, tests (further hardening ongoing).
- **M4:** UX polish and release packaging.

## Contributors

- **[Finnlove](https://github.com/Finnlove)** — author
- **[OpenCode](https://opencode.ai/)** — contributor

## License

MIT (see [LICENSE](LICENSE)).
