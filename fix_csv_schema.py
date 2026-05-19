#!/usr/bin/env python3
"""Rewrite a docspell-name-classification CSV to match the live Docspell schema.

This script is OFFLINE (no network calls) and IDEMPOTENT: running it twice
on the same input yields the same output, and running it on already-migrated
data is a no-op that prints 'no changes needed'.

Two corrections:
1. safe_suggested_folder: Archive -> Library (Library folder is already in Docspell
   with 240 items applied; Archive would split the dataset).
2. safe_add_tags: classifier's `area:economics` / `doctype:*` shorthand ->
   user's existing `Book:Capitalized` convention (so we reuse existing tags
   instead of creating 22 duplicates).

Tag mapping (matches what `apply_plan.json` and the live Docspell collective use):
  area:economics          -> Book:Economics
  area:diy                -> Book:DIY        (acronym preserved)
  area:hr                 -> Book:HR
  area:it                 -> Book:IT
  area:legal-compliance   -> Book:Legal Compliance
  area:project-management -> Book:Project Management
  area:<other>            -> Book:Title-cased
  doctype:book            -> (dropped -- folder=Library is implicit)
  doctype:manual          -> (dropped -- same)
  doctype:certificate     -> Certificate     (no category, matches existing tag)
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path


DEFAULT_INPUT = Path("out/docspell-name-classification.csv")
DEFAULT_OUTPUT = Path("out/docspell-name-classification-fixed.csv")


def redact(text: str) -> str:
    """No-op for the offline migrator — preserved for API symmetry."""
    return text


def remap_tag(token: str) -> str:
    """Return remapped 'category:name' or empty string to drop."""
    token = token.strip()
    if not token:
        return ""
    if ":" not in token:
        return token  # plain name — keep
    cat, _, name = token.partition(":")
    cat = cat.strip().lower()
    name = name.strip()

    if cat == "doctype":
        n = name.lower()
        if n == "certificate":
            return "Certificate"
        # book/manual are implicit by being in Library folder
        return ""

    if cat == "area":
        n = name.lower()
        # Preserve specific casings that match existing tags
        special = {
            "diy": "DIY",
            "hr": "HR",
            "it": "IT",
            "legal-compliance": "Legal Compliance",
            "project-management": "Project Management",
        }
        if n in special:
            return f"Book:{special[n]}"
        # Default: title-case (e.g. "economics" → "Economics", "monetary" → "Monetary")
        nice = name.replace("-", " ").replace("_", " ").title()
        return f"Book:{nice}"

    # Unknown category — keep as is
    return token


def parse_tags(value: str) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[;,|]", value)
    return [p.strip() for p in parts if p.strip()]


def rewrite_row(row: dict[str, str], stats: Counter[str]) -> dict[str, str]:
    # Folder remap
    if (row.get("safe_suggested_folder") or "").strip() == "Archive":
        row["safe_suggested_folder"] = "Library"
        stats["folders_remapped"] += 1
    if (row.get("suggested_folder") or "").strip() == "Archive":
        row["suggested_folder"] = "Library"

    # Tag remap (safe_add_tags is what apply_reviewed_actions.py reads)
    tags = parse_tags(row.get("safe_add_tags") or "")
    new_tags: list[str] = []
    seen: set[str] = set()
    for t in tags:
        m = remap_tag(t)
        if not m:
            stats["tags_dropped"] += 1
            continue
        if m.lower() in seen:
            continue
        seen.add(m.lower())
        if m != t:
            stats["tags_remapped"] += 1
        new_tags.append(m)
    row["safe_add_tags"] = ";".join(new_tags)

    # Also fix the informational suggested_areas + suggested_doctype columns
    # (purely cosmetic, no impact on apply, but keeps the CSV self-consistent)
    return row


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rewrite a docspell-name-classification CSV to the live "
        "Docspell schema. Offline and idempotent."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help=f"Input CSV path (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Output CSV path (default: {DEFAULT_OUTPUT})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        print(f"Input not found: {input_path}", file=sys.stderr)
        return 1

    with input_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        original_rows = [dict(r) for r in reader]

    stats: Counter[str] = Counter()
    rows = [rewrite_row(dict(r), stats) for r in original_rows]

    # Idempotency check: if nothing changed, still write the output (so the
    # downstream pipeline is happy) but report "no changes needed".
    no_changes = (rows == original_rows)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    folder_counts = Counter(r.get("safe_suggested_folder", "") for r in rows)
    all_tags: list[str] = []
    for r in rows:
        all_tags.extend(parse_tags(r.get("safe_add_tags") or ""))
    tag_counts = Counter(all_tags)

    print(f"Wrote {output_path} ({len(rows)} rows)")
    print()
    if no_changes:
        print("No changes needed - input is already in the target schema.")
    else:
        print("Migration summary:")
        print(f"  rows with folder remapped (Archive -> Library): {stats['folders_remapped']}")
        print(f"  tag tokens remapped:                            {stats['tags_remapped']}")
        print(f"  tag tokens dropped (book/manual implicit):      {stats['tags_dropped']}")
    print()
    print("Folder distribution after fix:")
    for k, v in folder_counts.most_common():
        print(f"  {k or '(empty)':20s} {v}")
    print()
    print("Tag distribution after fix (top 30):")
    for k, v in tag_counts.most_common(30):
        print(f"  {k:30s} {v}")
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
