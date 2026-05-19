#!/usr/bin/env python3
"""Apply external-metadata enrichment as Docspell custom fields.

Reads docspell_book_system_enriched/out/books-enriched/book-enrichment.csv
and, for every item with a strong enrichment match (configurable threshold,
default 0.78), sets the following custom fields on the corresponding item:

  book_year       (numeric)   first publication year
  book_isbn       (text)      ISBN-13
  book_publisher  (text)      publisher name
  book_author     (text)      first author
  book_source     (text)      'openlibrary' or 'googlebooks'

Default mode is dry-run. To make any changes, pass BOTH:

    --apply
    --confirm APPLY-ENRICHMENT

The script is idempotent — re-running won't duplicate fields or overwrite
existing non-empty values unless --overwrite is passed.

Endpoints used:

  POST /open/auth/login                          (interactive login)
  GET  /sec/customfield                          (list existing fields)
  POST /sec/customfield                          (create missing fields)
  PUT  /sec/item/{id}/customfield                (set value per item)

The script never calls delete or attachment-touching endpoints.
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
    load_processed_ids,
    redact,
    version_check,
    version_warn,
)


DEFAULT_URL = "https://docspell.medarov.net"
DEFAULT_CSV = "docspell_book_system_enriched/out/books-enriched/book-enrichment.csv"
DEFAULT_LOG = "out/apply-enrichment-log.csv"
CONFIRM_PHRASE = "APPLY-ENRICHMENT"


# Custom field definitions to ensure.
# Docspell field types: text, numeric, money, bool, date
CUSTOM_FIELDS: list[dict[str, str]] = [
    {"name": "book_year",      "label": "Year",      "ftype": "numeric"},
    {"name": "book_isbn",      "label": "ISBN",      "ftype": "text"},
    {"name": "book_publisher", "label": "Publisher", "ftype": "text"},
    {"name": "book_author",    "label": "Author",    "ftype": "text"},
    {"name": "book_source",    "label": "Metadata source", "ftype": "text"},
]


# ---------------------------------------------------------------------------
# Custom field management
# ---------------------------------------------------------------------------


def list_custom_fields(session: Session) -> list[dict[str, Any]]:
    data = session.request("GET", "/sec/customfield?query=")
    if isinstance(data, dict):
        return data.get("items", []) or []
    if isinstance(data, list):
        return data
    return []


def _id_from_create_result(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    for k in ("id", "newId", "value"):
        v = data.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def ensure_custom_field(
    session: Session,
    name: str,
    label: str,
    ftype: str,
    *,
    dry_run: bool,
    existing_by_name: dict[str, dict[str, Any]],
) -> tuple[str | None, str]:
    """Return (field_id, action) where action is 'reused' | 'created' | 'would-create'."""
    if name.lower() in existing_by_name:
        return existing_by_name[name.lower()]["id"], "reused"
    if dry_run:
        return None, "would-create"
    body = {"id": "", "name": name, "label": label, "ftype": ftype, "created": 0}
    resp = session.request("POST", "/sec/customfield", body=body)
    fid = _id_from_create_result(resp)
    if not fid:
        # Refresh and look up by name
        for f in list_custom_fields(session):
            if str(f.get("name", "")).lower() == name.lower():
                fid = f.get("id")
                break
    if not fid:
        raise RuntimeError(f"Could not determine id for custom field '{name}'")
    existing_by_name[name.lower()] = {"id": fid, "name": name, "label": label, "ftype": ftype}
    return fid, "created"


def set_item_custom_field(
    session: Session, item_id: str, field_id_or_name: str, value: Any
) -> None:
    """Set or update a custom field value on an item.

    Docspell endpoint: PUT /sec/item/{itemId}/customfield
    Body: {"field": <field-id-or-name>, "value": "<string>"}
    """
    body = {"field": field_id_or_name, "value": str(value)}
    session.request("PUT", f"/sec/item/{item_id}/customfield", body=body)


def get_item_field_values(session: Session, item_id: str) -> dict[str, str]:
    """Return {field_name_or_id_lower: current_value_str} for an item."""
    detail = session.request("GET", f"/sec/item/{item_id}")
    out: dict[str, str] = {}
    for cf in detail.get("customfields", []) or []:
        name = (cf.get("name") or "").lower()
        if name:
            out[name] = str(cf.get("value", ""))
    return out


# ---------------------------------------------------------------------------
# CSV reading
# ---------------------------------------------------------------------------


def load_enrichment_rows(csv_path: Path, min_score: float):
    rows = []
    skipped_low = 0
    skipped_blank = 0
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise RuntimeError("Empty CSV")
        required = {"item_id", "enrichment_source", "enrichment_match_score",
                    "enrichment_title", "enrichment_authors",
                    "enrichment_year", "enrichment_publisher", "enrichment_isbn13"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise RuntimeError(f"CSV missing columns: {sorted(missing)}")
        for r in reader:
            try:
                score = float(r.get("enrichment_match_score") or 0)
            except ValueError:
                score = 0
            src = (r.get("enrichment_source") or "").strip()
            if not src:
                skipped_blank += 1
                continue
            if score < min_score:
                skipped_low += 1
                continue
            rows.append(r)
    return rows, skipped_low, skipped_blank


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def parse_args():
    p = argparse.ArgumentParser(
        description="Apply external-metadata enrichment as Docspell custom fields."
    )
    p.add_argument("--url", default=os.environ.get("DOCSPELL_URL", DEFAULT_URL))
    p.add_argument("--account", help="Docspell account (or DOCSPELL_ACCOUNT)")
    p.add_argument("--csv", default=DEFAULT_CSV, help="enrichment CSV path")
    p.add_argument("--log", default=DEFAULT_LOG, help="log file path")
    p.add_argument("--min-score", type=float, default=0.78,
                   help="minimum enrichment_match_score to apply (default 0.78)")
    p.add_argument("--apply", action="store_true",
                   help="actually write changes (also requires --confirm)")
    p.add_argument("--confirm", default="",
                   help=f"must equal '{CONFIRM_PHRASE}' with --apply")
    p.add_argument("--overwrite", action="store_true",
                   help="overwrite existing non-empty custom-field values")
    p.add_argument("--limit", type=int, default=0,
                   help="max items to process (0 = all)")
    p.add_argument("--start-from", default="",
                   help="Skip rows until (and excluding) this item_id is "
                        "reached. Useful for resuming after a manual interrupt.")
    p.add_argument("--resume", action="store_true",
                   help="Skip items already marked 'set'/'already-set'/"
                        "'skipped-existing' in an existing log at --log.")
    return p.parse_args()


def resolve_csv(path_str):
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
        print(f"--apply requires --confirm {CONFIRM_PHRASE}.", file=sys.stderr)
        return 2
    dry_run = not args.apply
    base_url = args.url.rstrip("/")

    # DNS preflight (shared helper — exits 2 on failure with Tailscale hint).
    dns_preflight(base_url)

    try:
        csv_path = resolve_csv(args.csv)
    except FileNotFoundError:
        print(f"CSV not found: {args.csv}", file=sys.stderr)
        return 1

    rows, skipped_low, skipped_blank = load_enrichment_rows(csv_path, args.min_score)
    if args.limit > 0:
        rows = rows[: args.limit]

    if args.start_from:
        idx = next((i for i, r in enumerate(rows) if r["item_id"] == args.start_from), None)
        if idx is None:
            print(
                f"--start-from {args.start_from[:12]}… not found; continuing from top.",
                file=sys.stderr,
            )
        else:
            print(f"--start-from: resuming after index {idx}")
            rows = rows[idx + 1 :]

    version = version_check(base_url)
    version_str = version.get("version", "unknown")
    print(f"Docspell URL:     {base_url}")
    print(f"Docspell version: {version_str}")
    version_warn(version_str)
    print(f"Mode:             {'APPLY' if not dry_run else 'dry-run'}")
    print(f"CSV:              {csv_path}")
    print(f"Min score:        {args.min_score}")
    print(f"Rows to process:  {len(rows)}")
    print(f"Skipped low-score:{skipped_low}")
    print(f"Skipped blank:    {skipped_blank}")
    print(f"Overwrite values: {args.overwrite}")
    if not rows:
        print("Nothing to do.")
        return 0

    session = Session.from_args(base_url, args)

    # Resolve / create custom fields
    existing = list_custom_fields(session)
    existing_by_name: dict[str, dict[str, Any]] = {}
    for f in existing:
        nm = str(f.get("name", "")).lower()
        if nm:
            existing_by_name[nm] = {
                "id": f.get("id"),
                "name": f.get("name"),
                "label": f.get("label"),
                "ftype": f.get("ftype"),
            }

    field_ids: dict[str, str | None] = {}
    for spec in CUSTOM_FIELDS:
        fid, action = ensure_custom_field(
            session, spec["name"], spec["label"], spec["ftype"],
            dry_run=dry_run, existing_by_name=existing_by_name,
        )
        if action == "would-create":
            print(f"[dry-run] Would create custom field: {spec['name']} ({spec['ftype']})")
        elif action == "created":
            print(f"Created custom field: {spec['name']} (id={fid})")
        else:
            print(f"Custom field ready:   {spec['name']} (id={fid})")
        field_ids[spec["name"]] = fid

    print()
    print("First 10 items to enrich:")
    for r in rows[:10]:
        print(f"  [{r['item_id'][:8]}…] score={r['enrichment_match_score']} -> "
              f"{(r['enrichment_title'] or '')[:60]} "
              f"({r['enrichment_year'] or '-'}, ISBN:{r['enrichment_isbn13'] or '-'})")

    if dry_run:
        print()
        print("Dry-run complete. No changes made.")
        print(f"To apply, rerun with: --apply --confirm {CONFIRM_PHRASE}")
        return 0

    # APPLY
    log_path = Path(args.log)
    if not log_path.is_absolute():
        log_path = Path.cwd() / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)

    resumed: set[str] = set()
    if args.resume:
        resumed = load_processed_ids(
            str(log_path),
            item_id_field="item_id",
            status_fields=("year_set", "isbn_set", "publisher_set", "author_set", "source_set"),
        )
        if resumed:
            before = len(rows)
            rows = [r for r in rows if r["item_id"] not in resumed]
            print(f"--resume: skipping {before - len(rows)} item(s) "
                  f"already completed in prior run ({log_path.name}).")

    open_mode = "a" if (args.resume and log_path.exists()) else "w"
    needs_header = open_mode == "w"

    print()
    print(f"Applying. Log: {log_path}")
    retried_ids: set[str] = set()

    def _on_net(msg: str) -> None:
        if "retry" in msg or "401" in msg:
            cur = getattr(_on_net, "current", None)
            if cur:
                retried_ids.add(cur)
        sys.stdout.write("\n" + redact(msg) + "\n")
        sys.stdout.flush()

    session.log = _on_net

    summary = Summary(log_path=str(log_path))
    progress = Progress(len(rows), prefix="  ")
    fieldnames = [
        "timestamp", "item_id", "title", "score",
        "year_set", "isbn_set", "publisher_set", "author_set", "source_set",
        "error",
    ]
    with log_path.open(open_mode, newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        if needs_header:
            w.writeheader()
        for r in rows:
            item_id = r["item_id"]
            _on_net.current = item_id  # type: ignore[attr-defined]
            title = r.get("original_title") or ""
            try:
                # If --overwrite is off, read current values and skip non-empty fields
                current: dict[str, str] = {}
                if not args.overwrite:
                    try:
                        current = get_item_field_values(session, item_id)
                    except Exception:
                        current = {}

                results: dict[str, str] = {}
                mapping = {
                    "book_year":      r.get("enrichment_year") or "",
                    "book_isbn":      r.get("enrichment_isbn13") or "",
                    "book_publisher": r.get("enrichment_publisher") or "",
                    "book_author":    r.get("enrichment_authors") or "",
                    "book_source":    r.get("enrichment_source") or "",
                }
                # Take only the first author if "; " separated
                if mapping["book_author"]:
                    mapping["book_author"] = mapping["book_author"].split(";")[0].strip()
                for fname, val in mapping.items():
                    val = (val or "").strip()
                    if not val:
                        results[fname] = ""
                        continue
                    # Skip if already set and not overwriting
                    if not args.overwrite and current.get(fname.lower()):
                        results[fname] = "skipped-existing"
                        continue
                    set_item_custom_field(session, item_id, fname, val)
                    results[fname] = "set"

                w.writerow({
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "item_id": item_id,
                    "title": title,
                    "score": r.get("enrichment_match_score"),
                    "year_set": results.get("book_year", ""),
                    "isbn_set": results.get("book_isbn", ""),
                    "publisher_set": results.get("book_publisher", ""),
                    "author_set": results.get("book_author", ""),
                    "source_set": results.get("book_source", ""),
                    "error": "",
                })
                summary.ok += 1
                progress.tick(ok=True)
            except Exception as exc:
                err = err_to_log(exc, max_body=80)
                w.writerow({
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "item_id": item_id, "title": title,
                    "score": r.get("enrichment_match_score"),
                    "year_set": "", "isbn_set": "", "publisher_set": "",
                    "author_set": "", "source_set": "",
                    "error": err,
                })
                summary.failed += 1
                progress.tick(failed=True)

    progress.done()
    summary.total = progress.processed
    summary.skipped = len(resumed)
    summary.retried = len(retried_ids)
    summary.elapsed = progress.elapsed
    summary.print()
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"Error: {redact(str(exc))}", file=sys.stderr)
        raise SystemExit(1)
