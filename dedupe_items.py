#!/usr/bin/env python3
"""Detect and (optionally) delete duplicate Docspell items by title.

Reads ``out/docspell-name-classification.csv`` and groups items by
``title`` (filename as Docspell stores it). For any title that appears
more than once, the script reports the duplicate set — titles, item_ids,
upload dates, attachment counts — and recommends keeping the OLDEST
item (lowest ``itemDate`` / ``created``) on the theory that the first
upload is the canonical one and later copies are accidental re-uploads.

Defaults to a dry-run report only. ``--apply --confirm DEDUPE-DELETE``
will actually delete the non-keeper duplicates via DELETE
/api/v1/sec/item/{id}. This is destructive, so the script:

  * Prints the full plan first
  * Prompts interactively to type the confirm phrase a second time
  * Only then issues DELETE calls
  * Writes ``out/dedupe-plan.csv`` either way

In dry-run mode no DELETE is ever issued. Re-running is safe.

Endpoints used:

    GET  /api/info/version
    POST /open/auth/login              (only if DOCSPELL_TOKEN missing)
    GET  /sec/item/{id}                (read-only, to fetch date + attachments)
    DELETE /sec/item/{id}              (only with --apply, after 2-step prompt)

Never prints passwords, tokens, cookies, or auth headers.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_URL = "https://docspell.medarov.net"
DEFAULT_CSV = "out/docspell-name-classification.csv"
DEFAULT_PLAN_CSV = "out/dedupe-plan.csv"
CONFIRM_PHRASE = "DEDUPE-DELETE"


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def api_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/api/v1"):
        return f"{base}{path}"
    return f"{base}/api/v1{path}"


def redact(text: str) -> str:
    """Remove anything that smells like a credential before printing."""
    text = re.sub(r'("token"\s*:\s*")[^"]+', r"\1<redacted>", text)
    text = re.sub(r'("password"\s*:\s*")[^"]+', r"\1<redacted>", text)
    text = re.sub(r"(X-Docspell-Auth:\s*)\S+", r"\1<redacted>", text, flags=re.I)
    text = re.sub(r"(Cookie:\s*)[^\r\n]+", r"\1<redacted>", text, flags=re.I)
    text = re.sub(r"(Authorization:\s*)\S+", r"\1<redacted>", text, flags=re.I)
    return text


def request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 60,
) -> Any:
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    if token:
        headers["X-Docspell-Auth"] = token

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(
            f"HTTP {exc.code} from {method} {url}: {redact(detail)}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc

    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def prompt_credentials(args: argparse.Namespace) -> tuple[str, str]:
    account = args.account or os.environ.get("DOCSPELL_ACCOUNT")
    if not account:
        account = input("Docspell account: ").strip()
    password = os.environ.get("DOCSPELL_PASSWORD")
    if password is None:
        password = getpass.getpass("Docspell password: ")
    return account, password


def login(base_url: str, args: argparse.Namespace) -> str:
    token = os.environ.get("DOCSPELL_TOKEN")
    if token:
        return token
    account, password = prompt_credentials(args)
    response = request_json(
        "POST",
        api_url(base_url, "/open/auth/login"),
        body={"account": account, "password": password},
    )
    if not response.get("success"):
        message = response.get("message", "login failed")
        raise RuntimeError(f"Docspell login failed: {message}")
    token = response.get("token")
    if not token:
        raise RuntimeError("Docspell login response did not contain an auth token.")
    return token


def check_version(base_url: str) -> dict[str, Any]:
    urls = [
        f"{base_url.rstrip('/')}/api/info/version",
        api_url(base_url, "/api/info/version"),
    ]
    last_error: Exception | None = None
    for url in urls:
        try:
            return request_json("GET", url, timeout=20)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Version check failed: {last_error}")


# ---------------------------------------------------------------------------
# Item detail
# ---------------------------------------------------------------------------


def get_item_detail(base_url: str, token: str, item_id: str) -> dict[str, Any] | None:
    try:
        return request_json("GET", api_url(base_url, f"/sec/item/{item_id}"), token=token)
    except RuntimeError:
        return None


def extract_date_ms(detail: dict[str, Any] | None) -> int:
    """Return the item date as ms-since-epoch. Prefer itemDate, fall back to created."""
    if not detail:
        return 0
    for key in ("itemDate", "created", "updated"):
        value = detail.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return 0


def extract_attachment_count(detail: dict[str, Any] | None) -> int:
    if not detail:
        return 0
    attachments = detail.get("attachments")
    if isinstance(attachments, list):
        return len(attachments)
    sources = detail.get("sources")
    if isinstance(sources, list):
        return len(sources)
    return 0


def format_ts(ms: int) -> str:
    if not ms:
        return "(no-date)"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ms / 1000.0))
    except (OverflowError, ValueError, OSError):
        return "(bad-date)"


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def load_items(csv_path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise RuntimeError(f"Empty or unreadable CSV: {csv_path}")
        for row in reader:
            item_id = (row.get("item_id") or "").strip()
            title = (row.get("title") or "").strip()
            if item_id and title:
                rows.append({"item_id": item_id, "title": title})
    return rows


def find_duplicate_groups(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    """Group item_ids by case-folded title. Return only groups with >1 entry."""
    groups: dict[str, list[str]] = {}
    title_display: dict[str, str] = {}
    for r in rows:
        key = r["title"].casefold()
        title_display.setdefault(key, r["title"])
        groups.setdefault(key, []).append(r["item_id"])
    return {
        title_display[k]: ids
        for k, ids in groups.items()
        if len(ids) > 1
    }


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def delete_item(base_url: str, token: str, item_id: str) -> None:
    request_json(
        "DELETE",
        api_url(base_url, f"/sec/item/{item_id}"),
        token=token,
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect and (optionally) delete duplicate Docspell items by title."
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("DOCSPELL_URL", DEFAULT_URL),
        help="Docspell base URL",
    )
    parser.add_argument(
        "--account", help="Docspell account; default prompts or DOCSPELL_ACCOUNT"
    )
    parser.add_argument("--csv", default=DEFAULT_CSV, help="Items CSV path")
    parser.add_argument("--plan", default=DEFAULT_PLAN_CSV, help="Plan output CSV path")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete duplicates. Requires --confirm and an interactive prompt.",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help=f"Must equal '{CONFIRM_PHRASE}' together with --apply to enable deletes",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="(USE WITH CARE) skip the interactive second confirmation prompt",
    )
    return parser.parse_args()


def resolve_csv(path_str: str) -> Path:
    p = Path(path_str)
    if not p.is_absolute():
        p = Path.cwd() / p
    if p.exists():
        return p
    alt = Path(__file__).resolve().parent / path_str
    if alt.exists():
        return alt
    raise FileNotFoundError(path_str)


def main() -> int:
    args = parse_args()

    if args.apply and args.confirm != CONFIRM_PHRASE:
        print(
            f"--apply requires --confirm {CONFIRM_PHRASE}. Refusing to delete.",
            file=sys.stderr,
        )
        return 2

    dry_run = not args.apply
    base_url = args.url.rstrip("/")

    try:
        csv_path = resolve_csv(args.csv)
    except FileNotFoundError:
        print(f"CSV not found: {args.csv}", file=sys.stderr)
        return 1

    items = load_items(csv_path)
    duplicate_groups = find_duplicate_groups(items)

    version = check_version(base_url)
    print(f"Docspell URL:      {base_url}")
    print(f"Docspell version:  {version.get('version', 'unknown')}")
    print(f"Mode:              {'APPLY (DELETE)' if not dry_run else 'dry-run'}")
    print(f"CSV:               {csv_path}")
    print(f"Total items in CSV:{len(items)}")
    print(f"Duplicate groups:  {len(duplicate_groups)}")
    print()

    if not duplicate_groups:
        print("No duplicates detected. Nothing to do.")
        return 0

    # We always need to authenticate to fetch dates/attachments for ranking.
    token = login(base_url, args)

    # Build the per-group plan with API-fetched metadata.
    plan_rows: list[dict[str, Any]] = []
    for title, item_ids in sorted(duplicate_groups.items(), key=lambda kv: kv[0].casefold()):
        members: list[dict[str, Any]] = []
        for iid in item_ids:
            detail = get_item_detail(base_url, token, iid)
            members.append(
                {
                    "item_id": iid,
                    "date_ms": extract_date_ms(detail),
                    "attachments": extract_attachment_count(detail),
                }
            )
        # Keep the OLDEST (lowest date_ms > 0). If all dates are 0, keep the
        # first item_id sorted alphabetically for determinism.
        dated = [m for m in members if m["date_ms"] > 0]
        if dated:
            keeper = min(dated, key=lambda m: m["date_ms"])
        else:
            keeper = sorted(members, key=lambda m: m["item_id"])[0]
        for m in members:
            m["role"] = "keep" if m["item_id"] == keeper["item_id"] else "delete"
        plan_rows.append({"title": title, "members": members})

    # Print plan
    print("Duplicate plan (older = keeper):")
    print()
    to_delete: list[tuple[str, str]] = []  # (title, item_id)
    for group in plan_rows:
        print(f"  Title: {group['title']}")
        for m in sorted(group["members"], key=lambda x: (x["role"] != "keep", x["date_ms"])):
            marker = "KEEP  " if m["role"] == "keep" else "DELETE"
            print(
                f"    [{marker}] {m['item_id'][:12]}…  "
                f"date={format_ts(m['date_ms'])}  "
                f"attachments={m['attachments']}"
            )
            if m["role"] == "delete":
                to_delete.append((group["title"], m["item_id"]))
        print()

    print(f"Total items to delete: {len(to_delete)}")
    print()

    # Write plan CSV
    plan_path = Path(args.plan)
    if not plan_path.is_absolute():
        plan_path = Path.cwd() / plan_path
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    with plan_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["title", "item_id", "role", "date", "date_ms", "attachments"],
        )
        writer.writeheader()
        for group in plan_rows:
            for m in group["members"]:
                writer.writerow(
                    {
                        "title": group["title"],
                        "item_id": m["item_id"],
                        "role": m["role"],
                        "date": format_ts(m["date_ms"]),
                        "date_ms": m["date_ms"],
                        "attachments": m["attachments"],
                    }
                )
    print(f"Plan written to: {plan_path}")

    if dry_run:
        print()
        print("Dry-run complete. No deletes performed.")
        print(f"To actually delete, rerun with:  --apply --confirm {CONFIRM_PHRASE}")
        return 0

    # Second-step interactive confirmation
    if not args.no_prompt:
        print()
        print("!!! DESTRUCTIVE OPERATION !!!")
        print(
            f"About to permanently delete {len(to_delete)} item(s) from "
            f"{base_url}."
        )
        print(
            f"To proceed, type exactly: {CONFIRM_PHRASE}"
        )
        try:
            typed = input("> ").strip()
        except EOFError:
            typed = ""
        if typed != CONFIRM_PHRASE:
            print("Confirmation phrase did not match. Aborting.", file=sys.stderr)
            return 2

    # Apply
    ok = 0
    failed = 0
    for title, item_id in to_delete:
        try:
            delete_item(base_url, token, item_id)
            print(f"  [del]  {item_id[:12]}…  {title}")
            ok += 1
        except Exception as exc:
            print(f"  [FAIL] {item_id[:12]}…  {title}: {redact(str(exc))}")
            failed += 1

    print()
    print(f"Delete complete: ok={ok}, failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"Error: {redact(str(exc))}", file=sys.stderr)
        raise SystemExit(1)
