# Milestone 2 — Productization: First Successful Archive by a New User

**Status:** Planned (post-`v0.2.0-milestone`, E2E verified on developer machine)  
**Primary goal:** A person who did not participate in development can independently install, configure, and complete their **first voice-note archive** to FlowUs on Windows.

**Non-goals for M2:** New AI features, rich UI redesign, cross-platform parity, packaging/auto-update (defer to later milestones). Focus on onboarding, clear diagnostics, and a reliable happy path.

**Guiding principle:** Local raw audio and transcript artifacts are the primary assets; FlowUs is the archive destination. (Preservation/retry is emphasized in M3, but M2 must not make failures opaque or destructive.)

---

## How to use this task list

- Tasks are ordered by priority and dependency. Execute top-to-bottom unless noted as parallelizable.
- Each task is scoped so it can be handed to an implementation agent as a single change set (with verification).
- After each task: run relevant checks, update docs, and do a quick manual smoke test where applicable.
- Keep security invariants from v0.2.x: localhost-only bridge, token auth, origin/host checks, input/path validation, sanitized `/state`.

**Suggested working conventions**
- Add scripts under `scripts/` (or `tools/`) and keep them idempotent.
- Prefer Python stdlib for tooling (repo already assumes Python). Use `.cmd` wrappers for Windows UX.
- Do not commit secrets, `config.local.json`, personal IDs, or local meeting data.

---

## Priority 1 — New-user installation & configuration

### M2-P1-T1: Preflight / doctor checks (dependency & environment validation)
**Priority:** P1 (blocker for smooth onboarding)  
**Depends on:** —  
**Parallelizable with:** T2 (doc drafting), but integrate outputs together

**Objective**  
Provide a single command that checks prerequisites and current configuration, and reports actionable fixes.

**Scope / files (expected)**
- Add: `scripts/doctor.py` (or `tools/doctor.py`) — stdlib-only
- Add: `doctor.cmd` (Windows wrapper: runs doctor with local Python)
- Update: `README.md` (Troubleshooting/Setup references)

**Implementation notes**
- Checks (report PASS/WARN/FAIL with clear next step):
  - OS hint (Windows expected), Python version >= 3.9
  - `hex` CLI discoverable: configured `hex_exe`, auto-detect under `%APPDATA%\hex\versions\*`, or on PATH
  - Hex meetings capability hint: presence of meetings dir/config if detectable; print reminder `meetings.enabled=true` (do not guess silently)
  - `flowus` CLI on PATH and `flowus whoami` succeeds (capture non-sensitive status only; do not print tokens)
  - `config.local.json` (or config) exists and parses; `bridge_token` set and length >= 32; `bridge_port` valid
  - `bridge_allowed_origin` format valid (`chrome-extension://<id>`); warn if placeholder
  - Port availability / bridge reachability: if `127.0.0.1:<port>` listening, optionally probe `/ping` with token+origin? (only if doctor can read config; treat auth failure as config mismatch with guidance)
  - Extension files present (`extension/manifest.json`, options/background/content)
- Exit code non-zero on FAIL; human-readable summary at end.
- Never echo secrets (mask token).

**Acceptance criteria**
- `python scripts/doctor.py` (or `doctor.cmd`) runs without stack traces on a clean misconfigured machine state and lists concrete fixes.
- On a correctly configured machine, it reports PASS for required items.
- README mentions doctor as the first diagnostic step.

---

### M2-P1-T2: Interactive first-run setup (config bootstrap)
**Priority:** P1  
**Depends on:** T1 (recommended; can share validation helpers)  
**Parallelizable with:** T3

**Objective**  
Generate/repair `config.local.json` safely with sensible defaults so users don’t hand-edit JSON as the primary path.

**Scope / files (expected)**
- Add: `scripts/setup.py` (interactive CLI wizard) and/or `setup.cmd`
- Update: `config.example.json` (ensure comments-equivalent clarity via README; JSON has no comments)
- Update: `README.md`

**Implementation notes**
- Flow (idempotent, non-destructive by default):
  1. Detect/create `config.local.json` (never overwrite without explicit confirmation; preserve unknown keys).
  2. Auto-detect `hex_exe` and suggest default; allow manual override/confirm.
  3. Generate cryptographically strong `bridge_token` (>=32 chars) if missing; display once with warning to copy into extension Options (and do not log).
  4. Prompt for extension ID (or full origin) and construct `bridge_allowed_origin`; validate format.
  5. Confirm `bridge_port` (default 27123) and warn on conflict.
  6. Set `sync_script` default appropriate to repo layout (`scripts/sync_to_flowus.py`).
  7. Optionally run doctor summary at end.
