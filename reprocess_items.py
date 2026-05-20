#!/usr/bin/env python3
"""Reprocess Docspell items whose OCR'd text shows mojibake.

Background: items processed BEFORE the joex multi-language OCR override
was deployed (commit 843de75) were OCR'd with `-l eng` regardless of
actual content. Bulgarian/Cyrillic documents from that era have garbled
text like ``ÎÂÌËÂ`` instead of readable Cyrillic.

This script:
  1. Lists items in the Library folder (paginated).
  2. For each, fetches the first 1500 chars of extracted text.
  3. Scores each text for mojibake (ratio of Latin-1-misinterpreted-Cyrillic
     bytes to total non-space chars).
  4. Outputs a CSV plan: item_id, name, mojibake_score, recommendation.
  5. With --apply --confirm REPROCESS-OCR, batches the flagged items
     to /sec/items/reprocess for re-OCR with the now-active bul+eng+rus.

Confirmed items keep their metadata (folder, tags, correspondent,
custom fields). Only the OCR'd text + thumbnails are regenerated.

Usage:

    # Dry-run: just produce the CSV plan
    python3 reprocess_items.py

    # Lower the mojibake threshold to catch more items
    python3 reprocess_items.py --threshold 0.05

    # Use a curated list instead of auto-detection
    python3 reprocess_items.py --ids-file ids.txt

    # Actually reprocess
    python3 reprocess_items.py --apply --confirm REPROCESS-OCR

Confirmation phrase: ``REPROCESS-OCR``.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_URL = "https://docspell.medarov.net"
DEFAULT_ACCOUNT = "library/dmedarov"
DEFAULT_KEYCHAIN_SERVICE = "docspell"
DEFAULT_FOLDER = "Library"
DEFAULT_OUTPUT_CSV = "out/reprocess-plan.csv"
CONFIRM_PHRASE = "REPROCESS-OCR"

# Characters typical of "UTF-8 Cyrillic decoded as Latin-1" mojibake.
# When a Cyrillic byte sequence (e.g. 0xD0 0xA0 = 'Р') gets reinterpreted
# as two Latin-1 chars ('Ð' + ' '), the result is a mishmash of these:
MOJIBAKE_MARKERS = set("ÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ"
                       "ÂÃÄÅÆÇÈÉÊËÌÍÎÏ"
                       "fl"  # ÚÂÎfl pattern very common
                       )
CYRILLIC_CHARS = set("АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЬЮЯабвгдежзийклмнопрстуфхцчшщъьюяёЁ")


# ---------------------------------------------------------------------------
# HTTP plumbing
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
    raw_response: bool = False,
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
    if raw_response:
        return raw
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
# Folder + item enumeration (uses fixed pagination — see verify_docspell.py)
# ---------------------------------------------------------------------------


def find_folder_id(base: str, token: str, name: str) -> str:
    data = request_json("GET", api_url(base, "/sec/folder"), token=token)
    for f in data.get("items", []):
        if f.get("name") == name:
            return f["id"]
    raise RuntimeError(f"folder {name!r} not found")


def list_items_in_folder(
    base: str,
    token: str,
    folder_id: str | None,
) -> list[dict[str, Any]]:
    """Paginate all items in the given folder (or with no folder if id is None).

    Server caps page at ~200.
    """
    out: list[dict[str, Any]] = []
    offset = 0
    page = 200
    query = f"folder.id={folder_id}" if folder_id else "!exist:folder"
    while True:
        params = urllib.parse.urlencode(
            {"q": query, "limit": page, "offset": offset, "withDetails": "false"}
        )
        data = request_json(
            "GET", f"{api_url(base, '/sec/item/search')}?{params}", token=token
        )
        batch = []
        for g in data.get("groups", []):
            for it in g.get("items", []):
                batch.append(it)
        if not batch:
            break
        out.extend(batch)
        offset += len(batch)
    return out


def get_item_detail(base: str, token: str, item_id: str) -> dict[str, Any]:
    return request_json("GET", api_url(base, f"/sec/item/{item_id}"), token=token)


def get_attachment_text(base: str, token: str, att_id: str) -> str:
    raw = request_json(
        "GET",
        api_url(base, f"/sec/attachment/{att_id}/extracted-text"),
        token=token,
        raw_response=True,
    )
    try:
        return json.loads(raw).get("text", "")
    except Exception:
        return raw or ""


# ---------------------------------------------------------------------------
# Mojibake scoring
# ---------------------------------------------------------------------------


def mojibake_score(text: str) -> tuple[float, dict[str, int]]:
    """Return (score, stats). Higher score = more likely mojibake.

    Heuristic calibrated against real Docspell content (2026-05-20):

      score        meaning                            example
      ─────────    ─────────────────────────────────  ─────────────────────────
      0.20+        clear mojibake (re-OCR strongly    LADA 4x4 Album.pdf
                   recommended)                       (Cyrillic OCR'd as eng)
      0.10–0.20    likely mojibake                    mixed bilingual PDFs
      0.05–0.10    English text with OCR noise        DIY manuals, plain books
                   (false positive zone)              (NOT mojibake)
      0.00–0.05    clean text                         most academic English

    The down-weighting-by-Cyrillic-count rule from the v1 draft was
    REMOVED because LADA-style mojibake commonly has some clean Cyrillic
    in headers/titles ALONGSIDE mojibake body text. A simple ratio of
    Latin-1 noise to total non-space chars is the most robust signal;
    we just pick a tight threshold (0.15) to separate noise from real
    mojibake.
    """
    sample = text[:1500] if text else ""
    if not sample.strip():
        return 0.0, {"length": 0, "moji": 0, "cyr": 0, "non_space": 0}
    moji = sum(1 for c in sample if c in MOJIBAKE_MARKERS)
    cyr = sum(1 for c in sample if c in CYRILLIC_CHARS)
    non_space = sum(1 for c in sample if not c.isspace())
    score = (moji / non_space) if non_space else 0.0
    return score, {"length": len(sample), "moji": moji, "cyr": cyr, "non_space": non_space}


# ---------------------------------------------------------------------------
# Reprocess invocation
# ---------------------------------------------------------------------------


def reprocess_batch(base: str, token: str, ids: list[str]) -> None:
    # IdList schema uses field name `ids` (not `items`).
    request_json(
        "POST",
        api_url(base, "/sec/items/reprocess"),
        token=token,
        body={"ids": ids},
    )


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
        default=DEFAULT_FOLDER,
        help=f"Docspell folder to scan (default: {DEFAULT_FOLDER}). "
        "Pass --folder '' or --include-no-folder to scan items without folder.",
    )
    p.add_argument(
        "--include-no-folder",
        action="store_true",
        help="Also scan items that have no folder set (catches old webapp uploads)",
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=0.15,
        help="Mojibake score threshold for flagging (default: 0.15 — "
        "calibrated against LADA-style real mojibake at 0.22 vs English "
        "OCR noise at 0.05–0.08)",
    )
    p.add_argument(
        "--max-items",
        type=int,
        default=2000,
        help="Cap number of items to scan",
    )
    p.add_argument(
        "--ids-file",
        type=Path,
        help="Skip auto-detection — use this file of item IDs (one per line)",
    )
    p.add_argument(
        "--out",
        default=DEFAULT_OUTPUT_CSV,
        help=f"Output CSV with the plan (default: {DEFAULT_OUTPUT_CSV})",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="How many item IDs to send per /reprocess call",
    )
    p.add_argument("--apply", action="store_true")
    p.add_argument("--confirm", default="")
    args = p.parse_args()

    if args.apply and args.confirm != CONFIRM_PHRASE:
        print(
            f"ERROR: --apply requires --confirm {CONFIRM_PHRASE}", file=sys.stderr
        )
        return 2

    print(f"Docspell URL:    {args.url}")
    print(f"Account:         {args.account}")
    print(f"Folder:          {args.folder}")
    print(f"Threshold:       {args.threshold}")
    print(f"Mode:            {'APPLY' if args.apply else 'dry-run'}")

    password = os.environ.get("DOCSPELL_PASSWORD") or keychain_get(
        args.keychain_service, args.account
    )
    token = login(args.url, args.account, password)
    folder_id = find_folder_id(args.url, token, args.folder) if args.folder else None
    if folder_id:
        print(f"\nResolved folder {args.folder} → {folder_id}")
    else:
        print("\nScanning items without folder (--include-no-folder or --folder '')")

    # --- Determine which IDs to consider ---
    if args.ids_file:
        ids = [
            line.strip()
            for line in args.ids_file.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        print(f"Loaded {len(ids)} IDs from {args.ids_file}")
        plan: list[dict[str, Any]] = [
            {"item_id": i, "name": "(from file)", "score": 1.0, "flag": True}
            for i in ids
        ]
    else:
        print(f"Listing items in {args.folder or '<no folder>'}...")
        items = list_items_in_folder(args.url, token, folder_id)
        if args.include_no_folder and folder_id is not None:
            no_folder_items = list_items_in_folder(args.url, token, None)
            print(f"  + {len(no_folder_items)} additional items without folder")
            items = items + no_folder_items
        print(f"Found {len(items)} items, scoring up to {args.max_items} of them.")

        plan = []
        for i, it in enumerate(items[: args.max_items]):
            if i and i % 25 == 0:
                print(f"  ...scored {i}/{min(len(items), args.max_items)}")
            try:
                detail = get_item_detail(args.url, token, it["id"])
                atts = detail.get("attachments", [])
                if not atts:
                    continue
                text = get_attachment_text(args.url, token, atts[0]["id"])
                score, stats = mojibake_score(text)
                plan.append(
                    {
                        "item_id": it["id"],
                        "name": it.get("name", ""),
                        "score": round(score, 4),
                        "cyr_count": stats.get("cyr", 0),
                        "moji_count": stats.get("moji", 0),
                        "length": stats.get("length", 0),
                        "flag": score >= args.threshold,
                    }
                )
                if score >= args.threshold:
                    print(
                        f"    FLAG score={score:.3f} cyr={stats.get('cyr',0)} "
                        f"moji={stats.get('moji',0)} {it.get('name','')[:50]}"
                    )
            except Exception as e:
                print(f"    ERR fetching item {it.get('id','?')[:18]}: {e}")

    # --- Write CSV plan ---
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=["item_id", "name", "score", "cyr_count", "moji_count", "length", "flag"],
        )
        w.writeheader()
        for row in plan:
            w.writerow(row)
    flagged = [p for p in plan if p["flag"]]
    print(f"\nWrote plan to {out_path}")
    print(f"Total scored: {len(plan)} | Flagged for reprocess: {len(flagged)}")

    if not args.apply:
        print(f"\n(dry-run — to apply, run with --apply --confirm {CONFIRM_PHRASE})")
        return 0

    if not flagged:
        print("\nNothing to reprocess. Done.")
        return 0

    # --- Submit batches ---
    ids = [p["item_id"] for p in flagged]
    print(f"\nSubmitting {len(ids)} items for reprocess in batches of {args.batch_size}")
    for start in range(0, len(ids), args.batch_size):
        chunk = ids[start : start + args.batch_size]
        reprocess_batch(args.url, token, chunk)
        print(f"  submitted batch {start}..{start + len(chunk) - 1} ({len(chunk)} items)")
        time.sleep(0.5)
    print("\nAll batches submitted. joex will process the queue (~30s per item).")
    print("Monitor: curl /sec/queue/state, or watch joex logs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
