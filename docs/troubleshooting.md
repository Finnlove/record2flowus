# Troubleshooting

Start here:
- `doctor.cmd` (or `python scripts/doctor.py`) — dependency/config diagnostics
- `python bridge.py --check` — bridge config/startup preflight
- Extension **Options → Test connection** — auth/origin/port/reachability from the browser side
- Bridge console output — detailed processing/upload errors (UI status is sanitized)

---

## Common issues

| Symptom | Likely cause | What to do |
|---|---|---|
| `setup.cmd`/`doctor.cmd`/`start.cmd` says Python not found | Python not installed or not on PATH | Install Python 3.9+, ensure `python` works in a new terminal, re-run. |
| `config.local.json` parse/load error on startup | Invalid JSON or wrong structure | Fix JSON (doctor/setup will point to the file), or run `setup.cmd` to rebuild; keep a backup if needed. |
| Bridge exits: `bridge_token is not configured` or too short | Missing/placeholder/weak token | Run `setup.cmd` to generate a token (>=32 chars). Save same token in extension Options and `config.local.json`, restart bridge. |
| Extension shows “未配置 bridge token…” | Token not saved in extension storage | Open extension Options, paste the same token as `config.local.json`, Save, then retry (reload FlowUs page if needed). |
| “无法连接本地桥接…” / unreachable | Bridge not running, wrong port, or local connection blocked | Start with `start.cmd`; confirm port matches Options and `config.local.json`; ensure `127.0.0.1:<port>` is listening; check firewall/proxy rules for localhost. |
| “认证失败（Token 无效或不匹配）” / 401 | Token differs between extension and bridge config | Make tokens identical in extension Options and `config.local.json`, restart bridge. |
| “Origin 被拒绝…” / “桥接未配置允许的扩展 Origin” / 403 | `bridge_allowed_origin` missing/mismatch or extension ID changed | Copy **Extension Origin** from Options (`chrome-extension://<ID>`), set it in `config.local.json`, restart bridge. Re-check after reloading/reinstalling unpacked extension. |
| Port already in use (startup error) | Another process bound to `bridge_port` | Stop the conflicting process or choose a new port in `config.local.json` **and** extension Options, then restart bridge. |
| Button not visible on FlowUs page | Extension not loaded/enabled, wrong site, or page not ready | Reload extension, refresh page, confirm you’re on a FlowUs page URL and have site access; check extension errors in `chrome://extensions`. |
| “未识别到当前 FlowUs 页面 ID…” | Not on a supported FlowUs page route | Open the specific FlowUs page (or record) you want to archive and try again. |
| Recording/start fails | Hex not installed/configured, meetings disabled, wrong `hex_exe` | Verify Hex works, enable meetings (`meetings.enabled=true`), set correct `hex_exe` or let setup/doctor detect it. |
| Processing/upload fails | FlowUs CLI not authenticated, no permission, target page issues, network | Check bridge console for details; run `flowus --json whoami`; ensure target page is editable and reachable. |
| Repeated/duplicate archives | No idempotency/dedup yet (known limitation) | Avoid repeated stop/retry clicks for the same run; track duplicates manually until M3 recovery/idempotency lands. |
| Bridge restarted / browser closed during run | State may be interrupted (known limitation) | Check local Hex meeting artifacts and FlowUs page; re-run carefully (may duplicate) — see M3 plans. |

---

## Data & recovery notes (important)

- **Primary assets:** Hex local recordings/transcripts/manifests (e.g. Hex meetings/export directories). Keep them until you confirm the FlowUs page contains the expected archive.
- **Archive destination:** FlowUs page/database content created/appended by this workflow.
- If upload/append fails, do **not** delete local artifacts. Preserve them for manual recovery or future retry tooling.
- Temporary JSON payloads/summaries may be written under the system temp directory during processing.

---

## Security reminders

- Bridge binds to `127.0.0.1` only and requires token + strict request validation; do not expose it to LAN/internet.
- Keep `bridge_token` secret and out of git. Set the same token in extension Options and `config.local.json`.
- `bridge_allowed_origin` must match the loaded extension’s origin; restart bridge after config changes.

---

## Collecting info for a bug report

Include (sanitized, no secrets/tokens/personal IDs):
- OS, Python version, Hex version/CLI behavior (summary), FlowUs CLI status (`whoami` success/fail without sensitive data)
- `doctor.cmd` output (redact if needed)
- Extension Options “Test connection” result
- Bridge console log around the failure
- Steps to reproduce and expected vs actual behavior
