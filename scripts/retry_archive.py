import argparse
import json
import os
import sys

# run from repo root; ensure local imports work
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.archive_store import ArchiveStore, default_db_path


def main():
    ap = argparse.ArgumentParser(description="Retry a failed/needs-review voice-note archive (local ledger)")
    ap.add_argument("--archive-id", help="Archive id to retry")
    ap.add_argument("--meeting-id", help="Meeting id (with --page-id)")
    ap.add_argument("--page-id", help="Target FlowUs page id (with --meeting-id)")
    ap.add_argument("--list", action="store_true", help="List retryable archives")
    ap.add_argument("--db", help="Ledger sqlite path (default: APPDATA/hex-flowus-voice-note/archive_ledger.sqlite3)")
    ap.add_argument("--call-bridge", action="store_true", help="Ask running bridge at 127.0.0.1:<port> to retry instead of running locally")
    ap.add_argument("--bridge-port", type=int, default=27123)
    ap.add_argument("--bridge-token", help="Bridge token (required with --call-bridge)")
    args = ap.parse_args()

    db_path = args.db or default_db_path()
    store = ArchiveStore(db_path)

    if args.list:
        items = store.list_retryable(limit=50)
        print(json.dumps({"ok": True, "archives": items}, ensure_ascii=False, indent=2))
        return

    archive_id = args.archive_id
    rec = None
    if archive_id:
        rec = store.get(archive_id)
    elif args.meeting_id and args.page_id:
        rec = store.find_by_meeting_page(args.meeting_id, args.page_id)
        if rec:
            archive_id = rec.get("archive_id")
    else:
        # pick latest retryable
        items = store.list_retryable(limit=1)
        if items:
            rec = items[0]
            archive_id = rec.get("archive_id")

    if not rec or not archive_id:
        print(json.dumps({"ok": False, "error": "no retryable archive found (use --list or specify --archive-id)"} , ensure_ascii=False))
        sys.exit(1)

    if args.call_bridge:
        if not args.bridge_token:
            print(json.dumps({"ok": False, "error": "--bridge-token required with --call-bridge"}, ensure_ascii=False))
            sys.exit(1)
        import urllib.request
        payload = {"archive_id": archive_id}
        req = urllib.request.Request(
            f"http://127.0.0.1:{args.bridge_port}/retry",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {args.bridge_token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            print(body)
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            sys.exit(1)
        return

    # local retry path: reuse bridge logic by importing and running process flow
    try:
        import bridge
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"failed to import bridge: {e}"}, ensure_ascii=False))
        sys.exit(1)

    meeting_id = rec.get("meeting_id")
    page_id = rec.get("target_page_id")
    if not meeting_id or not page_id:
        print(json.dumps({"ok": False, "error": "archive record missing meeting/page id"}, ensure_ascii=False))
        sys.exit(1)

    # mark pending if needed and run
    if rec.get("status") == "needs_review":
        store.mark_retry_pending(archive_id)
    claim = store.claim(archive_id, meeting_id, target_page_id=page_id, allow_review_retry=True)
    if not claim.get("ok"):
        if claim.get("reason") == "already_done":
            print(json.dumps({"ok": True, "skipped": True, "reason": "already_done", "archive_id": archive_id}, ensure_ascii=False))
            return
        print(json.dumps({"ok": False, "error": f"cannot claim archive: {claim.get('reason')}"}, ensure_ascii=False))
        sys.exit(1)

    try:
        result = bridge.process_and_upload(meeting_id, page_id, archive_id=archive_id, retry=True)
        store.mark_done(archive_id, result=result)
        print(json.dumps({"ok": True, "archive_id": archive_id, "result": result}, ensure_ascii=False))
    except Exception as e:
        uncertain = bool(getattr(e, "uncertain", False))
        if uncertain:
            store.mark_needs_review(archive_id, str(e), result=getattr(e, "result", None))
        else:
            store.mark_error(archive_id, str(e))
        print(json.dumps({"ok": False, "archive_id": archive_id, "uncertain": uncertain, "error": str(e)}, ensure_ascii=False))
        sys.exit(2 if uncertain else 1)


if __name__ == "__main__":
    main()