#!/usr/bin/env python3
"""Apply reviewed Docspell triage actions — safe Library batch only.

This script reads ``out/docspell-safe-library-actions.csv`` (produced by the
review step) and, for rows where ``review_decision == safe_library_folder``,
moves the item into the target folder (default ``Library``) and adds the
tags listed in ``safe_add_tags``.

Defaults to dry-run. To change anything in Docspell you must pass BOTH:

    --apply
    --confirm APPLY-LIBRARY

Endpoints used:

    GET  /api/info/version
    POST /open/auth/login
    GET  /sec/folder
    POST /sec/folder              (only with --apply, if Library missing)
    GET  /sec/tag
    POST /sec/tag                 (only with --apply, if a target tag missing)
    GET  /sec/item/{id}           (read-only, to see current folder/tags)
    PUT  /sec/item/{id}/folder    (only with --apply)
    POST /sec/item/{id}/tags      (only with --apply — adds, never replaces)

The script never calls delete, attachment download, OCR / text extraction
or item-confirm endpoints. It never prints passwords, OTPs, session
tokens, cookies, or auth headers. Existing tags on items are preserved.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

from _docspell_common import (
    Progress,
    Session,
    Summary,
    api_url,
    dns_preflight,
    err_to_log,
    load_processed_ids,
    redact,
    version_check,
    version_warn,
)


DEFAULT_URL = "https://docspell.medarov.net"
DEFAULT_CSV = "out/docspell-name-classification.csv"
DEFAULT_LOG = "out/apply-log.csv"
DEFAULT_FOLDER = "Archive"
# Accept either the original review-CSV vocabulary ("safe_library_folder")
# or the newer name-based classifier vocabulary ("classified").
DEFAULT_DECISIONS = ("classified", "safe_library_folder")
CONFIRM_PHRASE = "APPLY-LIBRARY"


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------


def parse_tags(value: str) -> list[tuple[str, str | None]]:
    """Parse a semicolon/comma-separated tag list.

    Each entry may use Docspell-native category syntax ``category:name``
    (e.g. ``doctype:book``). Returns ``[(name, category_or_None), ...]``.
    """
    if not value:
        return []
    out: list[tuple[str, str | None]] = []
    for raw in re.split(r"[;,|]", value):
        raw = raw.strip()
        if not raw:
            continue
        if ":" in raw:
            category, _, name = raw.partition(":")
            category = category.strip() or None
            name = name.strip()
        else:
            category = None
            name = raw
        if name:
            out.append((name, category))
    return out


def load_safe_rows(
    csv_path: Path,
    accepted_decisions: set[str],
) -> tuple[list[dict[str, str]], int, int, list[str]]:
    """Read the reviewed CSV. Returns (safe rows, skipped_manual, skipped_other, columns)."""
    rows: list[dict[str, str]] = []
    skipped_manual = 0
    skipped_other = 0
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise RuntimeError(f"Empty or unreadable CSV: {csv_path}")
        required = {
            "item_id",
            "review_decision",
            "safe_suggested_folder",
            "safe_add_tags",
        }
        missing = required - set(reader.fieldnames)
        if missing:
            raise RuntimeError(
                "CSV missing required columns: "
                + ", ".join(sorted(missing))
                + ". Got: "
                + ", ".join(reader.fieldnames)
            )
        for row in reader:
            decision = (row.get("review_decision") or "").strip()
            if decision in accepted_decisions:
                rows.append(row)
            elif decision in ("manual_review", "needs_review"):
                skipped_manual += 1
            else:
                skipped_other += 1
        columns = list(reader.fieldnames)
    return rows, skipped_manual, skipped_other, columns


def build_plan(
    rows: list[dict[str, str]], default_folder: str
) -> tuple[list[dict[str, Any]], list[tuple[str, str | None]]]:
    plan: list[dict[str, Any]] = []
    needed_tags: dict[tuple[str, str | None], None] = {}
    seen_ids: set[str] = set()
    for row in rows:
        item_id = (row.get("item_id") or "").strip()
        if not item_id:
            continue
        if item_id in seen_ids:
            # Defensive — don't process the same item twice in one batch.
            continue
        seen_ids.add(item_id)
        title = (row.get("title") or "").strip()
        folder_name = (row.get("safe_suggested_folder") or "").strip() or default_folder
        tags = parse_tags(row.get("safe_add_tags") or "")
        for tag in tags:
            needed_tags[tag] = None
        plan.append(
            {
                "item_id": item_id,
                "title": title,
                "target_folder": folder_name,
                "target_tags": tags,
            }
        )
    # Sorted by (category, name) for stable output.
    return plan, sorted(needed_tags.keys(), key=lambda t: ((t[1] or "").casefold(), t[0].casefold()))


# ---------------------------------------------------------------------------
# Folders and tags
# ---------------------------------------------------------------------------


def _extract_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("items", "folders", "tags"):
            items = data.get(key)
            if isinstance(items, list):
                return [i for i in items if isinstance(i, dict)]
    if isinstance(data, list):
        return [i for i in data if isinstance(i, dict)]
    return []


def list_folders(session: Session) -> list[dict[str, Any]]:
    return _extract_items(session.request("GET", "/sec/folder?query="))


def list_tags(session: Session) -> list[dict[str, Any]]:
    return _extract_items(session.request("GET", "/sec/tag?q="))


def find_by_name(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    target = name.strip().casefold()
    for item in items:
        if str(item.get("name", "")).strip().casefold() == target:
            return item
    return None


def _id_from_create_result(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("id", "newId", "value"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def ensure_folder(
    session: Session, name: str, *, dry_run: bool
) -> tuple[str | None, bool]:
    """Return (folder_id, would_create_or_created). folder_id is None only in dry-run create path."""
    folder = find_by_name(list_folders(session), name)
    if folder:
        return folder.get("id"), False
    if dry_run:
        return None, True
    result = session.request("POST", "/sec/folder", body={"name": name})
    folder_id = _id_from_create_result(result)
    if not folder_id:
        # Fall back to re-listing.
        folder = find_by_name(list_folders(session), name)
        folder_id = folder.get("id") if folder else None
    if not folder_id:
        raise RuntimeError(f"Could not determine ID for folder '{name}' after create.")
    return folder_id, True


def _tag_key(name: str, category: str | None) -> str:
    """Compose a stable lookup key. Tags are unique by (name, category)."""
    return f"{(category or '').casefold()}|{name.casefold()}"


def ensure_tag(
    session: Session,
    tag_index: dict[str, dict[str, Any]],
    name: str,
    category: str | None,
    *,
    dry_run: bool,
) -> tuple[str | None, bool]:
    key = _tag_key(name, category)
    existing = tag_index.get(key)
    if existing:
        return existing["id"], False
    # Some legacy tags may exist by name with no category. Be lenient on a
    # second lookup if we can't find an exact category match.
    if category:
        fallback = tag_index.get(_tag_key(name, None))
        if fallback:
            return fallback["id"], False
    if dry_run:
        return None, True
    # Docspell 0.43.0 requires id="" and created=0 on tag create — bare
    # {name, category} returns HTTP 500.
    result = session.request(
        "POST",
        "/sec/tag",
        body={"id": "", "name": name, "category": category, "created": 0},
    )
    tag_id = _id_from_create_result(result)
    if not tag_id:
        # Re-list and pick the matching tag.
        for tag in list_tags(session):
            tname = str(tag.get("name", "")).strip()
            tcat = tag.get("category")
            if (
                tname.casefold() == name.casefold()
                and ((tcat or "").casefold() == (category or "").casefold())
            ):
                tag_id = tag.get("id")
                break
    if not tag_id:
        raise RuntimeError(
            f"Could not determine ID for tag '{name}' (category={category!r}) after create."
        )
    tag_index[key] = {"id": tag_id, "name": name, "category": category}
    return tag_id, True


# ---------------------------------------------------------------------------
# Item read + update
# ---------------------------------------------------------------------------


def get_item_detail(session: Session, item_id: str) -> dict[str, Any] | None:
    """Read-only fetch of current folder + tag names for an item."""
    try:
        return session.request("GET", f"/sec/item/{item_id}")
    except RuntimeError:
        # Fall back to search by id, in case some deployments restrict /sec/item/{id}.
        params = urllib.parse.urlencode(
            {
                "q": f"id:{item_id}",
                "limit": 1,
                "offset": 0,
                "withDetails": "true",
                "searchMode": "normal",
            }
        )
        data = session.request("GET", f"/sec/item/search?{params}")
        for group in (data.get("groups") or []):
            for item in (group.get("items") or []):
                if isinstance(item, dict):
                    return item
        return None


def current_state(detail: dict[str, Any] | None) -> tuple[str, set[str]]:
    if not detail:
        return "", set()
    folder = detail.get("folder")
    folder_name = ""
    if isinstance(folder, dict):
        folder_name = str(folder.get("name") or "").strip()
    elif isinstance(folder, str):
        folder_name = folder.strip()
    tag_names: set[str] = set()
    for tag in detail.get("tags", []) or []:
        if isinstance(tag, dict) and tag.get("name"):
            tag_names.add(str(tag["name"]))
        elif isinstance(tag, str):
            tag_names.add(tag)
    return folder_name, tag_names


def set_item_folder(session: Session, item_id: str, folder_id: str) -> None:
    session.request("PUT", f"/sec/item/{item_id}/folder", body={"id": folder_id})


def add_item_tags(session: Session, item_id: str, tag_ids: list[str]) -> None:
    """Link tags to an item additively.

    Docspell 0.43.0 has a bug where POST /sec/item/{id}/tags returns 500.
    The working alternative is PUT /sec/item/{id}/taglink which accepts
    tag IDs (or names) in the same {"items": [...]} body and adds them
    without removing existing tags.
    """
    if not tag_ids:
        return
    session.request("PUT", f"/sec/item/{item_id}/taglink", body={"items": tag_ids})


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply reviewed Docspell triage actions (safe Library batch only)."
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("DOCSPELL_URL", DEFAULT_URL),
        help="Docspell base URL",
    )
    parser.add_argument(
        "--account", help="Docspell account; default prompts or DOCSPELL_ACCOUNT"
    )
    parser.add_argument("--csv", default=DEFAULT_CSV, help="Reviewed actions CSV path")
    parser.add_argument("--log", default=DEFAULT_LOG, help="Apply log output CSV path")
    parser.add_argument(
        "--folder",
        default=DEFAULT_FOLDER,
        help=f"Default target folder name (default: {DEFAULT_FOLDER})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes. Still requires --confirm.",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help=f"Must equal '{CONFIRM_PHRASE}' together with --apply to enable writes",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum items to process (0 = all)",
    )
    parser.add_argument(
        "--start-from",
        default="",
        help="Skip rows until (and excluding) this item_id is reached. "
        "Useful for resuming after a manual interruption.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip items already marked ok/set/already-set in an existing "
        "log at --log (idempotent re-run). Default off.",
    )
    parser.add_argument(
        "--decision",
        action="append",
        default=None,
        help=(
            "Which review_decision values to act on. Repeatable. "
            f"Default: {', '.join(DEFAULT_DECISIONS)}"
        ),
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

    # Safety gates
    if args.apply and args.confirm != CONFIRM_PHRASE:
        print(
            f"--apply requires --confirm {CONFIRM_PHRASE}. Refusing to make changes.",
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

    # DNS preflight (clear error before any HTTP) — shared helper.
    dns_preflight(base_url)

    accepted = set(args.decision) if args.decision else set(DEFAULT_DECISIONS)
    rows, skipped_manual, skipped_other, _columns = load_safe_rows(csv_path, accepted)
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]
    plan, needed_tag_names = build_plan(rows, args.folder)

    # --start-from: drop everything up to and including the matched item_id.
    if args.start_from:
        idx_match = next(
            (i for i, e in enumerate(plan) if e["item_id"] == args.start_from),
            None,
        )
        if idx_match is None:
            print(
                f"--start-from {args.start_from[:12]}… not found in plan; "
                f"continuing from the top.",
                file=sys.stderr,
            )
        else:
            print(
                f"--start-from: resuming after index {idx_match} "
                f"(item_id={args.start_from[:12]}…)"
            )
            plan = plan[idx_match + 1 :]

    version = version_check(base_url)
    version_str = version.get("version", "unknown")
    print(f"Docspell URL:     {base_url}")
    print(f"Docspell version: {version_str}")
    version_warn(version_str)
    print(f"Mode:             {'APPLY' if not dry_run else 'dry-run'}")
    print(f"CSV:              {csv_path}")
    print(f"Accepted reviews: {', '.join(sorted(accepted))}")
    print(f"Rows planned:     {len(plan)}")
    print(f"Skipped review:   {skipped_manual}")
    print(f"Skipped other:    {skipped_other}")
    if not plan:
        print("Nothing to do.")
        return 0

    session = Session.from_args(base_url, args, log=lambda m: print(f"  [net] {m}"))

    # Resolve / create the target folder.
    target_folders = sorted({entry["target_folder"] for entry in plan})
    folder_ids: dict[str, str] = {}
    for folder_name in target_folders:
        folder_id, mutated = ensure_folder(session, folder_name, dry_run=dry_run)
        if mutated and dry_run:
            print(f"[dry-run] Would create folder: {folder_name}")
        elif mutated:
            print(f"Created folder: {folder_name} (id={folder_id})")
        else:
            print(f"Folder ready:   {folder_name} (id={folder_id})")
        if folder_id:
            folder_ids[folder_name] = folder_id

    # Resolve / create tags. Tags are keyed by (name, category).
    existing_tags = list_tags(session)
    tag_index: dict[str, dict[str, Any]] = {}
    for tag in existing_tags:
        name = str(tag.get("name", "")).strip()
        category = tag.get("category")
        if isinstance(category, str):
            category = category.strip() or None
        tag_id = tag.get("id")
        if name and tag_id:
            tag_index[_tag_key(name, category)] = {
                "id": tag_id,
                "name": name,
                "category": category,
            }

    tag_id_by_key: dict[str, str] = {}
    tags_to_create: list[tuple[str, str | None]] = []
    for name, category in needed_tag_names:
        key = _tag_key(name, category)
        if key in tag_index:
            tag_id_by_key[key] = tag_index[key]["id"]
            continue
        # If the user already has a category-less tag with this name, reuse it
        # rather than creating a duplicate.
        legacy_key = _tag_key(name, None)
        if category and legacy_key in tag_index:
            tag_id_by_key[key] = tag_index[legacy_key]["id"]
            continue
        tags_to_create.append((name, category))
    if tags_to_create and dry_run:
        for name, category in tags_to_create:
            label = f"{category}:{name}" if category else name
            print(f"[dry-run] Would create tag: {label}")
    elif tags_to_create:
        for name, category in tags_to_create:
            tag_id, _ = ensure_tag(session, tag_index, name, category, dry_run=False)
            if tag_id:
                tag_id_by_key[_tag_key(name, category)] = tag_id
                label = f"{category}:{name}" if category else name
                print(f"Created tag:    {label} (id={tag_id})")

    # Dry-run plan summary
    print()
    print("Plan summary:")
    print(f"  Items to update: {len(plan)}")
    print(f"  Target folders : {', '.join(target_folders)}")
    print(
        "  Required tags  : "
        + (
            ", ".join(
                f"{c}:{n}" if c else n for n, c in needed_tag_names
            )
            if needed_tag_names
            else "(none)"
        )
    )
    print("  First 20 titles:")
    for entry in plan[:20]:
        short = entry["item_id"][:8]
        print(f"    - [{short}…] {entry['title'][:120]}")

    if dry_run:
        print()
        print("Dry-run complete. No changes made.")
        print(
            f"To apply, rerun with:  --apply --confirm {CONFIRM_PHRASE}"
        )
        return 0

    # ---- APPLY ----
    log_path = Path(args.log)
    if not log_path.is_absolute():
        log_path = Path.cwd() / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # --resume: drop items already successfully processed in an existing log.
    resumed_set: set[str] = set()
    if args.resume:
        resumed_set = load_processed_ids(
            str(log_path),
            item_id_field="item_id",
            status_fields=("folder_status",),
        )
        if resumed_set:
            before = len(plan)
            plan = [e for e in plan if e["item_id"] not in resumed_set]
            print(
                f"--resume: skipping {before - len(plan)} item(s) already "
                f"completed in prior run ({log_path.name})."
            )

    # In resume mode we append to the existing log; otherwise we start fresh.
    open_mode = "a" if (args.resume and log_path.exists()) else "w"
    needs_header = open_mode == "w"

    print()
    print(f"Applying changes. Log: {log_path}")
    fieldnames = [
        "timestamp",
        "item_id",
        "title",
        "target_folder",
        "folder_status",
        "tags_added",
        "tags_skipped_existing",
        "error",
    ]
    summary = Summary(log_path=str(log_path))
    progress = Progress(len(plan), prefix="  ")
    retried_ids: set[str] = set()

    def _on_net(msg: str) -> None:
        # Each network log line implies a retry of some kind.
        # We treat any "backoff … retry" event as a retry signal.
        if "retry" in msg or "401" in msg:
            # Attribute to the currently-processing item if known.
            current = getattr(_on_net, "current", None)
            if current:
                retried_ids.add(current)
        # Avoid scrambling the TTY progress line with the message.
        sys.stdout.write("\n" + redact(msg) + "\n")
        sys.stdout.flush()

    session.log = _on_net

    with log_path.open(open_mode, newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        if needs_header:
            writer.writeheader()

        for entry in plan:
            item_id = entry["item_id"]
            _on_net.current = item_id  # type: ignore[attr-defined]
            title = entry["title"]
            target_folder = entry["target_folder"]
            folder_id = folder_ids.get(target_folder)

            folder_status = ""
            tags_added: list[str] = []
            tags_skipped_existing: list[str] = []
            error = ""

            try:
                detail = get_item_detail(session, item_id)
                cur_folder, cur_tags = current_state(detail)
                cur_tags_cf = {n.casefold() for n in cur_tags}

                # Folder
                if not folder_id:
                    raise RuntimeError(f"Missing folder id for '{target_folder}'")
                if cur_folder and cur_folder.casefold() == target_folder.casefold():
                    folder_status = "already-set"
                else:
                    set_item_folder(session, item_id, folder_id)
                    folder_status = "set"

                # Tags — only add ones not already present on the item.
                # Existing tags on items are matched by NAME only (the item
                # detail endpoint doesn't always return category alongside).
                tag_ids_to_send: list[str] = []
                for name, category in entry["target_tags"]:
                    label = f"{category}:{name}" if category else name
                    if name.casefold() in cur_tags_cf:
                        tags_skipped_existing.append(label)
                        continue
                    tid = tag_id_by_key.get(_tag_key(name, category))
                    if not tid:
                        raise RuntimeError(f"Tag id missing for '{label}'")
                    tag_ids_to_send.append(tid)
                    tags_added.append(label)
                if tag_ids_to_send:
                    add_item_tags(session, item_id, tag_ids_to_send)

                summary.ok += 1
                progress.tick(ok=True)
            except Exception as exc:
                error = err_to_log(exc, max_body=80)
                summary.failed += 1
                progress.tick(failed=True)

            writer.writerow(
                {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "item_id": item_id,
                    "title": title,
                    "target_folder": target_folder,
                    "folder_status": folder_status,
                    "tags_added": ";".join(tags_added),
                    "tags_skipped_existing": ";".join(tags_skipped_existing),
                    "error": error,
                }
            )

    progress.done()
    summary.total = progress.processed
    summary.skipped = len(resumed_set)
    summary.retried = len(retried_ids)
    summary.elapsed = progress.elapsed
    summary.print()

    if summary.failed:
        print(
            "\nSome rows failed. Inspect the log; rerunning is safe — "
            "items already in the target folder will be reported as 'already-set' "
            "and existing tags will be skipped. You can also pass --resume "
            "to skip rows that already succeeded."
        )
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"Error: {redact(str(exc))}", file=sys.stderr)
        raise SystemExit(1)
