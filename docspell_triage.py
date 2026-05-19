#!/usr/bin/env python3
"""Read-only Docspell triage report generator.

This script is strictly READ-ONLY. It only calls Docspell version, login,
and item search endpoints (GET + POST /open/auth/login for auth bootstrap).
It writes local metadata-only outputs and never downloads attachments or
OCR text. No create/update/delete/tag/move/confirm endpoints are touched.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import re
import shutil
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_URL = "https://docspell.medarov.net"
DEFAULT_QUERIES = [
    'content:"фактура"',
    'content:"invoice"',
    'content:"договор"',
    'content:"contract"',
    'content:"receipt"',
    'content:"касов"',
    'content:"банка"',
    'content:"statement"',
    'content:"застраховка"',
    'content:"policy"',
    'content:"НАП"',
    'content:"A1"',
    'content:"Vivacom"',
    'content:"Yettel"',
    'content:"DSK"',
    'content:"UniCredit"',
    'content:"Postbank"',
    "!exist:folder",
    "inbox:yes",
]


@dataclass
class Suggestion:
    item_id: str
    title: str
    current_tags: set[str] = field(default_factory=set)
    suggested_tags: set[str] = field(default_factory=set)
    suggested_folder: str = ""
    suggested_correspondent: str = ""
    confidence: float = 0.0
    reasons: set[str] = field(default_factory=set)
    source_queries: set[str] = field(default_factory=set)


def api_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/api/v1"):
        return f"{base}{path}"
    return f"{base}/api/v1{path}"


def request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
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
        raise RuntimeError(f"HTTP {exc.code} from {url}: {redact(detail)}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc

    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def redact(text: str) -> str:
    text = re.sub(r'("token"\s*:\s*")[^"]+', r"\1<redacted>", text)
    text = re.sub(r"(X-Docspell-Auth:\s*)\S+", r"\1<redacted>", text, flags=re.I)
    return text


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
    # The standard Docspell version endpoint is /api/info/version. The
    # /api/v1/info/version fallback is kept only as a safety net for unusual
    # reverse-proxy setups. Previously this list included
    # api_url(base, "/api/info/version") which double-prepends /api/v1 and
    # produces the nonsense URL /api/v1/api/info/version — fixed below.
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


def search_items(
    base_url: str,
    token: str,
    query: str,
    *,
    limit: int,
    max_items: int,
) -> list[dict[str, Any]]:
    offset = 0
    items: list[dict[str, Any]] = []
    while len(items) < max_items:
        params = urllib.parse.urlencode(
            {
                "q": query,
                "limit": min(limit, max_items - len(items)),
                "offset": offset,
                "withDetails": "true",
                "searchMode": "normal",
            }
        )
        data = request_json("GET", f"{api_url(base_url, '/sec/item/search')}?{params}", token=token)
        batch = flatten_search_items(data)
        if not batch:
            break
        items.extend(batch)
        if len(batch) < limit:
            break
        offset += len(batch)
    return items


def flatten_search_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for group in data.get("groups", []) or []:
        for item in group.get("items", []) or []:
            if isinstance(item, dict):
                items.append(item)
    return items


def ref_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    if isinstance(value, str):
        return value
    return ""


def tag_names(item: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for tag in item.get("tags", []) or []:
        if isinstance(tag, dict) and tag.get("name"):
            names.add(str(tag["name"]))
        elif isinstance(tag, str):
            names.add(tag)
    return names


def sanitize_item(item: dict[str, Any]) -> dict[str, Any]:
    folder = item.get("folder")
    return {
        "id": item.get("id", ""),
        "title": item.get("name", ""),
        "state": item.get("state", ""),
        "date": item.get("date"),
        "dueDate": item.get("dueDate"),
        "source": item.get("source", ""),
        "direction": item.get("direction", ""),
        "folder": ref_name(folder),
        "tags": sorted(tag_names(item), key=str.casefold),
        "corrOrg": ref_name(item.get("corrOrg")),
        "corrPerson": ref_name(item.get("corrPerson")),
        "concPerson": ref_name(item.get("concPerson")),
        "concEquipment": ref_name(item.get("concEquipment")),
        "attachmentCount": len(item.get("attachments", []) or []),
    }


def slug_query(query: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9А-Яа-я]+", "-", query).strip("-").lower()
    return slug or "query"


def add_rule(
    suggestion: Suggestion,
    *,
    query: str,
    tag: str | None = None,
    correspondent: str | None = None,
    confidence: float,
    reason: str,
) -> None:
    suggestion.source_queries.add(query)
    suggestion.confidence = max(suggestion.confidence, confidence)
    suggestion.reasons.add(reason)
    if tag and tag.casefold() not in {t.casefold() for t in suggestion.current_tags}:
        suggestion.suggested_tags.add(tag)
    if correspondent and not suggestion.suggested_correspondent:
        suggestion.suggested_correspondent = correspondent


def apply_rules(query: str, item: dict[str, Any], suggestion: Suggestion) -> None:
    q = query.casefold()
    title = str(item.get("name") or "")
    haystack = " ".join(
        [
            query,
            title,
            ref_name(item.get("corrOrg")),
            ref_name(item.get("corrPerson")),
        ]
    ).casefold()

    if "фактура" in q or "invoice" in q:
        add_rule(suggestion, query=query, tag="invoice", confidence=0.70, reason="matched invoice/faktura search")
    if "договор" in q or "contract" in q:
        add_rule(suggestion, query=query, tag="contract", confidence=0.70, reason="matched contract/dogovor search")
    if "receipt" in q or "касов" in q:
        add_rule(suggestion, query=query, tag="receipt", confidence=0.70, reason="matched receipt/kasov search")
    if "statement" in q or "банка" in q or "bank" in q:
        add_rule(suggestion, query=query, tag="bank", confidence=0.65, reason="matched bank statement search")
    if "застраховка" in q or "policy" in q:
        add_rule(suggestion, query=query, tag="insurance", confidence=0.70, reason="matched insurance/policy search")
    if "нап" in q or "нап" in haystack:
        add_rule(
            suggestion,
            query=query,
            tag="tax",
            correspondent="НАП",
            confidence=0.85,
            reason="matched НАП tax authority signal",
        )

    providers = {
        "a1": ("telecom", "A1"),
        "vivacom": ("telecom", "Vivacom"),
        "yettel": ("telecom", "Yettel"),
        "dsk": ("banking", "DSK"),
        "unicredit": ("banking", "UniCredit"),
        "postbank": ("banking", "Postbank"),
    }
    for needle, (tag, correspondent) in providers.items():
        if needle in q or needle in haystack:
            add_rule(
                suggestion,
                query=query,
                tag=tag,
                correspondent=correspondent,
                confidence=0.85,
                reason=f"matched obvious {correspondent} signal",
            )

    if query in {"!exist:folder", "inbox:yes"}:
        suggestion.source_queries.add(query)


def build_suggestions(results_by_query: dict[str, list[dict[str, Any]]]) -> dict[str, Suggestion]:
    suggestions: dict[str, Suggestion] = {}
    for query, items in results_by_query.items():
        for item in items:
            item_id = str(item.get("id") or "")
            if not item_id:
                continue
            suggestion = suggestions.setdefault(
                item_id,
                Suggestion(
                    item_id=item_id,
                    title=str(item.get("name") or ""),
                    current_tags=tag_names(item),
                ),
            )
            suggestion.current_tags.update(tag_names(item))
            apply_rules(query, item, suggestion)
    return suggestions


def write_actions_csv(path: Path, suggestions: dict[str, Suggestion]) -> int:
    fields = [
        "item_id",
        "title",
        "current_tags",
        "suggested_add_tags",
        "suggested_folder",
        "suggested_correspondent",
        "confidence",
        "reason",
        "source_query",
    ]
    rows_written = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for suggestion in sorted(suggestions.values(), key=lambda s: (s.title.casefold(), s.item_id)):
            has_action = bool(
                suggestion.suggested_tags
                or suggestion.suggested_folder
                or suggestion.suggested_correspondent
            )
            if not has_action:
                continue
            writer.writerow(
                {
                    "item_id": suggestion.item_id,
                    "title": suggestion.title,
                    "current_tags": ";".join(sorted(suggestion.current_tags, key=str.casefold)),
                    "suggested_add_tags": ";".join(sorted(suggestion.suggested_tags, key=str.casefold)),
                    "suggested_folder": suggestion.suggested_folder,
                    "suggested_correspondent": suggestion.suggested_correspondent,
                    "confidence": f"{suggestion.confidence:.2f}",
                    "reason": "; ".join(sorted(suggestion.reasons)),
                    "source_query": "; ".join(sorted(suggestion.source_queries)),
                }
            )
            rows_written += 1
    return rows_written


def write_summary(
    path: Path,
    *,
    base_url: str,
    version: dict[str, Any],
    dsc_path: str | None,
    results_by_query: dict[str, list[dict[str, Any]]],
    csv_rows: int,
) -> None:
    total_matches = sum(len(items) for items in results_by_query.values())
    unique_items = {str(item.get("id")) for items in results_by_query.values() for item in items if item.get("id")}
    lines = [
        "# Docspell triage summary",
        "",
        f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S %z')}",
        f"- Docspell URL: {base_url}",
        f"- Docspell version: {version.get('version', 'unknown')}",
        f"- dsc on PATH: {dsc_path or 'not found'}",
        "- Mode: read-only HTTP API search",
        f"- Queries run: {len(results_by_query)}",
        f"- Total query matches: {total_matches}",
        f"- Unique matched items: {len(unique_items)}",
        f"- Suggested action rows: {csv_rows}",
        "",
        "## Query counts",
        "",
    ]
    for query, items in results_by_query.items():
        lines.append(f"- `{query}`: {len(items)}")
    lines.extend(
        [
            "",
            "## Safety notes",
            "",
            "- The script only used version, login, and item search endpoints.",
            "- No create, update, delete, tag, confirm, move, attachment download, or text extraction endpoints were called.",
            "- Raw search output files contain sanitized metadata only, not document bodies or OCR text.",
            "- Suggested CSV rows are recommendations only; no Docspell data was changed.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate read-only Docspell triage outputs.")
    parser.add_argument("--url", default=os.environ.get("DOCSPELL_URL", DEFAULT_URL), help="Docspell base URL")
    parser.add_argument("--account", help="Docspell account name; default prompts or DOCSPELL_ACCOUNT")
    parser.add_argument("--out", default="out", help="Output directory")
    parser.add_argument("--limit", type=int, default=100, help="Page size per query")
    parser.add_argument("--max-items", type=int, default=1000, help="Maximum items per query")
    parser.add_argument("--version-only", action="store_true", help="Only check version and dsc availability")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_url = args.url.rstrip("/")
    dsc_path = shutil.which("dsc")

    dns_preflight(base_url)
    version = check_version(base_url)
    print(f"Docspell version: {version.get('version', 'unknown')}")
    print(f"dsc: {dsc_path or 'not found; using read-only HTTP API'}")
    if args.version_only:
        return 0

    token = login(base_url, args)

    out_dir = Path(args.out)
    searches_dir = out_dir / "docspell-searches"
    searches_dir.mkdir(parents=True, exist_ok=True)

    results_by_query: dict[str, list[dict[str, Any]]] = {}
    for index, query in enumerate(DEFAULT_QUERIES, start=1):
        print(f"[{index}/{len(DEFAULT_QUERIES)}] Searching: {query}")
        items = search_items(base_url, token, query, limit=args.limit, max_items=args.max_items)
        results_by_query[query] = items
        sanitized = {
            "query": query,
            "count": len(items),
            "items": [sanitize_item(item) for item in items],
        }
        filename = f"{index:02d}-{slug_query(query)}.json"
        (searches_dir / filename).write_text(
            json.dumps(sanitized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    suggestions = build_suggestions(results_by_query)
    csv_rows = write_actions_csv(out_dir / "docspell-actions.csv", suggestions)
    write_summary(
        out_dir / "docspell-summary.md",
        base_url=base_url,
        version=version,
        dsc_path=dsc_path,
        results_by_query=results_by_query,
        csv_rows=csv_rows,
    )
    print(f"Wrote {searches_dir}")
    print(f"Wrote {out_dir / 'docspell-actions.csv'} ({csv_rows} suggestion rows)")
    print(f"Wrote {out_dir / 'docspell-summary.md'}")
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
