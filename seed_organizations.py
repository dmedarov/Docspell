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
# HTTP plumbing (mirrors apply_reviewed_actions.py)
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
# Organization lookup + create
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


def list_organizations(base_url: str, token: str) -> list[dict[str, Any]]:
    # Docspell uses ?q= for org search; empty string returns all.
    url = api_url(base_url, "/sec/organization") + "?q=&full=false"
    return _extract_items(request_json("GET", url, token=token))


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


def create_organization(base_url: str, token: str, body: dict[str, Any]) -> str | None:
    result = request_json(
        "POST",
        api_url(base_url, "/sec/organization"),
        token=token,
        body=body,
    )
    org_id = _id_from_create_result(result)
    if org_id:
        return org_id
    # Some Docspell versions only return {success: true} — fall back to re-list.
    for org in list_organizations(base_url, token):
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

    version = check_version(base_url)
    print(f"Docspell URL:     {base_url}")
    print(f"Docspell version: {version.get('version', 'unknown')}")
    print(f"Mode:             {'APPLY' if not dry_run else 'dry-run'}")
    print(f"Orgs to seed:     {len(SEED_ORGS)}")
    print()

    token = login(base_url, args)
    existing = list_organizations(base_url, token)
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

    rows_for_log: list[dict[str, str]] = []

    for name, short_name, category, website, email in SEED_ORGS:
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
            continue

        body = build_org_body(name, short_name, category, website, email)
        try:
            org_id = create_organization(base_url, token, body)
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
        except Exception as exc:
            err = redact(str(exc))
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

    print()
    print("Summary:")
    print(f"  skipped (already existed): {skipped}")
    if dry_run:
        print(f"  would create:              {would_create}")
        print()
        print("Dry-run complete. No changes made.")
        print(f"To apply, rerun with:  --apply --confirm {CONFIRM_PHRASE}")
    else:
        print(f"  created:                   {created}")
        print(f"  failed:                    {failed}")
    print(f"Log: {log_path}")

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
