#!/usr/bin/env python3
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_NAMES = ("config.json", "config.local.json")
ORIGIN_RE = re.compile(r"^chrome-extension://[a-p]{32}$")
PLACEHOLDER_TOKEN = "REPLACE_WITH_A_RANDOM_TOKEN_AT_LEAST_32_CHARS"
PLACEHOLDER_ORIGIN = "chrome-extension://REPLACE_WITH_YOUR_EXTENSION_ID"


class Result:
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"

    def __init__(self):
        self.items = []

    def add(self, status, name, message, fix=""):
        self.items.append({"status": status, "name": name, "message": message, "fix": fix})

    @property
    def exit_code(self):
        return 1 if any(i["status"] == self.FAIL for i in self.items) else 0


def mask_secret(value):
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "..." + value[-4:]


def load_config():
    merged = {}
    sources = []
    errors = []
    for name in CONFIG_NAMES:
        p = ROOT / name
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data is None:
                data = {}
            if not isinstance(data, dict):
                raise ValueError("top-level JSON value must be an object")
            merged.update(data)
            sources.append(str(p))
        except Exception as e:
            errors.append(f"{p}: {e}")
    return merged, sources, errors


def run_cmd(cmd, timeout=10):
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        out = ((r.stdout or "") + ("\n" + r.stderr if r.stderr else "")).strip()
        return r.returncode, out
    except FileNotFoundError:
        return 127, "command not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except Exception as e:
        return 1, str(e)


