#!/usr/bin/env python3
"""Seed common Bulgarian correspondent Organizations into Docspell.

Creates Organization entries (used as item correspondents) for the
banks, telecoms, utilities, government bodies, couriers, and IT vendors
that appear in this household's document stream. Each org is created
with its public marketing website and a generic contact email so the
Docspell address-book auto-detection has something to match against
during item processing.

This script is idempotent: existing organizations (case-insensitive
match by name) are skipped. Re-running is safe.

Defaults to dry-run. To change anything in Docspell you must pass BOTH:

    --apply
    --confirm SEED-ORGS

Endpoints used:

    GET  /api/info/version
    POST /open/auth/login            (only if DOCSPELL_TOKEN missing)
    GET  /sec/organization           (read-only, lookup existing)
    POST /sec/organization           (only with --apply)

The script never calls delete, update, or destructive endpoints. It
never prints passwords, OTPs, session tokens, cookies, or auth headers.
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
DEFAULT_LOG = "out/seed-orgs-log.csv"
CONFIRM_PHRASE = "SEED-ORGS"


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

# Each entry: (display_name, short_name, category, website_domain, email)
# `category` is informational only — Docspell has no formal "org category"
# field. We stash it in the `notes` field for the operator's reference and
# use shortName for the abbreviation.
SEED_ORGS: list[tuple[str, str, str, str, str]] = [
    # Banking
    ("ДСК Банк",            "DSK",         "Banking",    "dskbank.bg",          "info@dskbank.bg"),
    ("UniCredit Bulbank",   "UniCredit",   "Banking",    "unicreditbulbank.bg", "info@unicreditgroup.bg"),
    ("Postbank",            "Postbank",    "Banking",    "postbank.bg",         "support@postbank.bg"),
    ("Fibank",              "Fibank",      "Banking",    "fibank.bg",           "office@fibank.bg"),
    ("ОББ (UBB)",           "UBB",         "Banking",    "ubb.bg",              "ubb@ubb.bg"),
    ("ProCredit Bank",      "ProCredit",   "Banking",    "procreditbank.bg",    "info@procreditbank.bg"),
    ("Allianz Bank",        "Allianz",     "Banking",    "allianz.bg",          "info@allianz.bg"),
    ("Райфайзенбанк",       "RBB",         "Banking",    "rbb.bg",              "info@rbb.bg"),

    # Telecom
    ("A1 България",         "A1",          "Telecom",    "a1.bg",               "customer@a1.bg"),
    ("Vivacom",             "Vivacom",     "Telecom",    "vivacom.bg",          "info@vivacom.bg"),
    ("Yettel",              "Yettel",      "Telecom",    "yettel.bg",           "info@yettel.bg"),

    # Utility
    ("EVN",                 "EVN",         "Utility",    "evn.bg",              "info@evn.bg"),
    ("ЧЕЗ",                 "CEZ",         "Utility",    "cez.bg",              "info@cez.bg"),
    ("Energo-Pro",          "Energo-Pro",  "Utility",    "energo-pro.bg",       "info@energo-pro.bg"),
    ("Топлофикация София",  "Toplo",       "Utility",    "toplo.bg",            "office@toplo.bg"),
    ("Софийска вода",       "SofVoda",     "Utility",    "sofiyskavoda.bg",     "customers@sofiyskavoda.bg"),
    ("Овергаз",             "Overgas",     "Utility",    "overgas.bg",          "office@overgas.bg"),
    ("Булгаргаз",           "Bulgargaz",   "Utility",    "bulgargaz.bg",        "office@bulgargaz.bg"),

    # Government
    ("НАП",                 "NAP",         "Government", "nra.bg",              "infocenter@nra.bg"),
    ("НОИ",                 "NOI",         "Government", "nssi.bg",             "noi@nssi.bg"),

    # Couriers / Logistics
    ("Speedy",              "Speedy",      "Logistics",  "speedy.bg",           "info@speedy.bg"),
    ("Econt",               "Econt",       "Logistics",  "econt.com",           "info@econt.com"),
    ("DHL",                 "DHL",         "Logistics",  "dhl.bg",              "info.bg@dhl.com"),
    ("DPD",                 "DPD",         "Logistics",  "dpd.com",             "info@dpd.bg"),

    # IT
    ("JetBrains",           "JetBrains",   "IT",         "jetbrains.com",       "sales@jetbrains.com"),
    ("GitHub",              "GitHub",      "IT",         "github.com",          "contact@github.com"),
    ("Atlassian",           "Atlassian",   "IT",         "atlassian.com",       "sales@atlassian.com"),
    ("Google",              "Google",      "IT",         "google.com",          "support@google.com"),
    ("Microsoft",           "Microsoft",   "IT",         "microsoft.com",       "support@microsoft.com"),
    ("AWS",                 "AWS",         "IT",         "aws.amazon.com",      "aws-billing@amazon.com"),
]


# ---------------------------------------------------------------------------
# Organization lookup + create  (HTTP plumbing lives in _docspell_common)
# ---------------------------------------------------------------------------


def _extract_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("items", "organizations"):
            items = data.get(key)
            if isinstance(items, list):
                return [i for i in items if isinstance(i, dict)]
    if isinstance(data, list):
        return [i for i in data if isinstance(i, dict)]
    return []


def list_organizations(session: Session) -> list[dict[str, Any]]:
    # Docspell uses ?q= for org search; empty string returns all.
    return _extract_items(session.request("GET", "/sec/organization?q=&full=false"))


def _id_from_create_result(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("id", "newId", "value"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def build_org_body(
    name: str, short_name: str, category: str, website: str, email: str
) -> dict[str, Any]:
    """Construct the Docspell Organization create payload."""
    return {
        "id": "",
        "name": name,
        "address": {
            "street": "",
            "zip": "",
            "city": "",
            "country": "",
        },
        "contacts": [
            {"id": "", "kind": "website", "value": f"https://{website}"},
            {"id": "", "kind": "email", "value": email},
        ],
        "notes": f"category: {category}",
        "shortName": short_name,
        "use": "correspondent",
        "created": 0,
    }


def create_organization(session: Session, body: dict[str, Any]) -> str | None:
    result = session.request("POST", "/sec/organization", body=body)
    org_id = _id_from_create_result(result)
    if org_id:
        return org_id
    # Some Docspell versions only return {success: true} — fall back to re-list.
    for org in list_organizations(session):
        if str(org.get("name", "")).strip().casefold() == body["name"].strip().casefold():
            return org.get("id")
    return None


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed common Bulgarian correspondent Organizations into Docspell."
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("DOCSPELL_URL", DEFAULT_URL),
        help="Docspell base URL",
    )
    parser.add_argument(
        "--account", help="Docspell account; default prompts or DOCSPELL_ACCOUNT"
    )
    parser.add_argument("--log", default=DEFAULT_LOG, help="Seed log output CSV path")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually create organizations. Still requires --confirm.",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help=f"Must equal '{CONFIRM_PHRASE}' together with --apply to enable writes",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Maximum number of orgs to seed (0 = all)",
    )
    parser.add_argument(
        "--start-from", default="",
        help="Skip orgs until (and excluding) this org name is reached. "
             "Useful for resuming after an interruption.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.apply and args.confirm != CONFIRM_PHRASE:
        print(
            f"--apply requires --confirm {CONFIRM_PHRASE}. Refusing to make changes.",
            file=sys.stderr,
        )
        return 2

    dry_run = not args.apply
    base_url = args.url.rstrip("/")

    dns_preflight(base_url)

    seed_list = list(SEED_ORGS)
    if args.start_from:
        idx = next(
            (i for i, t in enumerate(seed_list) if t[0] == args.start_from),
            None,
        )
        if idx is None:
            print(
                f"--start-from '{args.start_from}' not found; continuing from top.",
                file=sys.stderr,
            )
        else:
            print(f"--start-from: resuming after index {idx} ('{args.start_from}')")
            seed_list = seed_list[idx + 1 :]
    if args.limit and args.limit > 0:
        seed_list = seed_list[: args.limit]

    version = version_check(base_url)
    version_str = version.get("version", "unknown")
    print(f"Docspell URL:     {base_url}")
    print(f"Docspell version: {version_str}")
    version_warn(version_str)
    print(f"Mode:             {'APPLY' if not dry_run else 'dry-run'}")
    print(f"Orgs to seed:     {len(seed_list)}")
    print()

    session = Session.from_args(base_url, args)
    existing = list_organizations(session)
    existing_by_name = {
        str(o.get("name", "")).strip().casefold(): o for o in existing
    }
    print(f"Existing orgs in Docspell: {len(existing)}")
    print()

    log_path = Path(args.log)
    if not log_path.is_absolute():
        log_path = Path.cwd() / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)

    created = 0
    skipped = 0
    would_create = 0
    failed = 0
    retried_names: set[str] = set()

    rows_for_log: list[dict[str, str]] = []

    progress = Progress(len(seed_list), prefix="  ", print_every=5)

    def _on_net(msg: str) -> None:
        if "retry" in msg or "401" in msg:
            cur = getattr(_on_net, "current", None)
            if cur:
                retried_names.add(cur)
        sys.stdout.write("\n" + redact(msg) + "\n")
        sys.stdout.flush()

    session.log = _on_net

    for name, short_name, category, website, email in seed_list:
        _on_net.current = name  # type: ignore[attr-defined]
        key = name.casefold()
        if key in existing_by_name:
            org_id = existing_by_name[key].get("id", "")
            print(f"  [skip] {name:30s} already exists (id={org_id[:8]}…)")
            skipped += 1
            rows_for_log.append(
                {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "name": name,
                    "short_name": short_name,
                    "category": category,
                    "status": "skipped-existing",
                    "id": org_id,
                    "error": "",
                }
            )
            progress.tick(skipped=True)
            continue

        if dry_run:
            print(f"  [dry] {name:30s} -> would create ({category}, {website}, {email})")
            would_create += 1
            rows_for_log.append(
                {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "name": name,
                    "short_name": short_name,
                    "category": category,
                    "status": "dry-run-would-create",
                    "id": "",
                    "error": "",
                }
            )
            progress.tick(ok=True)
            continue

        body = build_org_body(name, short_name, category, website, email)
        try:
            org_id = create_organization(session, body)
            if not org_id:
                raise RuntimeError("create returned no id and re-list could not find org")
            print(f"  [new]  {name:30s} created (id={org_id[:8]}…)")
            created += 1
            rows_for_log.append(
                {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "name": name,
                    "short_name": short_name,
                    "category": category,
                    "status": "created",
                    "id": org_id,
                    "error": "",
                }
            )
            progress.tick(ok=True)
        except Exception as exc:
            err = err_to_log(exc, max_body=80)
            print(f"  [FAIL] {name:30s} {err}")
            failed += 1
            rows_for_log.append(
                {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "name": name,
                    "short_name": short_name,
                    "category": category,
                    "status": "failed",
                    "id": "",
                    "error": err,
                }
            )
            progress.tick(failed=True)

    progress.done()

    with log_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "timestamp",
                "name",
                "short_name",
                "category",
                "status",
                "id",
                "error",
            ],
        )
        writer.writeheader()
        for row in rows_for_log:
            writer.writerow(row)

    summary = Summary(
        total=progress.processed,
        ok=created if not dry_run else would_create,
        failed=failed,
        retried=len(retried_names),
        skipped=skipped,
        elapsed=progress.elapsed,
        log_path=str(log_path),
        extra={
            "would_create (dry-run)": would_create if dry_run else "-",
            "mode": "APPLY" if not dry_run else "dry-run",
        },
    )
    summary.print()
    if dry_run:
        print(f"\nTo apply, rerun with:  --apply --confirm {CONFIRM_PHRASE}")

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
