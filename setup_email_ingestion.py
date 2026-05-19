#!/usr/bin/env python3
"""Configure Docspell IMAP scan-mailbox ingestion for Gmail.

Reads Gmail App Password from macOS keychain (service `docspell-gmail`,
account = your Gmail address). Idempotent — re-running updates the
existing IMAP connection + scan task instead of creating duplicates.

Setup before first run:

    # 1. Generate Gmail App Password at https://myaccount.google.com/apppasswords
    # 2. Store it in keychain (one-time):
    security add-generic-password -s docspell-gmail \
        -a <your-gmail>@gmail.com -w

Run:

    python3 setup_email_ingestion.py --gmail <your-gmail>@gmail.com \
        [--label Docspell] [--folder-name Library] \
        [--schedule "*-*-* *:0/15:00 UTC"] [--attachments-only true] \
        [--dry-run] [--apply --confirm SETUP-EMAIL]

Confirmation phrase: ``SETUP-EMAIL`` (consistent with other apply scripts).
"""

from __future__ import annotations

import argparse
import getpass
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_URL = "https://docspell.medarov.net"
DEFAULT_ACCOUNT = "library/dmedarov"
DEFAULT_KEYCHAIN_SERVICE = "docspell"
DEFAULT_GMAIL_KEYCHAIN_SERVICE = "docspell-gmail"
DEFAULT_IMAP_NAME = "gmail-docspell"
DEFAULT_LABEL = "Docspell"
DEFAULT_DOCSPELL_FOLDER = "Library"
DEFAULT_SCHEDULE = "*-*-* *:0/15:00 UTC"
CONFIRM_PHRASE = "SETUP-EMAIL"


# ---------------------------------------------------------------------------
# HTTP helpers (mirrors patterns from apply_reviewed_actions.py)
# ---------------------------------------------------------------------------


def api_url(base: str, path: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/api/v1"):
        return f"{base}{path}"
    return f"{base}/api/v1{path}"


def request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Docspell-Auth"] = token
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code}: {body_text[:300]}") from None
    if not raw.strip():
        return None
    return json.loads(raw)


def keychain_get(service: str, account: str | None = None) -> str:
    cmd = ["security", "find-generic-password", "-s", service, "-w"]
    if account:
        cmd.extend(["-a", account])
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
    except subprocess.CalledProcessError:
        raise RuntimeError(
            f"Could not find password in keychain (service={service}). "
            f"Store first: security add-generic-password -s {service} -a <user> -w"
        ) from None


def login(base: str, account: str, password: str) -> str:
    data = request_json(
        "POST",
        api_url(base, "/open/auth/login"),
        body={"account": account, "password": password},
    )
    token = data.get("token") if isinstance(data, dict) else None
    if not token:
        raise RuntimeError(
            f"Login failed for account={account}: {json.dumps(data)[:200]}"
        )
    return token


# ---------------------------------------------------------------------------
# Folder lookup
# ---------------------------------------------------------------------------


def find_folder_id(base: str, token: str, name: str) -> str:
    data = request_json("GET", api_url(base, "/sec/folder"), token=token)
    for f in data.get("items", []):
        if f.get("name") == name:
            return f["id"]
    raise RuntimeError(
        f"Docspell folder {name!r} not found. Existing: "
        f"{[f.get('name') for f in data.get('items', [])]}"
    )


# ---------------------------------------------------------------------------
# IMAP connection
# ---------------------------------------------------------------------------


def imap_exists(base: str, token: str, name: str) -> bool:
    data = request_json("GET", api_url(base, "/sec/email/settings/imap"), token=token)
    return any(x.get("name") == name for x in data.get("items", []))


def imap_upsert(
    base: str,
    token: str,
    *,
    name: str,
    host: str,
    port: int,
    user: str,
    password: str,
    ssl_type: str = "ssl",
) -> None:
    body = {
        "name": name,
        "imapHost": host,
        "imapPort": port,
        "imapUser": user,
        "imapPassword": password,
        "sslType": ssl_type,
        "ignoreCertificates": False,
        "useOAuth": False,
    }
    method = "PUT" if imap_exists(base, token, name) else "POST"
    path = (
        f"/sec/email/settings/imap/{urllib.parse.quote(name)}"
        if method == "PUT"
        else "/sec/email/settings/imap"
    )
    request_json(method, api_url(base, path), token=token, body=body)


# ---------------------------------------------------------------------------
# Scan-mailbox usertask
# ---------------------------------------------------------------------------


def scan_find_by_imap(base: str, token: str, imap_name: str) -> dict[str, Any] | None:
    data = request_json(
        "GET", api_url(base, "/sec/usertask/scanmailbox"), token=token
    )
    for t in data.get("items", []):
        if t.get("imapConnection") == imap_name:
            return t
    return None


def scan_upsert(
    base: str,
    token: str,
    *,
    imap_name: str,
    folders: list[str],
    schedule: str,
    item_folder_id: str,
    received_since_hours: int = 168,
    attachments_only: bool = True,
    summary: str = "Gmail Docspell label sync",
) -> str:
    existing = scan_find_by_imap(base, token, imap_name)
    body = {
        "id": existing["id"] if existing else "",
        "enabled": True,
        "summary": summary,
        "imapConnection": imap_name,
        "scanRecursively": False,
        "schedule": schedule,
        "folders": folders,
        "receivedSinceHours": received_since_hours,
        "deleteMail": False,
        "direction": "incoming",
        "itemFolder": item_folder_id,
        "fileFilter": "",
        "subjectFilter": "",
        "attachmentsOnly": attachments_only,
        "postHandleAll": False,
    }
    method = "PUT" if existing else "POST"
    path = (
        f"/sec/usertask/scanmailbox/{existing['id']}"
        if existing
        else "/sec/usertask/scanmailbox"
    )
    request_json(method, api_url(base, path), token=token, body=body)
    # Reload to get the id (especially for POST creates)
    current = scan_find_by_imap(base, token, imap_name)
    return current["id"] if current else ""


