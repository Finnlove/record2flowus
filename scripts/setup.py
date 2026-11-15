#!/usr/bin/env python3
import argparse
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_LOCAL = ROOT / "config.local.json"
CONFIG_JSON = ROOT / "config.json"
EXAMPLE = ROOT / "config.example.json"
ORIGIN_RE = re.compile(r"^chrome-extension://[a-p]{32}$")
EXT_ID_RE = re.compile(r"^[a-p]{32}$")
PLACEHOLDERS = {
    "bridge_token": {"", "REPLACE_WITH_A_RANDOM_TOKEN_AT_LEAST_32_CHARS"},
    "bridge_allowed_origin": {"", "chrome-extension://REPLACE_WITH_YOUR_EXTENSION_ID"},
    "hex_exe": {"", r"C:\Users\<USER>\AppData\Roaming\hex\versions\<VERSION>\hex.exe"},
}


def eprint(msg):
    print(msg, file=sys.stderr)


def load_json_file(path):
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return None, f"top-level JSON value must be an object: {path}"
        return data, None
    except Exception as e:
        return None, f"failed to parse {path}: {e}"


def save_json_file(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def detect_hex_exe(current=""):
    cur = str(current or "").strip()
    if cur and cur not in PLACEHOLDERS["hex_exe"] and Path(cur).exists():
        return cur
    appdata = os.environ.get("APPDATA", "")
    versions_dir = Path(appdata) / "hex" / "versions" if appdata else None
    if versions_dir and versions_dir.is_dir():
        try:
            for d in sorted([p for p in versions_dir.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True):
                exe = d / "hex.exe"
                if exe.is_file():
                    return str(exe)
        except Exception:
            pass
    return cur if cur and cur not in PLACEHOLDERS["hex_exe"] else ""


def is_placeholder(key, value):
    v = str(value or "").strip()
    return v in PLACEHOLDERS.get(key, set())


def prompt(text, default=None):
    if default is None or default == "":
        s = input(f"{text}: ").strip()
        return s
    s = input(f"{text} [{default}]: ").strip()
    return s if s != "" else str(default)


def confirm(text, default=False):
    suffix = "Y/n" if default else "y/N"
    s = input(f"{text} ({suffix}): ").strip().lower()
    if not s:
        return default
    return s in ("y", "yes")


def normalize_origin(value):
    v = str(value or "").strip()
    if EXT_ID_RE.fullmatch(v):
        v = f"chrome-extension://{v}"
    return v


def run_doctor():
    doctor = ROOT / "scripts" / "doctor.py"
    if not doctor.exists():
        print("Doctor script not found; skipping summary.")
        return
    print("\n--- doctor summary ---")
    try:
        subprocess.run([sys.executable, str(doctor)], cwd=str(ROOT), check=False)
    except Exception as e:
        print(f"(failed to run doctor: {e})")


def main():
    ap = argparse.ArgumentParser(description="Setup wizard for record2flowus local configuration")
    ap.add_argument("--yes", action="store_true", help="Non-interactive: accept defaults where safe (requires existing/derivable values for required fields)")
    ap.add_argument("--hex-exe", help="Hex executable path")
    ap.add_argument("--port", type=int, help="Bridge port (default 27123)")
    ap.add_argument("--origin", help="Extension origin (chrome-extension://<id>) or extension ID")
    ap.add_argument("--generate-token", action="store_true", help="Generate a new bridge token (will replace existing if --yes or confirmed)")
    ap.add_argument("--no-doctor", action="store_true", help="Skip doctor summary at the end")
    args = ap.parse_args()

    interactive = not args.yes

    if CONFIG_JSON.exists():
        print(f"NOTE: {CONFIG_JSON} exists. This project prefers config.local.json for personal settings (both are gitignored).")

    cfg, err = load_json_file(CONFIG_LOCAL)
    if err:
        eprint(f"ERROR: {err}")
        eprint("Fix the file or remove it, then re-run setup.")
        return 2
    if cfg is None:
        cfg = {}
        if EXAMPLE.exists():
            ex, ex_err = load_json_file(EXAMPLE)
            if ex_err:
                print(f"Warning: could not read config.example.json: {ex_err}")
            elif isinstance(ex, dict):
                cfg.update(ex)
        print(f"Creating {CONFIG_LOCAL}")
    else:
        print(f"Updating {CONFIG_LOCAL} (existing values preserved unless changed)")

    changed = False

    # hex_exe
    detected_hex = detect_hex_exe(args.hex_exe or cfg.get("hex_exe", ""))
    cur_hex = str(cfg.get("hex_exe") or "")
    if args.hex_exe:
        new_hex = args.hex_exe.strip()
    elif interactive:
        prompt_default = detected_hex or (cur_hex if cur_hex not in PLACEHOLDERS["hex_exe"] else "")
        new_hex = prompt("Hex executable path (leave blank to auto-detect/fallback)", prompt_default)
        if not new_hex:
            new_hex = detected_hex
    else:
        new_hex = detected_hex or (cur_hex if cur_hex not in PLACEHOLDERS["hex_exe"] else "")
    if new_hex and new_hex != cur_hex:
        cfg["hex_exe"] = new_hex
        changed = True
    elif not cfg.get("hex_exe") and new_hex:
        cfg["hex_exe"] = new_hex
        changed = True

    # meetings/export dirs - keep existing or empty
    cfg.setdefault("hex_meetings_dir", "")
    cfg.setdefault("hex_export_dir", "")

    # port
    cur_port = cfg.get("bridge_port", 27123)
    try:
        cur_port_int = int(cur_port)
    except Exception:
        cur_port_int = 27123
    if args.port is not None:
        new_port = args.port
    elif interactive:
        new_port_raw = prompt("Bridge port", cur_port_int)
        try:
            new_port = int(new_port_raw)
        except Exception:
            print("Invalid port; keeping previous/default value.")
            new_port = cur_port_int
    else:
        new_port = cur_port_int
    if not (1 <= new_port <= 65535):
        print("Port out of range; using 27123.")
        new_port = 27123
    if cfg.get("bridge_port") != new_port:
        cfg["bridge_port"] = new_port
        changed = True

    # sync_script (repo default)
    desired_sync = str(cfg.get("sync_script") or "").strip()
    if not desired_sync or desired_sync in ("sync_v2.py",):
        desired_sync = "scripts/sync_to_flowus.py"
    if (ROOT / "sync_v2.py").exists() and not (ROOT / "scripts" / "sync_to_flowus.py").exists():
        desired_sync = "sync_v2.py"
    if args.yes and str(cfg.get("sync_script") or "").strip():
        desired_sync = str(cfg.get("sync_script")).strip()
    if cfg.get("sync_script") != desired_sync:
        cfg["sync_script"] = desired_sync
        changed = True

    # token
    cur_token = str(cfg.get("bridge_token") or "").strip()
    token_missing = is_placeholder("bridge_token", cur_token) or len(cur_token) < 32
    new_token = cur_token
    if args.generate_token or token_missing:
        should_generate = True
        if args.generate_token and not token_missing and interactive:
            should_generate = confirm("Replace existing bridge_token with a new random token?", default=False)
        elif args.generate_token and not token_missing and args.yes:
            should_generate = True
        if should_generate:
            new_token = secrets.token_urlsafe(32)
            if new_token != cur_token:
                cfg["bridge_token"] = new_token
                changed = True
            print("\nIMPORTANT: A bridge_token has been generated/set for config.local.json.")
            print("Copy this SAME token into the browser extension Options page (stored locally in extension storage).")
            print(f"bridge_token: {new_token}\n")
    elif interactive:
        if confirm("Generate a new random bridge_token (replaces current)?", default=False):
            new_token = secrets.token_urlsafe(32)
            cfg["bridge_token"] = new_token
            changed = True
            print("\nIMPORTANT: New bridge_token generated.")
            print("Copy this SAME token into the browser extension Options page.")
            print(f"bridge_token: {new_token}\n")

    # origin
    cur_origin = str(cfg.get("bridge_allowed_origin") or "").strip()
    if args.origin:
        new_origin = normalize_origin(args.origin)
    elif interactive:
        shown = "" if is_placeholder("bridge_allowed_origin", cur_origin) else cur_origin
        entered = prompt("Extension ID or origin (chrome-extension://<id>)", shown)
        new_origin = normalize_origin(entered) if entered else cur_origin
    else:
        new_origin = cur_origin

    if new_origin and new_origin != cur_origin:
        cfg["bridge_allowed_origin"] = new_origin
        changed = True

    # normalize placeholders away if still example
    if is_placeholder("hex_exe", cfg.get("hex_exe")):
        cfg["hex_exe"] = detected_hex or ""

    # validate & warn
    token_final = str(cfg.get("bridge_token") or "").strip()
    origin_final = str(cfg.get("bridge_allowed_origin") or "").strip()
    warnings = []
    if is_placeholder("bridge_token", token_final) or len(token_final) < 32:
        warnings.append("bridge_token is missing/weak. Run setup again with token generation or set a random token >= 32 chars.")
    if is_placeholder("bridge_allowed_origin", origin_final):
        warnings.append("bridge_allowed_origin is not set. Load the unpacked extension, copy its ID, and set chrome-extension://<id>.")
    elif not ORIGIN_RE.match(origin_final):
        warnings.append(f"bridge_allowed_origin format looks invalid: {origin_final}")

    hex_final = str(cfg.get("hex_exe") or "").strip()
    if hex_final and not Path(hex_final).exists():
        warnings.append(f"hex_exe path does not exist: {hex_final}")

    save_json_file(CONFIG_LOCAL, cfg)
    if changed:
        print(f"Saved: {CONFIG_LOCAL}")
    else:
        print(f"No changes written (file ensured): {CONFIG_LOCAL}")

    if warnings:
        print("\nWarnings / next steps:")
        for w in warnings:
            print(f"- {w}")
    else:
        print("\nConfig looks structurally ready.")
    print("- After loading the extension, confirm its ID matches bridge_allowed_origin.")
    print("- Set the SAME bridge_token and port in the extension Options page.")
    print("- Restart bridge.py after changing config.")

    if not args.no_doctor:
        run_doctor()

    # exit non-zero only on hard issues for automation; keep 0 for interactive guidance
    hard_fail = is_placeholder("bridge_token", token_final) or len(token_final) < 32
    if args.yes and hard_fail:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())