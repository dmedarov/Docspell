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
import os
import sys
import time
from pathlib import Path
from typing import Any

from _docspell_common import (
    Progress,
    Session,
    Summary,
    dns_preflight,
    err_to_log,
    redact,
    version_check,
    version_warn,
)


DEFAULT_URL = "https://docspell.medarov.net"
DEFAULT_CSV = "out/docspell-name-classification.csv"
DEFAULT_PLAN_CSV = "out/dedupe-plan.csv"
CONFIRM_PHRASE = "DEDUPE-DELETE"


# ---------------------------------------------------------------------------
# Item detail  (HTTP plumbing now lives in _docspell_common)
# ---------------------------------------------------------------------------


def get_item_detail(session: Session, item_id: str) -> dict[str, Any] | None:
    try:
        return session.request("GET", f"/sec/item/{item_id}")
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


def delete_item(session: Session, item_id: str) -> None:
    session.request("DELETE", f"/sec/item/{item_id}")


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
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Maximum number of duplicate ITEMS to delete (0 = all)",
    )
    parser.add_argument(
        "--start-from", default="",
        help="Skip duplicates until (and excluding) this item_id is reached. "
             "Useful for resuming after a manual interruption.",
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

    dns_preflight(base_url)

    try:
        csv_path = resolve_csv(args.csv)
    except FileNotFoundError:
        print(f"CSV not found: {args.csv}", file=sys.stderr)
        return 1

    items = load_items(csv_path)
    duplicate_groups = find_duplicate_groups(items)

    version = version_check(base_url)
    version_str = version.get("version", "unknown")
    print(f"Docspell URL:      {base_url}")
    print(f"Docspell version:  {version_str}")
    version_warn(version_str)
    print(f"Mode:              {'APPLY (DELETE)' if not dry_run else 'dry-run'}")
    print(f"CSV:               {csv_path}")
    print(f"Total items in CSV:{len(items)}")
    print(f"Duplicate groups:  {len(duplicate_groups)}")
    print()

    if not duplicate_groups:
        print("No duplicates detected. Nothing to do.")
        return 0

    # We always need to authenticate to fetch dates/attachments for ranking.
    session = Session.from_args(base_url, args)

    # Build the per-group plan with API-fetched metadata.
    plan_rows: list[dict[str, Any]] = []
    for title, item_ids in sorted(duplicate_groups.items(), key=lambda kv: kv[0].casefold()):
        members: list[dict[str, Any]] = []
        for iid in item_ids:
            detail = get_item_detail(session, iid)
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

    # Honor --start-from and --limit on the delete list (NOT on the plan CSV).
    if args.start_from:
        idx = next(
            (i for i, (_t, iid) in enumerate(to_delete) if iid == args.start_from),
            None,
        )
        if idx is None:
            print(
                f"--start-from {args.start_from[:12]}… not found in delete list; "
                f"proceeding from top.",
                file=sys.stderr,
            )
        else:
            print(f"--start-from: resuming after index {idx}")
            to_delete = to_delete[idx + 1 :]
    if args.limit and args.limit > 0:
        to_delete = to_delete[: args.limit]

    # Apply
    retried_ids: set[str] = set()

    def _on_net(msg: str) -> None:
        if "retry" in msg or "401" in msg:
            cur = getattr(_on_net, "current", None)
            if cur:
                retried_ids.add(cur)
        sys.stdout.write("\n" + redact(msg) + "\n")
        sys.stdout.flush()

    session.log = _on_net

    summary = Summary(log_path=str(plan_path))
    progress = Progress(len(to_delete), prefix="  ", print_every=5)
    for title, item_id in to_delete:
        _on_net.current = item_id  # type: ignore[attr-defined]
        try:
            delete_item(session, item_id)
            print(f"  [del]  {item_id[:12]}…  {title}")
            summary.ok += 1
            progress.tick(ok=True)
        except Exception as exc:
            print(f"  [FAIL] {item_id[:12]}…  {title}: {err_to_log(exc, max_body=80)}")
            summary.failed += 1
            progress.tick(failed=True)

    progress.done()
    summary.total = progress.processed
    summary.retried = len(retried_ids)
    summary.elapsed = progress.elapsed
    summary.print()
    return 1 if summary.failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"Error: {redact(str(exc))}", file=sys.stderr)
        raise SystemExit(1)