def detect_hex_exe(cfg):
    configured = str(cfg.get("hex_exe") or "").strip()
    if configured:
        return configured, "configured"
    appdata = os.environ.get("APPDATA", "")
    versions_dir = Path(appdata) / "hex" / "versions" if appdata else None
    if versions_dir and versions_dir.is_dir():
        try:
            for d in sorted([p for p in versions_dir.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True):
                exe = d / "hex.exe"
                if exe.is_file():
                    return str(exe), "auto-detected"
        except Exception:
            pass
    which = shutil.which("hex")
    if which:
        return which, "PATH"
    return "", "not found"


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def probe_bridge(port, token, origin):
    if not token or token == PLACEHOLDER_TOKEN:
        return None, "skipped (token not configured)"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/ping",
        headers={"Authorization": f"Bearer {token}", "Origin": origin or ""},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            body = r.read().decode("utf-8", errors="replace")
            return r.status, body
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, body
    except Exception as e:
        return None, str(e)


def main():
    res = Result()
    cfg, sources, cfg_errors = load_config()

    # OS / Python
    py_ok = sys.version_info >= (3, 9)
    res.add(
        Result.PASS if py_ok else Result.FAIL,
        "python",
        f"Python {platform.python_version()} on {platform.system()} {platform.release()}",
        "" if py_ok else "Install Python 3.9+ and ensure `python` is available in PATH.",
    )
    if platform.system().lower() != "windows":
        res.add(Result.WARN, "os", f"Detected {platform.system()}; this workflow targets Windows.", "Use Windows for full Hex/extension support.")

    # Config load
    if cfg_errors:
        for err in cfg_errors:
            res.add(Result.FAIL, "config.parse", f"Failed to load config: {err}", "Fix JSON syntax/structure in the reported file, then re-run doctor.")
    if sources:
        res.add(Result.PASS, "config.files", f"Loaded config from: {', '.join(sources)}")
    else:
        res.add(
            Result.FAIL,
            "config.files",
            "No config.local.json/config.json found.",
            "Run: python scripts/setup.py (or copy config.example.json to config.local.json and edit values).",
        )

    token = str(cfg.get("bridge_token") or "").strip()
    origin = str(cfg.get("bridge_allowed_origin") or "").strip()
    port = cfg.get("bridge_port", 27123)
    try:
        port = int(port)
        if not (1 <= port <= 65535):
            raise ValueError
    except Exception:
        res.add(Result.FAIL, "config.bridge_port", f"Invalid bridge_port: {cfg.get('bridge_port')}", "Set bridge_port to an integer between 1 and 65535 in config.local.json.")
        port = 27123

    # Token
    if not token or token == PLACEHOLDER_TOKEN:
        res.add(Result.FAIL, "config.bridge_token", "bridge_token is missing or still placeholder.", "Generate a random token (>=32 chars), set it in config.local.json and in the extension Options page.")
    elif len(token) < 32:
        res.add(Result.FAIL, "config.bridge_token", f"bridge_token is too short (length {len(token)}).", "Use a high-entropy random token with at least 32 characters.")
    else:
        res.add(Result.PASS, "config.bridge_token", f"bridge_token configured ({mask_secret(token)}).", "Ensure the same token is saved in the browser extension Options.")

    # Origin
    if not origin or origin == PLACEHOLDER_ORIGIN:
        res.add(Result.FAIL, "config.bridge_allowed_origin", "bridge_allowed_origin is missing or placeholder.", "Load unpacked extension in chrome://extensions, copy its ID, set bridge_allowed_origin to chrome-extension://<ID>, then restart bridge.")
    elif ORIGIN_RE.match(origin):
        res.add(Result.PASS, "config.bridge_allowed_origin", f"bridge_allowed_origin looks valid ({origin}).", "Confirm it matches the actual loaded extension ID.")
    else:
        res.add(Result.FAIL, "config.bridge_allowed_origin", f"bridge_allowed_origin format is invalid: {origin}", "Use format chrome-extension://<32 lowercase letters a-p>.")

    # Hex
    hex_exe, hex_src = detect_hex_exe(cfg)
    if hex_exe and Path(hex_exe).exists():
        res.add(Result.PASS, "hex.exe", f"Hex executable found ({hex_src}): {hex_exe}")
        rc, out = run_cmd([hex_exe, "meetings", "list", "--json"], timeout=8)
        if rc == 0:
            res.add(Result.PASS, "hex.cli", "Hex CLI responded to `hex meetings list --json`.")
        else:
            hint = "Ensure Hex is installed correctly and meetings are enabled (meetings.enabled=true in Hex settings)."
            if "meeting" in out.lower() or "not enabled" in out.lower() or "disabled" in out.lower():
                hint = "Enable meetings in Hex settings (meetings.enabled=true), then retry."
            res.add(Result.WARN, "hex.cli", f"Hex CLI check returned code {rc}. {out[:200]}".strip(), hint)
    elif hex_exe:
        res.add(Result.FAIL, "hex.exe", f"Configured hex_exe not found: {hex_exe}", "Update hex_exe in config.local.json or install Hex.")
    else:
        res.add(Result.FAIL, "hex.exe", "Hex executable not found.", "Install Hex, or set hex_exe in config.local.json to the full path of hex.exe.")

    meetings_dir = str(cfg.get("hex_meetings_dir") or "").strip() or os.path.join(os.environ.get("APPDATA", ""), "hex", "meetings")
    if meetings_dir and Path(meetings_dir).is_dir():
        res.add(Result.PASS, "hex.meetings_dir", f"Meetings directory exists: {meetings_dir}")
    else:
        res.add(Result.WARN, "hex.meetings_dir", f"Meetings directory not found: {meetings_dir}", "After enabling Hex meetings and recording once, this directory should appear.")

    # FlowUs CLI
    flowus_path = shutil.which("flowus")
    if flowus_path:
        res.add(Result.PASS, "flowus.cli", f"FlowUs CLI found: {flowus_path}")
        rc, out = run_cmd(["flowus", "--json", "whoami"], timeout=12)
        if rc == 0:
            res.add(Result.PASS, "flowus.auth", "FlowUs CLI authenticated (`whoami` OK).")
        else:
            safe = out[:200].replace(token, mask_secret(token)) if token else out[:200]
            res.add(Result.FAIL, "flowus.auth", f"FlowUs CLI not ready/authenticated (code {rc}). {safe}".strip(), "Run FlowUs CLI login/auth flow (e.g. `flowus login`/`flowus whoami`) until whoami succeeds.")
    else:
        res.add(Result.FAIL, "flowus.cli", "FlowUs CLI (`flowus`) not found in PATH.", "Install FlowUs CLI and authenticate, then re-run doctor.")

    # Extension files
    required_ext = ["manifest.json", "background.js", "content.js", "options.html", "options.js"]
    missing_ext = [f for f in required_ext if not (ROOT / "extension" / f).exists()]
    if not missing_ext:
        res.add(Result.PASS, "extension.files", "Required extension files are present in ./extension.")
    else:
        res.add(Result.FAIL, "extension.files", f"Missing extension files: {', '.join(missing_ext)}", "Restore missing files from the repository.")

    # Port / bridge
    if port_in_use(port):
        status_code, body = probe_bridge(port, token if ORIGIN_RE.match(origin) or origin == PLACEHOLDER_ORIGIN else token, origin if ORIGIN_RE.match(origin) else "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        if status_code == 200:
            res.add(Result.PASS, "bridge", f"Bridge reachable on 127.0.0.1:{port} and /ping succeeded.")
        elif status_code in (401, 403):
            res.add(Result.WARN, "bridge", f"Port {port} is in use and bridge responded HTTP {status_code}.", "Check that bridge_token and bridge_allowed_origin in config.local.json match the extension Options and restart bridge.")
        elif status_code is None:
            res.add(Result.WARN, "bridge", f"Port {port} is in use but probe failed: {body}", "Verify the process on this port is the local bridge and restart it if needed.")
        else:
            res.add(Result.WARN, "bridge", f"Port {port} responded HTTP {status_code}.", "Verify bridge is running with current config.")
    else:
        res.add(Result.WARN, "bridge", f"Bridge not listening on 127.0.0.1:{port}.", "Start it with: python bridge.py (or start-bridge.cmd) after configuration is complete.")

    # Summary
    counts = {Result.PASS: 0, Result.WARN: 0, Result.FAIL: 0}
    icons = {Result.PASS: "[PASS]", Result.WARN: "[WARN]", Result.FAIL: "[FAIL]"}
    for item in res.items:
        counts[item["status"]] += 1
    print("\nrecord2flowus doctor\n" + "=" * 22)
    for item in res.items:
        print(f"{icons[item['status']]} {item['name']}: {item['message']}")
        if item["fix"]:
            print(f"       fix: {item['fix']}")
    print("-" * 22)
    print(f"Summary: PASS={counts[Result.PASS]} WARN={counts[Result.WARN]} FAIL={counts[Result.FAIL]}")
    if res.exit_code != 0:
        print("Result: issues found. Address FAIL items above, then re-run doctor.")
    else:
        print("Result: required checks passed. Complete browser extension setup and do a short first-archive test.")
    return res.exit_code


if __name__ == "__main__":
    sys.exit(main())