- Provide `--yes`/non-interactive mode for advanced users (only with explicit args/env) — keep safe defaults.
- If `config.json` exists, warn per current policy (prefer `config.local.json`).

**Acceptance criteria**
- On a machine with no local config, wizard creates a valid `config.local.json` that passes doctor’s config checks (except steps requiring user to load extension/set options in browser).
- Re-running does not clobber existing token/origin unless user confirms.
- Token is never written to git-tracked files.

---

### M2-P1-T3: Bridge startup UX & configuration diagnostics
**Priority:** P1  
**Depends on:** T2 (shared messages/config keys) helpful but not strict

**Objective**  
Make `bridge.py` failures self-explanatory for first-time users and align runtime behavior with setup tooling.

**Scope / files (expected)**
- Update: `bridge.py`
- Update: `start-bridge.cmd` (and keep `start-bridge-silent.vbs.example` consistent if needed)
- Update: `README.md` (brief notes)

**Implementation notes**
- On startup failure, print:
  - what is wrong (e.g. missing token, token too short, bad origin, port in use, config parse error with file+reason — already partly done)
  - exact fix (which file/field or which command: `python scripts/setup.py`, `python scripts/doctor.py`)
  - pointer to extension Options when token/origin mismatch is likely
- Detect port bind failure (`OSError` address in use) and suggest changing `bridge_port` (and remind to update extension Options + host permission implications if any).
- Consider a `--check`/`--doctor` flag or clear message directing to doctor (avoid duplicating logic heavily).
- Keep loopback-only and all security checks intact.

**Acceptance criteria**
- Misconfiguration scenarios (missing/short token, bad config JSON, port occupied) produce a single clear message + next action, no raw traceback as the primary UX.
- Successful start prints concise ready-state info (host/port) without leaking secrets.

---

### M2-P1-T4: Extension onboarding & configuration ergonomics
**Priority:** P1  
**Depends on:** T2/T3 for consistent terminology/values

**Objective**  
Reduce confusion around extension ID, token entry, port alignment, and “bridge not connected” states.

**Scope / files (expected)**
- Update: `extension/options.html`, `extension/options.js`
- Update: `extension/background.js`, `extension/content.js` (status messaging only, keep protocol compatible)
- Update: `README.md`

**Implementation notes**
- Options page improvements:
  - Inline validation (token length >= 32, port range), explicit “Saved” feedback, and a short checklist: (1) start bridge, (2) set same token/port, (3) set `bridge_allowed_origin` in `config.local.json` to this extension’s ID and restart bridge.
  - Show/copy helper: display `chrome.runtime.id`-based origin hint if available in extension context, to reduce manual errors.
  - Optional: “Test connection” button calling background `/ping` and surfacing categorized errors (unauthorized, forbidden origin, unreachable, timeout) mapped to fixes.
- Content/background UX: distinguish “extension not configured (token missing)” vs “bridge unreachable” vs “unauthorized/origin rejected” using existing error strings/sanitized states where possible.
- Do not weaken security (no token exposed to page/content context beyond what’s necessary; keep storage local).

**Acceptance criteria**
- A user can follow Options UI to understand all three coupling points (token, port, extension origin in bridge config).
- Connection test (if implemented) clearly differentiates wrong token vs wrong origin vs bridge down.

---

### M2-P1-T5: End-to-end “first archive” setup guide (README + quickstart)
**Priority:** P1  
**Depends on:** T1–T4 (document final flow)

**Objective**  
Rewrite onboarding docs so a stranger can go from download → first successful archive without reading source.

**Scope / files (expected)**
- Update: `README.md` (major restructure of Setup/Quickstart)
- Add (optional): `docs/quickstart-windows.md` for screenshots/step detail if README becomes too long
- Add: `docs/troubleshooting.md` (consolidate common failures)

**Implementation notes**
- Structure:
  1. **Prerequisites** with verification commands (`python --version`, `flowus whoami`, Hex installed/meetings enabled) and links/names (no secrets).
  2. **Install** (clone/download zip) + `python scripts/setup.py` + `doctor.cmd`.
  3. **Load extension** (unpacked), copy extension ID into setup/config, set token in Options.
  4. **Start bridge** and confirm ready state.
  5. **First archive**: open a specific FlowUs page you can edit → click button → record short test → stop → confirm page content (audio/transcript/summary blocks).
  6. **Daily use** and (optional) auto-start note.
- Troubleshooting table: symptom → likely cause → fix (include origin/token/port/PNA/private network, FlowUs auth, Hex meetings off, port in use).
- Explicitly state Windows scope and security model in plain language.

