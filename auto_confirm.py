#!/usr/bin/env python3
"""Auto-confirm Docspell items that have enough metadata.

Background: every email-imported document lands in `state=created`
(Docspell's "inbox"). Without intervention these pile up indefinitely.
A human-style confirmation rule:

    item is safe to auto-confirm IF
        state == "created"
        AND folder is set                     (Library, Personal, …)
        AND at least one tag is attached      (Book:* or doctype:* or …)
        AND optionally: source matches a whitelist  (e.g. mailbox-*)

Items missing folder OR tags are left in created state for manual review.

Cron-friendly: idempotent, read-only without --apply, no stdin prompts
when DOCSPELL_PASSWORD or keychain entry is available. Designed to be
invoked from a macOS LaunchAgent (see COMMANDS.md §7).

Usage:

    # Dry-run: see what would be confirmed
    python3 auto_confirm.py

    # Restrict to email-imported items
    python3 auto_confirm.py --source-prefix mailbox-

    # Restrict to specific folder
    python3 auto_confirm.py --folder Library

    # Actually apply
    python3 auto_confirm.py --apply --confirm AUTO-CONFIRM

Confirmation phrase: ``AUTO-CONFIRM``.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_URL = "https://docspell.medarov.net"
DEFAULT_ACCOUNT = "library/dmedarov"
DEFAULT_KEYCHAIN_SERVICE = "docspell"
DEFAULT_OUTPUT_CSV = "out/auto-confirm-log.csv"
CONFIRM_PHRASE = "AUTO-CONFIRM"


# ---------------------------------------------------------------------------
# HTTP plumbing (uses same patterns as the other scripts)
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
        with urllib.request.urlopen(req, timeout=60) as resp:
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
    return subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()


def login(base: str, account: str, password: str) -> str:
    data = request_json(
        "POST",
        api_url(base, "/open/auth/login"),
        body={"account": account, "password": password},
    )
    token = data.get("token") if isinstance(data, dict) else None
    if not token:
        raise RuntimeError(f"login failed: {data}")
    return token


# ---------------------------------------------------------------------------
# Item enumeration & confirm
# ---------------------------------------------------------------------------


def list_inbox_items(
    base: str,
    token: str,
    *,
    folder_id: str | None = None,
    max_items: int = 5000,
) -> list[dict[str, Any]]:
    """Return items in state=created (Docspell inbox), paginated.

    Uses ``inbox:yes`` qualifier. Optionally narrows to a specific folder.
    Server caps page size at ~200.
    """
    out: list[dict[str, Any]] = []
    offset = 0
    page = 200
    query = "inbox:yes"
    if folder_id:
        query = f"inbox:yes folder.id={folder_id}"
    while len(out) < max_items:
        # withDetails=true required so the response includes the `tags`
        # array — otherwise every item looks like "no tag" and we'd
        # never auto-confirm anything.
        params = urllib.parse.urlencode(
            {"q": query, "limit": page, "offset": offset, "withDetails": "true"}
        )
        data = request_json(
            "GET", f"{api_url(base, '/sec/item/search')}?{params}", token=token
        )
        batch: list[dict[str, Any]] = []
        for g in data.get("groups", []):
            for it in g.get("items", []):
                batch.append(it)
        if not batch:
            break
        out.extend(batch)
        offset += len(batch)
    return out


def find_folder_id(base: str, token: str, name: str) -> str:
    data = request_json("GET", api_url(base, "/sec/folder"), token=token)
    for f in data.get("items", []):
        if f.get("name") == name:
            return f["id"]
    raise RuntimeError(f"folder {name!r} not found")


def confirm_batch(base: str, token: str, ids: list[str]) -> None:
    # IdList schema uses field name `ids` (not `items`).
    request_json(
        "PUT",
        api_url(base, "/sec/items/confirm"),
        token=token,
        body={"ids": ids},
    )


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------


def is_eligible(
    item: dict[str, Any],
    *,
    require_folder: bool,
    require_tag: bool,
    source_prefix: str | None,
) -> tuple[bool, str]:
    """Return (eligible, reason). Reason is empty when eligible."""
    if item.get("state") != "created":
        return False, "not in created state"
    if require_folder:
        folder = item.get("folder")
        if not folder or not folder.get("id"):
            return False, "no folder"
    if require_tag and not item.get("tags"):
        return False, "no tag"
    if source_prefix:
        src = item.get("source") or ""
        if not src.startswith(source_prefix):
            return False, f"source '{src}' not matching prefix '{source_prefix}'"
    return True, ""


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--account", default=DEFAULT_ACCOUNT)
    p.add_argument("--keychain-service", default=DEFAULT_KEYCHAIN_SERVICE)
    p.add_argument(
        "--folder",
        default="",
        help="Restrict to items in this Docspell folder (default: any)",
    )
    p.add_argument(
        "--source-prefix",
        default="",
        help="Restrict to items whose `source` starts with this prefix "
        "(e.g. 'mailbox-' for email-imported items only)",
    )
    p.add_argument(
        "--require-tag",
        action="store_true",
        default=True,
        help="Require ≥1 tag (default: true; use --no-require-tag to disable)",
    )
    p.add_argument("--no-require-tag", dest="require_tag", action="store_false")
    p.add_argument(
        "--max-items",
        type=int,
        default=5000,
        help="Cap number of items to scan",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="How many item IDs to send per /confirm call",
    )
    p.add_argument(
        "--out",
        default=DEFAULT_OUTPUT_CSV,
        help=f"Log CSV (default: {DEFAULT_OUTPUT_CSV})",
    )
    p.add_argument("--apply", action="store_true")
    p.add_argument("--confirm", default="")
    args = p.parse_args()

    if args.apply and args.confirm != CONFIRM_PHRASE:
        print(
            f"ERROR: --apply requires --confirm {CONFIRM_PHRASE}", file=sys.stderr
        )
        return 2

    print(f"Docspell URL:        {args.url}")
    print(f"Account:             {args.account}")
    print(f"Folder filter:       {args.folder or '<any>'}")
    print(f"Source prefix:       {args.source_prefix or '<none>'}")
    print(f"Require tag:         {args.require_tag}")
    print(f"Mode:                {'APPLY' if args.apply else 'dry-run'}")

    password = os.environ.get("DOCSPELL_PASSWORD") or keychain_get(
        args.keychain_service, args.account
    )
    token = login(args.url, args.account, password)
    folder_id = None
    if args.folder:
        folder_id = find_folder_id(args.url, token, args.folder)

    print("\nListing inbox items (state=created)...")
    items = list_inbox_items(args.url, token, folder_id=folder_id, max_items=args.max_items)
    print(f"Found {len(items)} items in inbox.")

    eligible: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}
    for it in items:
        ok, reason = is_eligible(
            it,
            require_folder=True,
            require_tag=args.require_tag,
            source_prefix=args.source_prefix or None,
        )
        if ok:
            eligible.append(it)
        else:
            skipped[reason] = skipped.get(reason, 0) + 1

    print(f"Eligible for auto-confirm: {len(eligible)}")
    print(f"Skipped: {sum(skipped.values())}")
    for reason, n in sorted(skipped.items(), key=lambda x: -x[1]):
        print(f"  {n:5d}  {reason}")

    # Write CSV log
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    with out_path.open("a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=["timestamp", "item_id", "name", "folder", "tags", "source", "action"],
        )
        if fh.tell() == 0:
            w.writeheader()
        for it in eligible:
            w.writerow(
                {
                    "timestamp": timestamp,
                    "item_id": it.get("id", ""),
                    "name": it.get("name", ""),
                    "folder": (it.get("folder") or {}).get("name", ""),
                    "tags": ";".join(t.get("name", "") for t in it.get("tags", [])),
                    "source": it.get("source", ""),
                    "action": "would-confirm" if not args.apply else "confirmed",
                }
            )

    if not args.apply:
        print(f"\n(dry-run — to apply, run with --apply --confirm {CONFIRM_PHRASE})")
        print(f"Log appended to {out_path}")
        return 0

    if not eligible:
        print("\nNothing to confirm. Done.")
        return 0

    ids = [it["id"] for it in eligible]
    print(f"\nConfirming {len(ids)} items in batches of {args.batch_size}...")
    for start in range(0, len(ids), args.batch_size):
        chunk = ids[start : start + args.batch_size]
        confirm_batch(args.url, token, chunk)
        print(f"  confirmed {start}..{start + len(chunk) - 1} ({len(chunk)} items)")
    print(f"\nDone. Log appended to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