def scan_start_once(
    base: str,
    token: str,
    *,
    task_id: str,
    imap_name: str,
    folders: list[str],
    schedule: str,
    item_folder_id: str,
    received_since_hours: int,
    attachments_only: bool,
    summary: str,
) -> None:
    body = {
        "id": task_id,
        "enabled": True,
        "summary": summary,
        "imapConnection": imap_name,
        "scanRecursively": False,
        "schedule": schedule,
        "folders": folders,
        "receivedSinceHours": received_since_hours,
        "deleteMail": False,
        "direction": "incoming",
        "itemFolder": item_folder_id,
        "fileFilter": "",
        "subjectFilter": "",
        "attachmentsOnly": attachments_only,
        "postHandleAll": False,
    }
    request_json(
        "POST",
        api_url(base, "/sec/usertask/scanmailbox/startonce"),
        token=token,
        body=body,
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--account", default=DEFAULT_ACCOUNT)
    p.add_argument(
        "--keychain-service",
        default=DEFAULT_KEYCHAIN_SERVICE,
        help=f"Keychain service for Docspell password (default: {DEFAULT_KEYCHAIN_SERVICE})",
    )
    p.add_argument(
        "--gmail-keychain-service",
        default=DEFAULT_GMAIL_KEYCHAIN_SERVICE,
        help=f"Keychain service for Gmail App Password (default: {DEFAULT_GMAIL_KEYCHAIN_SERVICE})",
    )
    p.add_argument("--gmail", required=True, help="Your Gmail address (the IMAP user)")
    p.add_argument(
        "--imap-name",
        default=DEFAULT_IMAP_NAME,
        help=f"IMAP connection identifier in Docspell (default: {DEFAULT_IMAP_NAME})",
    )
    p.add_argument(
        "--label",
        default=DEFAULT_LABEL,
        help=f"Gmail label/folder to scan (default: {DEFAULT_LABEL})",
    )
    p.add_argument(
        "--folder-name",
        default=DEFAULT_DOCSPELL_FOLDER,
        help=f"Docspell folder for imported items (default: {DEFAULT_DOCSPELL_FOLDER})",
    )
    p.add_argument(
        "--schedule",
        default=DEFAULT_SCHEDULE,
        help=f"systemd-timer-like schedule (default: {DEFAULT_SCHEDULE!r})",
    )
    p.add_argument(
        "--received-since-hours",
        type=int,
        default=168,
        help="How far back to look at first scan (default: 168 = 7 days)",
    )
    p.add_argument(
        "--attachments-only",
        choices=("true", "false"),
        default="true",
        help="Import only attachments (skip emails without files)",
    )
    p.add_argument(
        "--start-once",
        action="store_true",
        help="Trigger an immediate scan after setup (in addition to the schedule)",
    )
    p.add_argument("--dry-run", action="store_true", help="Show what would be done")
    p.add_argument("--apply", action="store_true", help="Actually apply changes")
    p.add_argument(
        "--confirm",
        default="",
        help=f"Required with --apply (must equal {CONFIRM_PHRASE!r})",
    )
    args = p.parse_args()

    if args.apply and args.confirm != CONFIRM_PHRASE:
        print(
            f"ERROR: --apply requires --confirm {CONFIRM_PHRASE}", file=sys.stderr
        )
        return 2

    print(f"Docspell URL:        {args.url}")
    print(f"Docspell account:    {args.account}")
    print(f"Gmail address:       {args.gmail}")
    print(f"Gmail label (src):   {args.label}")
    print(f"Docspell folder:     {args.folder_name}")
    print(f"IMAP connection:     {args.imap_name}")
    print(f"Schedule:            {args.schedule}")
    print(f"attachmentsOnly:     {args.attachments_only}")
    print(f"receivedSinceHours:  {args.received_since_hours}")

    if not args.apply:
        print("\n(dry-run — re-run with --apply --confirm SETUP-EMAIL)")
        return 0

    # --- credentials ---
    docspell_pw = keychain_get(args.keychain_service, args.account)
    gmail_pw = keychain_get(args.gmail_keychain_service, args.gmail)

    # --- login + folder lookup ---
    token = login(args.url, args.account, docspell_pw)
    folder_id = find_folder_id(args.url, token, args.folder_name)
    print(f"\nResolved {args.folder_name} folder id = {folder_id}")

    # --- IMAP connection ---
    imap_upsert(
        args.url,
        token,
        name=args.imap_name,
        host="imap.gmail.com",
        port=993,
        user=args.gmail,
        password=gmail_pw,
        ssl_type="ssl",
    )
    print(f"IMAP connection {args.imap_name!r} upserted.")

    # --- Scan-mailbox usertask ---
    task_id = scan_upsert(
        args.url,
        token,
        imap_name=args.imap_name,
        folders=[args.label],
        schedule=args.schedule,
        item_folder_id=folder_id,
        received_since_hours=args.received_since_hours,
        attachments_only=args.attachments_only == "true",
    )
    print(f"Scan-mailbox task upserted, id={task_id}")

    if args.start_once:
        scan_start_once(
            args.url,
            token,
            task_id=task_id,
            imap_name=args.imap_name,
            folders=[args.label],
            schedule=args.schedule,
            item_folder_id=folder_id,
            received_since_hours=args.received_since_hours,
            attachments_only=args.attachments_only == "true",
            summary="Gmail Docspell label sync",
        )
        print("Triggered immediate scan via /sec/usertask/scanmailbox/startonce")

    print("\nDone. Next scheduled run will pick up mails in the Gmail label.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