**Acceptance criteria**
- A fresh-user dry run script/checklist exists in docs and matches actual commands/filenames.
- **Validation (required before closing M2):** at least one person unfamiliar with the codebase completes first archive using only docs; capture feedback/issues as follow-ups.

---

### M2-P1-T6: One-command local launcher (convenience, Windows)
**Priority:** P1 (nice-to-have within P1; can follow T3)  
**Depends on:** T1, T3

**Objective**  
Reduce “how do I start this?” friction after configuration is complete.

**Scope / files (expected)**
- Add/Update: `start.cmd` or enhance `start-bridge.cmd` to optionally run doctor/preflight then start bridge
- Update: `README.md`

**Implementation notes**
- Default behavior: quick config sanity check → start bridge; clear message if not configured (point to setup).
- Do not auto-install system dependencies silently; only check and guide.
- Keep silent/startup variant documented (existing `.vbs.example`).

**Acceptance criteria**
- Double-click/one-command path works for a configured environment and fails gracefully with guidance when not configured.

---

## Priority 2 — Stability & recovery (plan M3, prepare hooks in M2)

> M2 focuses on onboarding; do not expand scope into full recovery implementation except where required to avoid obvious data loss messaging. Track these as M3 tasks.

### M3 candidates (to refine after M2)
- **M3-T1:** Preserve local audio/transcript as source of truth on upload failure; surface retry path (CLI/bridge) without re-recording.
- **M3-T2:** Detect and represent abnormal states (browser closed mid-recording, bridge exit, stale “recording/processing” state) with recovery guidance.
- **M3-T3:** Idempotency/dedup by meeting ID; prevent duplicate appends on retry/double-click.
- **M3-T4:** Network/FlowUs CLI failure handling with non-destructive retries and clear status.
- **M3-T5:** Reconcile Hex meeting completion vs bridge state after restart.

**M2 handoff requirement:** In troubleshooting/docs, explicitly warn users where data is stored locally and that, if upload fails, they should retain local artifacts until M3 retry tooling lands.

---

## Priority 3 — Structure & data management (M3/M4 prep)

### M3/M4 candidates
- **M3-T6:** Use per-run temp directories / unique filenames for JSON payloads and summaries (avoid fixed-name races).
- **M3-T7:** Temp/sensitive artifact lifecycle (cleanup policy, opt-in retention, avoid leaking paths in UI — bridge already sanitizes state).
- **M3-T8:** Config schema/versioning, defaults, migration, and unified port propagation guidance/checks.
- **M3-T9:** Extension message sender/origin hardening review (defense-in-depth) and permission minimization audit.
- **M3-T10:** Automated tests for core pure logic (validators, config parsing, path confinement, block building) + smoke/integration harness where feasible.

---

## Priority 4 — User experience & release (M4)

- Status visualization (recording/transcribing/uploading) and progress detail.
- Retry failed uploads/jobs from UI/CLI.
- History/search of processed meetings.
- Configurable summary/todo/mindmap templates.
- Installer/packaging and update mechanism.

---

## Suggested execution order (M2 sprint)

1. **M2-P1-T1** Doctor checks (foundation for all other UX)
2. **M2-P1-T2** Setup wizard (generates correct config)
3. **M2-P1-T3** Bridge startup/config diagnostics
4. **M2-P1-T4** Extension onboarding + connection test UX
5. **M2-P1-T6** One-command launcher (small, after core flow is clear)
6. **M2-P1-T5** README/quickstart/troubleshooting rewrite + **stranger install validation**

*Parallelizable:* parts of T4 UI copy and T5 docs can proceed alongside T3 once config keys/terms stabilize.

---

## Definition of Done for Milestone 2

- [ ] `doctor` and `setup` exist, are documented, and pass on a correctly configured dev machine.
- [ ] Bridge/extension errors guide users to specific fixes (token/origin/port/dependency).
- [ ] README provides a complete Windows quickstart to first archive.
- [ ] A non-developer tester completes **first archive** using docs (+ tools) without reading source; issues filed for gaps.
- [ ] No regression of v0.2.x security controls; no secrets committed.

---

## Risks / notes for implementers

- **Extension ID is assigned by Chrome/Edge:** tooling can only assist (detect/show instructions) after unpacked load; config/origin alignment must be an explicit guided step.
- **FlowUs/Hex are external dependencies:** doctor should check presence/auth signals but avoid brittle assumptions about their internal output formats.
- **Keep repo sanitized:** examples must use placeholders; never embed real page/database/meeting IDs, tokens, or local paths.
- **Local working copy divergence:** this repo (`record2flowus`) is the sanitized source of truth; if a separate local runtime copy exists, port M2 changes deliberately and re-verify.
