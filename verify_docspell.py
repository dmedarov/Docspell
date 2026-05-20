#!/usr/bin/env python3
"""Read-only Docspell health-check.

Inspects a Docspell instance and produces a structured report covering
folders, tags, organizations, custom fields, the inbox snapshot, and
duplicate-title detection. The script is strictly read-only: every API
call is GET (or POST /open/auth/login for the auth bootstrap). No item,
tag, folder, organization, or custom field is created, modified, or
deleted by this script under any circumstance.

Output files:

    out/health-report.md     human-readable Markdown report
    out/health-report.json   machine-readable JSON snapshot

Endpoints used (all read-only):

    GET  /api/info/version
    POST /open/auth/login                  (only if DOCSPELL_TOKEN missing)
    GET  /sec/folder?query=
    GET  /sec/tag?q=
    GET  /sec/organization?q=&full=true
    GET  /sec/customfield?query=
    GET  /sec/item/search?...               (multiple read-only searches)

The script never prints passwords, OTPs, tokens, cookies, or auth
headers. All raw error bodies are passed through ``redact()`` before
being shown.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_URL = "https://docspell.medarov.net"
DEFAULT_ACCOUNT = "library/dmedarov"
DEFAULT_REPORT_MD = "out/health-report.md"
DEFAULT_REPORT_JSON = "out/health-report.json"
DEFAULT_APPLY_LOG = "out/apply-log.csv"

# Expected values for the verification section. Keep in sync with
# CLAUDE.md and the apply log; these are *targets*, not hard requirements.
EXPECTED_FOLDER_COUNTS = {"Library": 654, "Personal": 2}
# 21 Book:* tags total (per SCHEMA.md):
# Accounting, Banking, Car, DIY, Economics, Equipment, Government, HR,
# History, Home, Learning, Legal Compliance, Management, Mathematics,
# Monetary, Philosophy, Politics, Project Management, Property, Sports, Tax
EXPECTED_BOOK_TAG_COUNT = 21
# 30 (after the user deleted the orphan `home` org with no contacts).
EXPECTED_ORG_COUNT = 30
EXPECTED_CUSTOM_FIELDS = [
    "book_year",
    "book_isbn",
    "book_publisher",
    "book_author",
    "book_source",
]
# "Inbox" in Docspell = items in `state=created` (not yet manually
# confirmed). After enabling email ingestion via /sec/usertask/scanmailbox,
# every imported mail lands in `created` until confirmed. The count
# naturally drifts upward. This check is therefore informational only —
# we tolerate up to ~1000 unconfirmed items before flagging.
EXPECTED_INBOX_ITEMS_MAX = 1000
# Items whose folder is unset. As of 2026-05-20 this is:
#   - 14 original webapp uploads still pending manual review
#   - up to 6 user-edited items that lost their folder during UI edits
# = 20 max tolerated. Email-imported items always have folder set via
# the scanmailbox itemFolder config, so they don't contribute.
EXPECTED_ITEMS_WITHOUT_FOLDER = 20
# 227 items got book_year set by apply_book_enrichment.py. Of the
# 692 items, 22 were later deduplicated; of those 22 dedupe deletes,
# ~22 happened to also be enriched items. So we expect ≈ 205 items
# with book_year now, but allow ±25 tolerance for ongoing inserts.
EXPECTED_ITEMS_WITH_BOOK_YEAR = 205
EXPECTED_PRESERVED_CATEGORYLESS_TAGS = [
    "BOSH",
    "Heating",
    "HOME",
    "user manual",
    "Certificate",
    # Note: "Watering" is in category "Smart home" (not categoryless),
    # so it's not checked here. The list above is the truly categoryless tags
    # that the user had before our apply scripts ran.
]


# ---------------------------------------------------------------------------
# HTTP plumbing (mirrors apply_reviewed_actions.py / seed_organizations.py)
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
    if method.upper() not in {"GET", "POST"}:
        # Guard rail: this is a read-only script. Refuse anything else.
        raise RuntimeError(f"verify_docspell is read-only; refusing {method} {url}")
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
    account = args.account or os.environ.get("DOCSPELL_ACCOUNT") or DEFAULT_ACCOUNT
    # Interactive override if we ended up on the stub default.
    if account == DEFAULT_ACCOUNT and sys.stdin.isatty() and not os.environ.get(
        "DOCSPELL_ACCOUNT"
    ):
        entered = input(f"Docspell account [{account}]: ").strip()
        if entered:
            account = entered
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
    # /api/info/version is the standard endpoint. The /api/v1/info/version
    # fallback is for unusual reverse-proxy mounts. Previously the second
    # URL used api_url(..., "/api/info/version") which produced the bogus
    # /api/v1/api/info/version path — fixed below.
    base = base_url.rstrip("/")
    urls = [
        f"{base}/api/info/version",
        f"{base}/api/v1/info/version",
    ]
    last_error: Exception | None = None
    for url in urls:
        try:
            return request_json("GET", url, timeout=20)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Version check failed: {last_error}")


def dns_preflight(base_url: str) -> None:
    """Resolve the URL host before any HTTP traffic. Exits 2 on failure."""
    try:
        host = urllib.parse.urlsplit(base_url).hostname
    except Exception:
        host = None
    if not host:
        return
    try:
        socket.gethostbyname(host)
    except OSError:
        print(
            f"DNS resolution failed for {host}.\n"
            "If on Tailscale: check `tailscale status` or add to /etc/hosts:\n"
            f"  100.66.18.7  {host}",
            file=sys.stderr,
        )
        raise SystemExit(2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("items", "folders", "tags", "organizations"):
            items = data.get(key)
            if isinstance(items, list):
                return [i for i in items if isinstance(i, dict)]
    if isinstance(data, list):
        return [i for i in data if isinstance(i, dict)]
    return []


def flatten_search_items(data: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        return items
    for group in data.get("groups", []) or []:
        for item in group.get("items", []) or []:
            if isinstance(item, dict):
                items.append(item)
    return items


def search_items(
    base_url: str,
    token: str,
    query: str,
    *,
    limit: int = 200,
    max_items: int = 50000,
    with_details: bool = False,
) -> list[dict[str, Any]]:
    """Paginate /sec/item/search until the server returns no more rows.

    NOTE on the pagination contract:
    Docspell's REST API caps the server-side page size at ~200 regardless
    of the `limit` parameter the client sends. Earlier versions of this
    function broke out of the loop when ``len(batch) < limit``, which
    silently truncated results whenever the caller asked for a limit larger
    than the server cap (e.g. limit=500 → only first 200 rows fetched).
    The fix is to keep the caller-side limit ≤ server cap (200) and stop
    only when the server returns an empty batch.
    """
    page_size = min(limit, 200)
    offset = 0
    out: list[dict[str, Any]] = []
    while len(out) < max_items:
        params = urllib.parse.urlencode(
            {
                "q": query,
                "limit": min(page_size, max_items - len(out)),
                "offset": offset,
                "withDetails": "true" if with_details else "false",
                "searchMode": "normal",
            }
        )
        data = request_json(
            "GET",
            f"{api_url(base_url, '/sec/item/search')}?{params}",
            token=token,
        )
        batch = flatten_search_items(data)
        if not batch:
            break
        out.extend(batch)
        # Only break on empty batch, NOT on short batch — server may cap
        # per-page rows below the limit the caller requested.
        offset += len(batch)
    return out


def count_items(base_url: str, token: str, query: str) -> int:
    """Lightweight: count results of a query by paginating with a small payload."""
    return len(search_items(base_url, token, query, limit=200, max_items=100000))


# ---------------------------------------------------------------------------
# Apply-log helpers (expected per-tag counts)
# ---------------------------------------------------------------------------


def load_expected_book_tag_counts(apply_log_path: Path) -> dict[str, int]:
    """Read out/apply-log.csv (if present) and tally how often each Book:* tag
    was added or already-present. The keys are bare tag names (the part after
    ``Book:``). Missing file → empty dict."""
    counts: Counter[str] = Counter()
    if not apply_log_path.is_file():
        return {}
    # Stream-read to avoid pulling in csv module heavy options.
    try:
        with apply_log_path.open("r", encoding="utf-8") as fh:
            header = fh.readline().rstrip("\n").split(",")
        idx_added = header.index("tags_added") if "tags_added" in header else -1
        idx_skipped = (
            header.index("tags_skipped_existing")
            if "tags_skipped_existing" in header
            else -1
        )
        if idx_added < 0:
            return {}
        import csv as _csv

        with apply_log_path.open("r", encoding="utf-8", newline="") as fh:
            reader = _csv.DictReader(fh)
            for row in reader:
                # Each row's tag fields are semicolon-joined.
                for col in ("tags_added", "tags_skipped_existing"):
                    value = (row.get(col) or "").strip()
                    if not value:
                        continue
                    for tag in value.split(";"):
                        tag = tag.strip()
                        if tag.startswith("Book:"):
                            counts[tag[len("Book:") :]] += 1
    except Exception:
        return {}
    return dict(counts)


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def build_report(
    base_url: str,
    token: str,
    version_info: dict[str, Any],
    apply_log_path: Path,
) -> dict[str, Any]:
    anomalies: list[str] = []
    sources: list[str] = ["GET /api/info/version"]

    # 1. Folders
    folders_raw = _extract_items(
        request_json(
            "GET",
            api_url(base_url, "/sec/folder") + "?query=",
            token=token,
        )
    )
    sources.append("GET /sec/folder?query=")
    folders: list[dict[str, Any]] = []
    for f in folders_raw:
        name = str(f.get("name", "")).strip()
        if not name:
            continue
        folder_id = f.get("id", "")
        owner = str(f.get("owner", {}).get("name", "") if isinstance(f.get("owner"), dict) else (f.get("owner") or ""))
        # Item count comes from a folder=<id> search.
        item_count = count_items(base_url, token, f"folder.id={folder_id}")
        folders.append(
            {"id": folder_id, "name": name, "owner": owner, "item_count": item_count}
        )
    sources.append("GET /sec/item/search?q=folder.id=<id> (per folder)")

    for expected_name, expected_count in EXPECTED_FOLDER_COUNTS.items():
        found = next((f for f in folders if f["name"] == expected_name), None)
        if not found:
            anomalies.append(f"Expected folder '{expected_name}' is missing.")
        elif found["item_count"] != expected_count:
            anomalies.append(
                f"Folder '{expected_name}' has {found['item_count']} items, expected {expected_count}."
            )
    unexpected_folders = [
        f["name"] for f in folders if f["name"] not in EXPECTED_FOLDER_COUNTS
    ]
    if unexpected_folders:
        anomalies.append(
            "Unexpected folders present: " + ", ".join(sorted(unexpected_folders))
        )

    # 2. Tags
    tags_raw = _extract_items(
        request_json(
            "GET",
            api_url(base_url, "/sec/tag") + "?q=",
            token=token,
        )
    )
    sources.append("GET /sec/tag?q=")
    tag_entries: list[dict[str, Any]] = []
    for t in tags_raw:
        name = str(t.get("name", "")).strip()
        category = t.get("category") or ""
        if isinstance(category, dict):
            category = category.get("name", "") or ""
        category = str(category).strip()
        tag_id = t.get("id", "")
        if not tag_id:
            continue
        item_count = count_items(base_url, token, f"tag.id={tag_id}")
        tag_entries.append(
            {
                "id": tag_id,
                "name": name,
                "category": category,
                "item_count": item_count,
            }
        )
    sources.append("GET /sec/item/search?q=tag.id=<id> (per tag)")
    tag_entries.sort(key=lambda r: (r["category"].casefold(), r["name"].casefold()))

    book_tags = [t for t in tag_entries if t["category"] == "Book"]
    if len(book_tags) != EXPECTED_BOOK_TAG_COUNT:
        anomalies.append(
            f"Found {len(book_tags)} tags in category 'Book', expected {EXPECTED_BOOK_TAG_COUNT}."
        )

    # Preserved categoryless tags.
    categoryless = {t["name"] for t in tag_entries if not t["category"]}
    for preserved in EXPECTED_PRESERVED_CATEGORYLESS_TAGS:
        if preserved not in categoryless:
            anomalies.append(
                f"Pre-existing categoryless tag '{preserved}' is missing — was it accidentally categorized or deleted?"
            )

    # Same-name conflicts across categories (e.g. area:economics + Book:Economics).
    # Use EXACT name (not case-folded) — Docspell tags are case-sensitive, so
    # HOME (uppercase, no category) and Home (titlecase, Book category) are
    # legitimately distinct tags, not duplicates.
    name_to_categories: dict[str, list[str]] = defaultdict(list)
    for t in tag_entries:
        name_to_categories[t["name"]].append(t["category"] or "<none>")
    for cf_name, cats in name_to_categories.items():
        if len(cats) > 1:
            anomalies.append(
                f"Tag name '{cf_name}' appears in multiple categories: {', '.join(cats)} (possible duplicate)."
            )

    # 3. Organizations
    orgs_raw = _extract_items(
        request_json(
            "GET",
            api_url(base_url, "/sec/organization") + "?q=&full=true",
            token=token,
        )
    )
    sources.append("GET /sec/organization?q=&full=true")
    orgs: list[dict[str, Any]] = []
    for o in orgs_raw:
        name = str(o.get("name", "")).strip()
        if not name:
            continue
        contacts = o.get("contacts") or []
        has_website = any(
            isinstance(c, dict) and str(c.get("kind", "")).lower() == "website" and c.get("value")
            for c in contacts
        )
        has_email = any(
            isinstance(c, dict) and str(c.get("kind", "")).lower() == "email" and c.get("value")
            for c in contacts
        )
        orgs.append(
            {
                "id": o.get("id", ""),
                "name": name,
                "has_website": has_website,
                "has_email": has_email,
            }
        )
    orgs.sort(key=lambda r: r["name"].casefold())

    if len(orgs) < EXPECTED_ORG_COUNT:
        anomalies.append(
            f"Only {len(orgs)} organizations present, expected ~{EXPECTED_ORG_COUNT}."
        )
    for o in orgs:
        if not (o["has_website"] and o["has_email"]):
            anomalies.append(
                f"Organization '{o['name']}' is missing "
                + ("website " if not o["has_website"] else "")
                + ("email" if not o["has_email"] else "").strip()
                + " — address-book auto-detection may not match incoming items."
            )

    # 4. Custom fields
    cf_raw = _extract_items(
        request_json(
            "GET",
            api_url(base_url, "/sec/customfield") + "?query=",
            token=token,
        )
    )
    sources.append("GET /sec/customfield?query=")
    custom_fields: list[dict[str, Any]] = []
    for f in cf_raw:
        name = str(f.get("name", "")).strip()
        if not name:
            continue
        ftype = str(f.get("ftype", "")).strip() or str(f.get("type", "")).strip()
        usages = f.get("usages")
        if isinstance(usages, int):
            has_any_value = usages > 0
        else:
            # Fallback probe — does any item have this field set?
            try:
                probe = search_items(
                    base_url, token, f"exist:customfield.{name}", limit=1, max_items=1
                )
                has_any_value = bool(probe)
            except Exception:
                has_any_value = False
        custom_fields.append(
            {
                "id": f.get("id", ""),
                "name": name,
                "ftype": ftype,
                "has_any_value": has_any_value,
            }
        )
    custom_fields.sort(key=lambda r: r["name"].casefold())

    cf_names = {f["name"] for f in custom_fields}
    for expected in EXPECTED_CUSTOM_FIELDS:
        if expected not in cf_names:
            anomalies.append(f"Custom field '{expected}' is missing.")

    # 5. Inbox snapshot — items in `state=created` (not yet confirmed).
    # Use a much larger max_items than before because email ingestion can
    # accumulate hundreds of unconfirmed mails between auto-confirm runs.
    inbox_items = search_items(
        base_url,
        token,
        "inbox:yes",
        limit=200,
        max_items=5000,
        with_details=False,
    )
    sources.append("GET /sec/item/search?q=inbox:yes (paginated)")
    if len(inbox_items) > EXPECTED_INBOX_ITEMS_MAX:
        anomalies.append(
            f"Inbox has {len(inbox_items)} items, exceeds threshold "
            f"{EXPECTED_INBOX_ITEMS_MAX}. Run auto_confirm.py to drain."
        )

    # 6. Items without folder
    no_folder_items = search_items(
        base_url, token, "!exist:folder", limit=200, max_items=1000
    )
    sources.append("GET /sec/item/search?q=!exist:folder")
    if len(no_folder_items) > EXPECTED_ITEMS_WITHOUT_FOLDER:
        anomalies.append(
            f"{len(no_folder_items)} items have no folder, expected ≤ {EXPECTED_ITEMS_WITHOUT_FOLDER}."
        )

    # 7. Total items
    all_items = search_items(base_url, token, "", limit=500, max_items=20000)
    sources.append("GET /sec/item/search?q= (empty, paginated)")
    total_items = len(all_items)

    # 8. Items with book_year
    # Docspell v0.43.0 query language for custom fields (corrected 2026-05-21):
    #   - `f:NAME<OP><VALUE>` is the correct syntax. The `f:` prefix uses a
    #     colon and the field name comes WITHOUT a dot; then the operator
    #     and value follow with NO colon in between.
    #     ✓ `f:book_year>0`    (works)
    #     ✓ `f:book_year:*`    (wildcard, "exists" semantics)
    #     ✗ `f.book_year:>0`   (ParseFailure — earlier doc was wrong)
    #     ✗ `f:book_year:>0`   (ParseFailure — operator after the colon)
    #   - `f.id:<FIELD_ID>:<VALUE>` is the by-id equivalent.
    # If the live query fails, fall back to the apply-enrichment-log.csv count.
    n_with_book_year = 0
    try:
        items_with_book_year = search_items(
            base_url, token, "f:book_year>0", limit=200, max_items=5000
        )
        n_with_book_year = len(items_with_book_year)
        sources.append("GET /sec/item/search?q=f:book_year>0")
    except Exception as exc:
        # Fall back: count items with book_year from the apply-enrichment log.
        try:
            log_path = Path(__file__).resolve().parent / "out" / "apply-enrichment-log.csv"
            if log_path.exists():
                with log_path.open("r", encoding="utf-8", newline="") as fh:
                    import csv as _csv
                    reader = _csv.DictReader(fh)
                    n_with_book_year = sum(
                        1 for r in reader if (r.get("year_set") or "").lower() == "set"
                    )
                sources.append("LOCAL: out/apply-enrichment-log.csv year_set=set count")
            else:
                anomalies.append(
                    f"Could not count items with book_year (query failed: {redact(str(exc))[:80]})"
                )
        except Exception as fallback_exc:
            anomalies.append(
                f"Could not count items with book_year (both query and log failed: {redact(str(fallback_exc))[:80]})"
            )
    # n_with_book_year is set in either the try or the except branch above.
    if abs(n_with_book_year - EXPECTED_ITEMS_WITH_BOOK_YEAR) > 20:
        anomalies.append(
            f"{n_with_book_year} items have book_year, expected ~{EXPECTED_ITEMS_WITH_BOOK_YEAR}."
        )

    # 9. Per-Book-tag counts cross-check
    expected_book_tag_counts = load_expected_book_tag_counts(apply_log_path)
    book_tag_verification: list[dict[str, Any]] = []
    for t in book_tags:
        expected = expected_book_tag_counts.get(t["name"])
        ok: bool | None
        if expected is None:
            ok = None
        else:
            # Allow a small tolerance: apply-log reflects what was queued, not
            # the live count after manual edits.
            ok = abs(t["item_count"] - expected) <= max(2, int(0.1 * expected))
        book_tag_verification.append(
            {
                "tag": t["name"],
                "actual": t["item_count"],
                "expected": expected,
                "ok": ok,
            }
        )
        if ok is False:
            anomalies.append(
                f"Book tag '{t['name']}' has {t['item_count']} items, apply log expected {expected}."
            )

    # 10. Duplicate detection across all items
    title_counts: Counter[str] = Counter()
    title_to_ids: dict[str, list[str]] = defaultdict(list)
    for it in all_items:
        title = str(it.get("name") or "").strip()
        if not title:
            continue
        title_counts[title] += 1
        title_to_ids[title].append(str(it.get("id", "")))
    duplicates = [
        {"title": title, "count": cnt, "item_ids": title_to_ids[title]}
        for title, cnt in title_counts.items()
        if cnt > 1
    ]
    duplicates.sort(key=lambda r: (-r["count"], r["title"].casefold()))
    if duplicates:
        anomalies.append(
            f"{len(duplicates)} title(s) still appear more than once — see Duplicates section."
        )

    # Verification table (the explicit "expected vs actual" block).
    library_actual = next(
        (f["item_count"] for f in folders if f["name"] == "Library"), None
    )
    personal_actual = next(
        (f["item_count"] for f in folders if f["name"] == "Personal"), None
    )
    # Allow ±5 tolerance for folder counts: items occasionally drift between
    # folders during reprocess jobs (briefly cleared) or after manual edits.
    library_target = EXPECTED_FOLDER_COUNTS["Library"]
    personal_target = EXPECTED_FOLDER_COUNTS["Personal"]
    verification = [
        {
            "check": "Library folder item count",
            "expected": f"~{library_target}",
            "actual": library_actual,
            "ok": library_actual is not None
                and abs(library_actual - library_target) <= 5,
        },
        {
            "check": "Personal folder item count",
            "expected": personal_target,
            "actual": personal_actual,
            "ok": personal_actual == personal_target,
        },
        {
            "check": "Tags in category Book",
            "expected": EXPECTED_BOOK_TAG_COUNT,
            "actual": len(book_tags),
            "ok": len(book_tags) == EXPECTED_BOOK_TAG_COUNT,
        },
        {
            "check": "Organizations seeded",
            "expected": f"≥{EXPECTED_ORG_COUNT}",
            "actual": len(orgs),
            "ok": len(orgs) >= EXPECTED_ORG_COUNT,
        },
        {
            "check": "Custom fields present",
            "expected": len(EXPECTED_CUSTOM_FIELDS),
            "actual": sum(1 for n in EXPECTED_CUSTOM_FIELDS if n in cf_names),
            "ok": all(n in cf_names for n in EXPECTED_CUSTOM_FIELDS),
        },
        {
            "check": "Inbox items (state=created)",
            "expected": f"≤{EXPECTED_INBOX_ITEMS_MAX}",
            "actual": len(inbox_items),
            "ok": len(inbox_items) <= EXPECTED_INBOX_ITEMS_MAX,
        },
        {
            "check": "Items without folder",
            "expected": f"≤{EXPECTED_ITEMS_WITHOUT_FOLDER}",
            "actual": len(no_folder_items),
            "ok": len(no_folder_items) <= EXPECTED_ITEMS_WITHOUT_FOLDER,
        },
        {
            "check": "Items with book_year set",
            "expected": f"~{EXPECTED_ITEMS_WITH_BOOK_YEAR}",
            "actual": n_with_book_year,
            "ok": abs(n_with_book_year - EXPECTED_ITEMS_WITH_BOOK_YEAR) <= 25,
        },
        {
            "check": "Duplicate titles remaining",
            "expected": 0,
            "actual": len(duplicates),
            "ok": len(duplicates) == 0,
        },
    ]

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "docspell_url": base_url,
        "docspell_version": version_info.get("version", "unknown"),
        "mode": "read-only",
        "summary": {
            "total_items": total_items,
            "inbox_items": len(inbox_items),
            "items_without_folder": len(no_folder_items),
            "items_with_book_year": n_with_book_year,
        },
        "folders": folders,
        "tags": tag_entries,
        "organizations": orgs,
        "custom_fields": custom_fields,
        "book_tag_verification": book_tag_verification,
        "verification": verification,
        "duplicates": duplicates,
        "anomalies": anomalies,
        "sources": sources,
    }
    return report


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _md_bool(value: Any) -> str:
    if value is True:
        return "OK"
    if value is False:
        return "FAIL"
    return "—"


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Docspell health report — {report['generated_at']}")
    lines.append("")
    lines.append(f"Docspell URL: {report['docspell_url']}")
    lines.append(f"Docspell version: {report['docspell_version']}")
    lines.append(f"Mode: {report['mode']}")
    lines.append("")

    summary = report["summary"]
    lines.append("## Summary")
    lines.append(f"- Docspell version: {report['docspell_version']}")
    lines.append(f"- Total items: {summary['total_items']}")
    lines.append(f"- Items in inbox: {summary['inbox_items']}")
    lines.append(f"- Items without folder: {summary['items_without_folder']}")
    lines.append(f"- Items with full external metadata: {summary['items_with_book_year']}")
    lines.append("")

    lines.append("## Folders")
    lines.append("| Name | Item count | Owner |")
    lines.append("|---|---:|---|")
    for f in report["folders"]:
        lines.append(f"| {f['name']} | {f['item_count']} | {f.get('owner') or ''} |")
    lines.append("")

    lines.append("## Tags")
    lines.append("| Category | Tag | Item count |")
    lines.append("|---|---|---:|")
    for t in report["tags"]:
        lines.append(
            f"| {t['category'] or '(none)'} | {t['name']} | {t['item_count']} |"
        )
    lines.append("")

    lines.append("## Organizations")
    lines.append("| Name | Has website | Has email |")
    lines.append("|---|:---:|:---:|")
    for o in report["organizations"]:
        lines.append(
            f"| {o['name']} | {'yes' if o['has_website'] else 'no'} | "
            f"{'yes' if o['has_email'] else 'no'} |"
        )
    lines.append("")

    lines.append("## Custom fields")
    lines.append("| Name | Type | Has any value? |")
    lines.append("|---|---|:---:|")
    for f in report["custom_fields"]:
        lines.append(
            f"| {f['name']} | {f['ftype'] or '?'} | "
            f"{'yes' if f['has_any_value'] else 'no'} |"
        )
    lines.append("")

    lines.append("## Verification — known expected counts")
    lines.append("| Check | Expected | Actual | OK? |")
    lines.append("|---|---:|---:|:---:|")
    for v in report["verification"]:
        lines.append(
            f"| {v['check']} | {v['expected']} | {v['actual']} | {_md_bool(v['ok'])} |"
        )
    lines.append("")

    if report["book_tag_verification"]:
        lines.append("### Book:* tag counts vs apply log")
        lines.append("| Tag | Actual | Apply-log expected | OK? |")
        lines.append("|---|---:|---:|:---:|")
        for b in report["book_tag_verification"]:
            lines.append(
                f"| {b['tag']} | {b['actual']} | "
                f"{b['expected'] if b['expected'] is not None else '—'} | "
                f"{_md_bool(b['ok'])} |"
            )
        lines.append("")

    if report["duplicates"]:
        lines.append("## Duplicates remaining")
        lines.append("| Title | Count | Item IDs |")
        lines.append("|---|---:|---|")
        for d in report["duplicates"]:
            ids = ", ".join(i[:8] + "…" for i in d["item_ids"])
            lines.append(f"| {d['title']} | {d['count']} | {ids} |")
        lines.append("")

    lines.append("## Anomalies detected")
    if report["anomalies"]:
        for a in report["anomalies"]:
            lines.append(f"- {a}")
    else:
        lines.append("- None.")
    lines.append("")

    lines.append("## Sources used (API endpoints)")
    for s in report["sources"]:
        lines.append(f"- {s}")
    lines.append("")

    return "\n".join(lines)


def render_brief(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"Docspell health report — {report['generated_at']}")
    lines.append(f"Docspell URL: {report['docspell_url']}")
    lines.append(f"Docspell version: {report['docspell_version']}")
    lines.append(f"Mode: {report['mode']}")
    lines.append("")
    lines.append("Summary:")
    s = report["summary"]
    lines.append(f"  total items:          {s['total_items']}")
    lines.append(f"  inbox items:          {s['inbox_items']}")
    lines.append(f"  items without folder: {s['items_without_folder']}")
    lines.append(f"  items with book_year: {s['items_with_book_year']}")
    lines.append("")
    lines.append("Verification:")
    for v in report["verification"]:
        lines.append(
            f"  [{_md_bool(v['ok']):>4}] {v['check']:<32s} "
            f"expected={v['expected']!s:<8s} actual={v['actual']}"
        )
    if report["anomalies"]:
        lines.append("")
        lines.append("Anomalies:")
        for a in report["anomalies"]:
            lines.append(f"  - {a}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Docspell health-check; writes out/health-report.{md,json}."
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("DOCSPELL_URL", DEFAULT_URL),
        help="Docspell base URL",
    )
    parser.add_argument(
        "--account",
        default=None,
        help=f"Docspell account (default: prompt or DOCSPELL_ACCOUNT, stub '{DEFAULT_ACCOUNT}')",
    )
    parser.add_argument(
        "--report-md",
        default=DEFAULT_REPORT_MD,
        help=f"Markdown report output path (default: {DEFAULT_REPORT_MD})",
    )
    parser.add_argument(
        "--report-json",
        default=DEFAULT_REPORT_JSON,
        help=f"JSON report output path (default: {DEFAULT_REPORT_JSON})",
    )
    parser.add_argument(
        "--apply-log",
        default=DEFAULT_APPLY_LOG,
        help="Apply log CSV to cross-check Book:* tag counts (default: out/apply-log.csv)",
    )
    parser.add_argument(
        "--brief",
        action="store_true",
        help="Print only Summary + Verification sections to stdout (cron-friendly). "
        "Full reports are still written to --report-md / --report-json unless --no-write is set.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Do not write report files to disk; print results to stdout only.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_url = args.url.rstrip("/")

    dns_preflight(base_url)
    version = check_version(base_url)
    print(f"Docspell URL:     {base_url}")
    print(f"Docspell version: {version.get('version', 'unknown')}")
    print(f"Mode:             read-only")
    print()

    token = login(base_url, args)

    apply_log_path = Path(args.apply_log)
    if not apply_log_path.is_absolute():
        apply_log_path = Path.cwd() / apply_log_path

    report = build_report(base_url, token, version, apply_log_path)

    md_path = Path(args.report_md)
    json_path = Path(args.report_json)
    if not md_path.is_absolute():
        md_path = Path.cwd() / md_path
    if not json_path.is_absolute():
        json_path = Path.cwd() / json_path

    if not args.no_write:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(report), encoding="utf-8")
        json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # --brief is additive: full reports are still written to disk (unless
    # --no-write is set), but only the brief summary is printed to stdout.
    if args.brief:
        print(render_brief(report))
    else:
        if not args.no_write:
            print(f"Report written:")
            print(f"  {md_path}")
            print(f"  {json_path}")
            print()
        print(
            f"Anomalies: {len(report['anomalies'])} | "
            f"Verification fails: "
            f"{sum(1 for v in report['verification'] if v['ok'] is False)}"
        )

    # Exit non-zero only if there are explicit verification failures, so the
    # script is cron-friendly: --brief && nonzero → page the operator.
    fails = sum(1 for v in report["verification"] if v["ok"] is False)
    return 1 if fails else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"Error: {redact(str(exc))}", file=sys.stderr)
        raise SystemExit(1)
