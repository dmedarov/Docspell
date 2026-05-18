#!/usr/bin/env python3
"""Find and classify book-like documents in Docspell.

Modes:
  scan         - login to Docspell, search item metadata, classify candidates
  classify-csv - classify an existing CSV such as out/docspell-actions.csv
  apply        - dry-run/apply reviewed safe book actions to Docspell

The scan/classify modes are read-only. They do not download attachments or OCR
text; they use Docspell item search metadata plus a local seed catalog.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import difflib
import getpass
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_URL = "https://docspell.medarov.net"
AUTH_HEADER = "X-Docspell-Auth"


# ------------------------- small utilities -------------------------


def now_millis() -> int:
    return int(time.time() * 1000)


def utc_now_str() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def slugify(value: str, max_len: int = 80) -> str:
    value = normalize_text(value).lower()
    value = re.sub(r"[^a-z0-9а-яё]+", "-", value, flags=re.I).strip("-")
    return value[:max_len] or "x"


def normalize_text(text: Any) -> str:
    if text is None:
        return ""
    text = str(text)
    text = text.replace("\u00ad", "").replace("\ufffe", "")
    text = text.replace("_", " ").replace("+", " ")
    text = re.sub(r"[\u2010-\u2015]", "-", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_title(title: str) -> str:
    title = normalize_text(title)
    # Strip file extension and common release/watermark prefixes.
    title = re.sub(r"\.(pdf|epub|mobi|docx?|txt)$", "", title, flags=re.I)
    title = re.sub(r"^_?OceanofPDF\.com[_\s-]*", "", title, flags=re.I)
    title = re.sub(r"^\d{5,12}[\s_-]+", "", title)
    title = re.sub(r"\[(?:19|20)\d{2}\](?:\[[A-Z]\])?", "", title)
    title = re.sub(r"\bconverted\b", "", title, flags=re.I)
    title = title.replace(" . ", " ").replace("..", ".")
    title = re.sub(r"\s+-\s+", " - ", title)
    title = re.sub(r"\s+", " ", title).strip(" -_.")
    return title


def tokens(text: str) -> set[str]:
    text = normalize_title(text).lower()
    toks = re.findall(r"[a-zа-яё0-9]{3,}", text, flags=re.I)
    stop = {
        "the", "and", "for", "with", "from", "into", "this", "that", "edition", "pdf", "ebook",
        "amazon", "com", "books", "download", "free", "www", "http", "https", "2010", "2011", "2012",
        "2013", "2014", "2015", "2016", "2017", "2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025"
    }
    return {t for t in toks if t not in stop}


def token_score(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    # Dice coefficient works better than raw Jaccard for truncated titles.
    return (2.0 * inter) / (len(ta) + len(tb))


def seq_score(a: str, b: str) -> float:
    aa, bb = normalize_title(a).lower(), normalize_title(b).lower()
    if not aa or not bb:
        return 0.0
    if aa in bb or bb in aa:
        small, big = (aa, bb) if len(aa) <= len(bb) else (bb, aa)
        if len(tokens(small)) >= 2:
            return 0.95
    return difflib.SequenceMatcher(None, aa, bb).ratio()


def combined_match_score(a: str, b: str) -> float:
    return max(seq_score(a, b), token_score(a, b))


def split_semicolon(value: Any) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return []
    parts = re.split(r"[;,]", text)
    return [p.strip() for p in parts if p.strip()]


def first_nonempty(*values: Any) -> str:
    for v in values:
        s = normalize_text(v)
        if s and s.lower() != "nan":
            return s
    return ""


# ------------------------- data classes -------------------------


@dataclass
class SeedBook:
    source_pdf: str
    directory: str
    title: str
    authors: str = ""
    publisher: str = ""
    published: str = ""
    categories: str = ""
    default_tags: list[str] = field(default_factory=list)
    norm_title: str = ""

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "SeedBook":
        title = first_nonempty(row.get("seed_title"), row.get("title"))
        return cls(
            source_pdf=row.get("source_pdf", ""),
            directory=row.get("directory", ""),
            title=title,
            authors=row.get("authors", ""),
            publisher=row.get("publisher", ""),
            published=row.get("published", ""),
            categories=row.get("categories", ""),
            default_tags=split_semicolon(row.get("default_tags", "")),
            norm_title=normalize_title(title),
        )


@dataclass
class CandidateItem:
    item_id: str
    title: str
    current_tags: list[str] = field(default_factory=list)
    current_folder: str = ""
    page_count: int = 0
    source_queries: set[str] = field(default_factory=set)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Classification:
    item_id: str
    original_title: str
    normalized_title: str
    review_decision: str
    suggested_folder: str
    suggested_tags: list[str]
    confidence: float
    reason: str
    seed_match_title: str = ""
    seed_match_score: float = 0.0
    detected_authors: str = ""
    detected_publisher: str = ""
    detected_published: str = ""
    current_tags: list[str] = field(default_factory=list)
    current_folder: str = ""
    page_count: int = 0
    source_queries: list[str] = field(default_factory=list)
    enrichment_source: str = ""
    enrichment_title: str = ""
    enrichment_authors: str = ""
    enrichment_year: str = ""
    enrichment_publisher: str = ""
    enrichment_isbn13: str = ""
    enrichment_id: str = ""
    enrichment_url: str = ""
    enrichment_categories: str = ""
    enrichment_match_score: float = 0.0
    enrichment_reason: str = ""


# ------------------------- config and taxonomy -------------------------


def load_rules(path: Path | None) -> dict[str, Any]:
    if path is None:
        path = Path(__file__).with_name("book_classifier_rules.json")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_seed_catalog(path: Path | None) -> list[SeedBook]:
    if path is None:
        path = Path(__file__).with_name("seed_catalog.csv")
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return [SeedBook.from_row(row) for row in csv.DictReader(f)]


def infer_keyword_tags(text: str, rules: dict[str, Any]) -> tuple[list[str], list[str]]:
    blob = normalize_title(text).lower()
    tags: list[str] = []
    reasons: list[str] = []
    for tag, needles in rules.get("tag_keywords", {}).items():
        for needle in needles:
            if needle.lower() in blob:
                tags.append(tag)
                reasons.append(f"keyword:{needle}->{tag}")
                break
    return sorted(set(tags), key=tags.index), reasons


def is_obvious_nonbook(title: str, rules: dict[str, Any], page_count: int = 0) -> tuple[bool, str]:
    blob = normalize_title(title).lower()
    for needle in rules.get("obvious_nonbook_keywords", []):
        if needle.lower() in blob:
            # Some books contain these words; only hard-negative short/administrative files.
            if page_count and page_count >= 40:
                return False, ""
            if len(tokens(title)) <= 8 or re.search(r"\b(inv|invoice|receipt|statement)[-_\s]*\d", blob):
                return True, f"obvious non-book title keyword:{needle}"
    return False, ""


def best_seed_match(title: str, seeds: list[SeedBook]) -> tuple[Optional[SeedBook], float]:
    best: Optional[SeedBook] = None
    best_score = 0.0
    nt = normalize_title(title)
    nt_lower = nt.lower()
    item_tokens = tokens(nt)
    for seed in seeds:
        st = getattr(seed, "_tokens", None)
        if st is None:
            st = tokens(seed.norm_title)
            setattr(seed, "_tokens", st)
        if item_tokens and st and not (item_tokens & st):
            continue
        # Fast token score first; avoid expensive SequenceMatcher for obviously unrelated titles.
        if item_tokens and st:
            inter = len(item_tokens & st)
            tscore = (2.0 * inter) / (len(item_tokens) + len(st))
        else:
            tscore = 0.0
        sl = seed.norm_title.lower()
        if nt_lower in sl or sl in nt_lower:
            sscore = 0.95 if min(len(tokens(nt_lower)), len(tokens(sl))) >= 2 else 0.0
        elif tscore >= 0.22:
            sscore = difflib.SequenceMatcher(None, nt_lower, sl).ratio()
        else:
            sscore = 0.0
        score = max(tscore, sscore)
        # Author overlap can help with ambiguous short titles.
        if score >= 0.45 and seed.authors:
            if tokens(seed.authors) & item_tokens:
                score = min(1.0, score + 0.10)
        if score > best_score:
            best = seed
            best_score = score
    return best, best_score


def classify_item(item: CandidateItem, seeds: list[SeedBook], rules: dict[str, Any], online_enricher: Any = None) -> Classification:
    title = item.title
    nt = normalize_title(title)
    base_tags = list(rules.get("base_tags", ["library", "book"]))
    target_folder = rules.get("target_folder", "Library")
    reasons: list[str] = []
    tags: list[str] = list(base_tags)
    score = 0.0

    seed, seed_score = best_seed_match(nt, seeds)
    if seed:
        reasons.append(f"seed_match={seed_score:.2f}:{seed.title}")
    if seed and seed_score >= 0.90:
        score += 0.62
        tags.extend(seed.default_tags)
    elif seed and seed_score >= 0.80:
        score += 0.50
        tags.extend(seed.default_tags)
    elif seed and seed_score >= 0.68:
        score += 0.36
        tags.extend(seed.default_tags)
    elif seed and seed_score >= 0.55:
        score += 0.22
        tags.extend(seed.default_tags[:4])

    kw_tags, kw_reasons = infer_keyword_tags(nt + " " + (seed.categories if seed else "") + " " + (seed.directory if seed else ""), rules)
    if kw_tags:
        score += min(0.25, 0.06 * len(kw_tags) + 0.05)
        tags.extend(kw_tags)
        reasons.extend(kw_reasons[:4])

    strong_hits = []
    blob = nt.lower()
    for needle in rules.get("strong_book_keywords", []):
        if needle.lower() in blob:
            strong_hits.append(needle)
    if strong_hits:
        score += min(0.24, 0.08 + 0.03 * len(strong_hits))
        reasons.append("bookish_keywords=" + ",".join(strong_hits[:5]))

    if re.search(r"\b(?:97[89][0-9]{10}|[0-9]{9}[0-9xX])\b", nt.replace("-", "")):
        score += 0.18
        reasons.append("ISBN-like number in title")

    if item.page_count >= 120:
        score += 0.30
        reasons.append(f"page_count>={item.page_count}")
    elif item.page_count >= 60:
        score += 0.22
        reasons.append(f"page_count>={item.page_count}")
    elif item.page_count >= 30:
        score += 0.12
        reasons.append(f"page_count>={item.page_count}")

    # Long PDF filenames with many meaningful tokens are usually books in this data set.
    if len(tokens(nt)) >= 5 and nt.lower().endswith("pdf") is False:
        score += 0.08
        reasons.append("long descriptive filename")

    nonbook, nonbook_reason = is_obvious_nonbook(title, rules, item.page_count)
    if nonbook and seed_score < 0.72:
        score -= 0.35
        reasons.append(nonbook_reason)

    enrichment_source = enrichment_title = enrichment_authors = ""
    enrichment_year = enrichment_publisher = enrichment_isbn13 = ""
    enrichment_id = enrichment_url = enrichment_categories = ""
    enrichment_match_score = 0.0
    enrichment_reason = ""
    online_min = float(rules.get("online_min_score", 0.45))
    if online_enricher and score >= online_min:
        enriched = online_enricher(nt, seed.authors if seed else "")
        if enriched:
            enrichment_source = enriched.get("source", "")
            enrichment_title = enriched.get("title", "")
            authors_value = enriched.get("authors", [])
            enrichment_authors = "; ".join(authors_value) if isinstance(authors_value, list) else str(authors_value or "")
            enrichment_year = str(enriched.get("year", "") or "")
            enrichment_publisher = str(enriched.get("publisher", "") or "")
            enrichment_isbn13 = str(enriched.get("isbn13", "") or "")
            enrichment_id = str(enriched.get("id", "") or "")
            enrichment_url = str(enriched.get("url", "") or "")
            categories_value = enriched.get("categories", [])
            enrichment_categories = "; ".join(categories_value) if isinstance(categories_value, list) else str(categories_value or "")
            try:
                enrichment_match_score = float(enriched.get("match_score", 0) or 0)
            except Exception:
                enrichment_match_score = 0.0
            enrichment_reason = str(enriched.get("match_reason", "") or "")
            if enrichment_match_score >= 0.78:
                score = min(1.0, score + 0.14)
                reasons.append(f"online_metadata_match={enrichment_source}:{enrichment_match_score:.2f}")
            elif enrichment_match_score >= 0.66:
                score = min(1.0, score + 0.08)
                reasons.append(f"online_metadata_possible={enrichment_source}:{enrichment_match_score:.2f}")
            # Use online categories only as weak thematic hints. Never remove tags.
            online_kw_tags, online_kw_reasons = infer_keyword_tags(
                " ".join([enrichment_title, enrichment_authors, enrichment_publisher, enrichment_categories]),
                rules,
            )
            if online_kw_tags and enrichment_match_score >= 0.66:
                tags.extend(online_kw_tags[:4])
                reasons.extend(["online_" + r for r in online_kw_reasons[:3]])

    confidence = max(0.0, min(1.0, round(score, 3)))
    if confidence >= float(rules.get("safe_threshold", 0.82)):
        decision = "safe_book"
    elif confidence >= float(rules.get("probable_threshold", 0.65)):
        decision = "probable_book"
    elif confidence >= float(rules.get("manual_threshold", 0.45)):
        decision = "manual_review"
    else:
        decision = "reject"

    # Keep tags clean and not excessive.
    tags = [t for t in tags if t]
    seen = set()
    clean_tags = []
    for t in tags:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            clean_tags.append(t)
    if "library" not in seen:
        clean_tags.insert(0, "library")
    if "book" not in seen:
        clean_tags.insert(1, "book")
    clean_tags = clean_tags[:10]

    return Classification(
        item_id=item.item_id,
        original_title=title,
        normalized_title=nt,
        review_decision=decision,
        suggested_folder=target_folder if decision != "reject" else "",
        suggested_tags=clean_tags if decision != "reject" else [],
        confidence=confidence,
        reason="; ".join(reasons) if reasons else "weak/no book signals",
        seed_match_title=seed.title if seed else "",
        seed_match_score=round(seed_score, 3),
        detected_authors=seed.authors if seed else "",
        detected_publisher=seed.publisher if seed else "",
        detected_published=seed.published if seed else "",
        current_tags=item.current_tags,
        current_folder=item.current_folder,
        page_count=item.page_count,
        source_queries=sorted(item.source_queries),
        enrichment_source=enrichment_source,
        enrichment_title=enrichment_title,
        enrichment_authors=enrichment_authors,
        enrichment_year=enrichment_year,
        enrichment_publisher=enrichment_publisher,
        enrichment_isbn13=enrichment_isbn13,
        enrichment_id=enrichment_id,
        enrichment_url=enrichment_url,
        enrichment_categories=enrichment_categories,
        enrichment_match_score=round(enrichment_match_score, 3),
        enrichment_reason=enrichment_reason,
    )


# ------------------------- HTTP / Docspell client -------------------------


class DocspellClient:
    def __init__(self, base_url: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token: Optional[str] = None

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + "/api/v1" + path

    def request(self, method: str, path: str, data: Any = None, params: dict[str, Any] | None = None, auth: bool = True) -> Any:
        url = self._url(path)
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        body = None
        headers = {"Accept": "application/json"}
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth and self.token:
            headers[AUTH_HEADER] = self.token
        req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                content = resp.read()
                if not content:
                    return {}
                text = content.decode("utf-8", errors="replace")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"raw": text}
        except urllib.error.HTTPError as e:
            payload = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code} {method} {path}: {payload[:500]}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"HTTP error {method} {path}: {e}") from None

    def login(self, account: str, password: str | None = None) -> None:
        if password is None:
            password = getpass.getpass("Docspell password: ")
        res = self.request("POST", "/open/auth/login", {"account": account, "password": password, "rememberMe": False}, auth=False)
        if not res.get("success"):
            raise RuntimeError("Docspell login failed: " + str(res.get("message", "unknown error")))
        token = res.get("token")
        if res.get("requireSecondFactor"):
            otp = getpass.getpass("Docspell OTP/TOTP: ")
            res2 = self.request("POST", "/open/auth/two-factor", {"token": token, "otp": otp, "rememberMe": False}, auth=False)
            if not res2.get("success"):
                raise RuntimeError("Docspell 2FA failed: " + str(res2.get("message", "unknown error")))
            token = res2.get("token")
        if not token:
            raise RuntimeError("Docspell login response did not include a token")
        self.token = token

    def search_items(self, query: str, limit: int = 200, with_details: bool = True, max_pages: int = 100) -> list[dict[str, Any]]:
        all_items: list[dict[str, Any]] = []
        offset = 0
        for _ in range(max_pages):
            res = self.request("POST", "/sec/item/search", {
                "query": query,
                "offset": offset,
                "limit": limit,
                "withDetails": with_details,
                "searchMode": "normal",
            })
            items = flatten_search_items(res)
            all_items.extend(items)
            got = len(items)
            if got < limit or got == 0:
                break
            offset += got
        return all_items

    def get_item(self, item_id: str) -> dict[str, Any]:
        return self.request("GET", f"/sec/item/{urllib.parse.quote(item_id)}")

    def list_folders(self) -> list[dict[str, Any]]:
        res = self.request("GET", "/sec/folder", params={"full": "true"})
        return res.get("items", []) if isinstance(res, dict) else []

    def create_folder(self, name: str) -> str:
        res = self.request("POST", "/sec/folder", {"name": name})
        if not res.get("success"):
            raise RuntimeError("Failed to create folder: " + str(res))
        return res.get("id") or ""

    def list_tags(self) -> list[dict[str, Any]]:
        res = self.request("GET", "/sec/tag")
        return res.get("items", []) if isinstance(res, dict) else []

    def create_tag(self, name: str, category: str = "library") -> str:
        tag_id = f"{slugify(name, 32)}-{uuid.uuid4().hex[:8]}"
        res = self.request("POST", "/sec/tag", {"id": tag_id, "name": name, "category": category, "created": now_millis()})
        if not res.get("success"):
            raise RuntimeError("Failed to create tag: " + str(res))
        return tag_id

    def set_item_folder(self, item_id: str, folder_id: str) -> dict[str, Any]:
        return self.request("PUT", f"/sec/item/{urllib.parse.quote(item_id)}/folder", {"id": folder_id})

    def link_item_tags(self, item_id: str, tag_names_or_ids: list[str]) -> dict[str, Any]:
        return self.request("PUT", f"/sec/item/{urllib.parse.quote(item_id)}/taglink", {"items": tag_names_or_ids})


# ------------------------- search/output helpers -------------------------


def flatten_search_items(res: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for group in res.get("groups", []) if isinstance(res, dict) else []:
        items.extend(group.get("items", []) if isinstance(group, dict) else [])
    return items


def item_from_docspell(raw: dict[str, Any], source_query: str = "") -> CandidateItem:
    item_id = first_nonempty(raw.get("id"), raw.get("item_id"))
    title = first_nonempty(raw.get("name"), raw.get("title"), raw.get("filename"), item_id)
    current_tags = []
    for t in raw.get("tags", []) or []:
        if isinstance(t, dict):
            current_tags.append(first_nonempty(t.get("name"), t.get("id")))
        else:
            current_tags.append(str(t))
    folder = ""
    if isinstance(raw.get("folder"), dict):
        folder = first_nonempty(raw["folder"].get("name"), raw["folder"].get("id"))
    elif raw.get("folder"):
        folder = str(raw.get("folder"))
    page_count = 0
    for a in raw.get("attachments", []) or []:
        if isinstance(a, dict):
            try:
                page_count += int(a.get("pageCount") or 0)
            except Exception:
                pass
    return CandidateItem(item_id=item_id, title=title, current_tags=current_tags, current_folder=folder, page_count=page_count, source_queries={source_query} if source_query else set(), raw=raw)


def read_candidates_from_csv(path: Path) -> list[CandidateItem]:
    candidates: dict[str, CandidateItem] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            item_id = first_nonempty(row.get("item_id"), row.get("id"))
            title = first_nonempty(row.get("title"), row.get("name"), row.get("filename"), item_id)
            if not item_id:
                continue
            c = candidates.get(item_id)
            if not c:
                c = CandidateItem(
                    item_id=item_id,
                    title=title,
                    current_tags=split_semicolon(row.get("current_tags", "")),
                    current_folder=first_nonempty(row.get("current_folder"), row.get("folder")),
                    page_count=int(float(row.get("page_count") or 0)) if str(row.get("page_count") or "").strip() else 0,
                    raw=dict(row),
                )
                candidates[item_id] = c
            for q in split_semicolon(row.get("source_query", "")) + split_semicolon(row.get("original_source_query", "")):
                c.source_queries.add(q)
    return list(candidates.values())


def merge_candidates(target: dict[str, CandidateItem], items: Iterable[CandidateItem]) -> None:
    for item in items:
        if not item.item_id:
            continue
        existing = target.get(item.item_id)
        if existing is None:
            target[item.item_id] = item
        else:
            existing.source_queries.update(item.source_queries)
            if not existing.current_tags and item.current_tags:
                existing.current_tags = item.current_tags
            if not existing.current_folder and item.current_folder:
                existing.current_folder = item.current_folder
            if item.page_count > existing.page_count:
                existing.page_count = item.page_count


DEFAULT_QUERIES = [
    "!exist:folder",
    "inbox:yes",
    'content:"ISBN"',
    'content:"Publisher"',
    'content:"Published"',
    'content:"Bookboon"',
    'content:"Mises Institute"',
    'content:"Open Textbook Library"',
    'content:"University Press"',
    'content:"McGraw"',
    'content:"Princeton University Press"',
    'content:"Economics"',
    'content:"Money"',
    'content:"Banking"',
    'content:"Macroeconomics"',
    'content:"Microeconomics"',
    'content:"Management"',
    'content:"Project Management"',
]


def seed_queries(seeds: list[SeedBook], max_queries: int = 80) -> list[str]:
    # Use selected high-value title/author fragments. Too many queries can be slow.
    picks: list[str] = []
    seen = set()
    for seed in seeds:
        t = normalize_title(seed.title)
        if len(tokens(t)) < 2:
            continue
        # Very long exact phrases can fail; use first distinctive title segment.
        phrase = re.split(r"[:(]", t)[0].strip()
        phrase = re.sub(r"^\d+\s+", "", phrase)
        if 8 <= len(phrase) <= 70 and phrase.lower() not in seen:
            seen.add(phrase.lower())
            picks.append(f'content:"{phrase}"')
        if len(picks) >= max_queries:
            break
    return picks


def write_classifications(out_dir: Path, classes: list[Classification], prefix: str = "book-actions") -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "item_id", "original_title", "normalized_title", "review_decision", "suggested_folder", "suggested_tags",
        "confidence", "reason", "seed_match_title", "seed_match_score", "detected_authors", "detected_publisher",
        "detected_published", "current_tags", "current_folder", "page_count", "source_queries", "enrichment_source",
        "enrichment_title", "enrichment_authors", "enrichment_year", "enrichment_publisher", "enrichment_isbn13",
        "enrichment_id", "enrichment_url", "enrichment_categories", "enrichment_match_score", "enrichment_reason",
    ]
    def row(c: Classification) -> dict[str, Any]:
        return {
            "item_id": c.item_id,
            "original_title": c.original_title,
            "normalized_title": c.normalized_title,
            "review_decision": c.review_decision,
            "suggested_folder": c.suggested_folder,
            "suggested_tags": ";".join(c.suggested_tags),
            "confidence": f"{c.confidence:.3f}",
            "reason": c.reason,
            "seed_match_title": c.seed_match_title,
            "seed_match_score": f"{c.seed_match_score:.3f}",
            "detected_authors": c.detected_authors,
            "detected_publisher": c.detected_publisher,
            "detected_published": c.detected_published,
            "current_tags": ";".join(c.current_tags),
            "current_folder": c.current_folder,
            "page_count": c.page_count,
            "source_queries": ";".join(c.source_queries),
            "enrichment_source": c.enrichment_source,
            "enrichment_title": c.enrichment_title,
            "enrichment_authors": c.enrichment_authors,
            "enrichment_year": c.enrichment_year,
            "enrichment_publisher": c.enrichment_publisher,
            "enrichment_isbn13": c.enrichment_isbn13,
            "enrichment_id": c.enrichment_id,
            "enrichment_url": c.enrichment_url,
            "enrichment_categories": c.enrichment_categories,
            "enrichment_match_score": f"{c.enrichment_match_score:.3f}",
            "enrichment_reason": c.enrichment_reason,
        }
    def write_csv(path: Path, rows: list[Classification]) -> None:
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for c in rows:
                writer.writerow(row(c))
    classes_sorted = sorted(classes, key=lambda c: (-c.confidence, c.review_decision, c.original_title.lower()))
    write_csv(out_dir / f"{prefix}.csv", classes_sorted)
    for decision in ["safe_book", "probable_book", "manual_review", "reject"]:
        write_csv(out_dir / f"{prefix}-{decision}.csv", [c for c in classes_sorted if c.review_decision == decision])
    write_enrichment_csv(out_dir / "book-enrichment.csv", [c for c in classes_sorted if c.enrichment_source])
    write_summary(out_dir / "book-summary.md", classes_sorted)


def write_enrichment_csv(path: Path, rows: list[Classification]) -> None:
    fieldnames = [
        "item_id", "original_title", "normalized_title", "review_decision", "confidence",
        "enrichment_source", "enrichment_match_score", "enrichment_title", "enrichment_authors",
        "enrichment_year", "enrichment_publisher", "enrichment_isbn13", "enrichment_id",
        "enrichment_url", "enrichment_categories", "enrichment_reason",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for c in sorted(rows, key=lambda x: (-x.enrichment_match_score, x.original_title.lower())):
            writer.writerow({
                "item_id": c.item_id,
                "original_title": c.original_title,
                "normalized_title": c.normalized_title,
                "review_decision": c.review_decision,
                "confidence": f"{c.confidence:.3f}",
                "enrichment_source": c.enrichment_source,
                "enrichment_match_score": f"{c.enrichment_match_score:.3f}",
                "enrichment_title": c.enrichment_title,
                "enrichment_authors": c.enrichment_authors,
                "enrichment_year": c.enrichment_year,
                "enrichment_publisher": c.enrichment_publisher,
                "enrichment_isbn13": c.enrichment_isbn13,
                "enrichment_id": c.enrichment_id,
                "enrichment_url": c.enrichment_url,
                "enrichment_categories": c.enrichment_categories,
                "enrichment_reason": c.enrichment_reason,
            })


def write_summary(path: Path, classes: list[Classification]) -> None:
    counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    enrich_counts: dict[str, int] = {}
    for c in classes:
        counts[c.review_decision] = counts.get(c.review_decision, 0) + 1
        if c.enrichment_source:
            enrich_counts[c.enrichment_source] = enrich_counts.get(c.enrichment_source, 0) + 1
        for t in c.suggested_tags:
            tag_counts[t] = tag_counts.get(t, 0) + 1
    with path.open("w", encoding="utf-8") as f:
        f.write("# Docspell book classification summary\n\n")
        f.write(f"- Generated: {utc_now_str()}\n")
        f.write(f"- Total candidate items: {len(classes)}\n")
        for key in ["safe_book", "probable_book", "manual_review", "reject"]:
            f.write(f"- {key}: {counts.get(key, 0)}\n")
        if enrich_counts:
            f.write("- Online enrichment matches: " + ", ".join(f"{k}={v}" for k, v in sorted(enrich_counts.items())) + "\n")
        f.write("\n## Top suggested tags\n\n")
        for tag, n in sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))[:25]:
            f.write(f"- {tag}: {n}\n")
        f.write("\n## Safest examples\n\n")
        for c in [x for x in classes if x.review_decision == "safe_book"][:25]:
            enrich = f" — online: {c.enrichment_source} {c.enrichment_title}" if c.enrichment_source else ""
            f.write(f"- {c.confidence:.2f} — {c.original_title} — tags: {', '.join(c.suggested_tags)}{enrich}\n")


# ------------------------- online enrichment, optional -------------------------


def http_json(url: str, timeout: int = 15) -> Optional[dict[str, Any]]:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "docspell-book-classifier/2.0 (+local metadata enrichment)",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def extract_isbns(text: str) -> list[str]:
    """Return ISBN-10/13 candidates from a title-like string."""
    text = normalize_text(text)
    found: list[str] = []
    # ISBNs often appear with hyphens/spaces. Keep the original groups but normalize digits.
    for m in re.finditer(r"(?i)(?:ISBN(?:-1[03])?[:\s]*)?((?:97[89][\-\s]?)?\d[\d\-\s]{8,}[\dXx])", text):
        raw = re.sub(r"[^0-9Xx]", "", m.group(1))
        if len(raw) in (10, 13) and raw not in found:
            found.append(raw.upper())
    return found


def first_author(author_string: str) -> str:
    parts = split_semicolon(author_string)
    if not parts and author_string:
        parts = [p.strip() for p in re.split(r"\band\b|\||/", author_string) if p.strip()]
    return parts[0] if parts else ""


def author_match_score(query_author: str, result_authors: list[str]) -> float:
    if not query_author or not result_authors:
        return 0.0
    q = tokens(query_author)
    r = tokens(" ".join(result_authors))
    if not q or not r:
        return 0.0
    # Author initials and transliterations make exact matching hard; token overlap is sufficient here.
    inter = len(q & r)
    return min(1.0, (2.0 * inter) / (len(q) + len(r)))


def first_year(value: Any) -> str:
    if value is None:
        return ""
    m = re.search(r"(?:17|18|19|20)\d{2}", str(value))
    return m.group(0) if m else ""


def choose_isbn13(isbns: Iterable[str]) -> str:
    clean = []
    for i in isbns or []:
        s = re.sub(r"[^0-9Xx]", "", str(i)).upper()
        if len(s) in (10, 13):
            clean.append(s)
    for s in clean:
        if len(s) == 13:
            return s
    return clean[0] if clean else ""


def score_online_candidate(query_title: str, query_author: str, candidate: dict[str, Any]) -> tuple[float, str]:
    result_title = normalize_title(candidate.get("title", ""))
    result_authors = candidate.get("authors", []) or []
    if not result_title:
        return 0.0, "no result title"
    title_s = combined_match_score(query_title, result_title)
    author_s = author_match_score(query_author, result_authors)
    query_isbns = set(extract_isbns(query_title + " " + query_author))
    result_isbns = set(extract_isbns(" ".join(candidate.get("isbns", []) or [])))
    isbn_hit = bool(query_isbns & result_isbns)
    # Weighted but conservative: title dominates, author helps, ISBN is decisive.
    score = 0.72 * title_s
    reason = [f"title={title_s:.2f}"]
    if query_author:
        score += 0.22 * author_s
        reason.append(f"author={author_s:.2f}")
    if isbn_hit:
        score = max(score, 0.96)
        reason.append("isbn=1.00")
    # A result with no title overlap is almost always a false positive when searching noisy filenames.
    if title_s < 0.42 and not isbn_hit:
        score = min(score, 0.40)
        reason.append("low-title-overlap")
    elif title_s >= 0.86 and not query_author:
        score += 0.08
    return max(0.0, min(1.0, round(score, 3))), ";".join(reason)


class OnlineBookEnricher:
    """Lookup title/author metadata via public book APIs with caching and rate limiting.

    Privacy boundary: this class sends only normalized title and optional author strings
    to Open Library / Google Books. It never receives Docspell credentials and never sends
    OCR text or file contents.
    """

    def __init__(
        self,
        cache_path: Path,
        providers: str = "both",
        max_results: int = 5,
        delay: float = 0.35,
        timeout: int = 15,
        min_match_score: float = 0.56,
        google_books_api_key: str = "",
    ) -> None:
        self.cache_path = cache_path
        self.providers = providers
        self.max_results = max(1, min(10, int(max_results)))
        self.delay = max(0.0, float(delay))
        self.timeout = timeout
        self.min_match_score = min_match_score
        self.google_books_api_key = google_books_api_key or os.environ.get("GOOGLE_BOOKS_API_KEY", "")
        self.last_call = 0.0
        self.stats = {"cache_hit": 0, "miss": 0, "openlibrary": 0, "googlebooks": 0}
        self.cache: dict[str, Any] = {}
        if cache_path.exists():
            try:
                self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}

    def save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")

    def _cache_key(self, title: str, author: str) -> str:
        payload = json.dumps({
            "v": 3,
            "title": normalize_title(title).lower(),
            "author": normalize_text(author).lower(),
            "providers": self.providers,
            "max_results": self.max_results,
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _throttle(self) -> None:
        if self.delay <= 0:
            return
        elapsed = time.time() - self.last_call
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self.last_call = time.time()

    def __call__(self, title: str, author: str = "") -> Optional[dict[str, Any]]:
        title = normalize_title(title)
        author = normalize_text(author)
        key = self._cache_key(title, author)
        if key in self.cache:
            self.stats["cache_hit"] += 1
            return self.cache[key].get("result")

        candidates: list[dict[str, Any]] = []
        if self.providers in ("both", "openlibrary"):
            candidates.extend(self.query_open_library(title, author))
        if self.providers in ("both", "googlebooks"):
            candidates.extend(self.query_google_books(title, author))

        best: Optional[dict[str, Any]] = None
        best_score = 0.0
        best_reason = ""
        for cand in candidates:
            score, reason = score_online_candidate(title, author, cand)
            if score > best_score:
                best = cand
                best_score = score
                best_reason = reason

        result: Optional[dict[str, Any]] = None
        if best and best_score >= self.min_match_score:
            result = dict(best)
            result["match_score"] = best_score
            result["match_reason"] = best_reason
        else:
            self.stats["miss"] += 1

        self.cache[key] = {
            "ts": utc_now_str(),
            "query_title": title,
            "query_author": author,
            "result": result,
        }
        return result

    def query_open_library(self, title: str, author: str = "") -> list[dict[str, Any]]:
        self._throttle()
        params = {
            "title": title,
            "limit": self.max_results,
            "fields": "key,title,author_name,first_publish_year,publisher,isbn,subject,cover_i",
        }
        fa = first_author(author)
        if fa:
            params["author"] = fa
        ol = http_json("https://openlibrary.org/search.json?" + urllib.parse.urlencode(params), timeout=self.timeout)
        out: list[dict[str, Any]] = []
        if not ol or not ol.get("docs"):
            return out
        self.stats["openlibrary"] += 1
        for d in ol.get("docs", [])[: self.max_results]:
            isbns = d.get("isbn", []) or []
            key = str(d.get("key", "") or "")
            url = "https://openlibrary.org" + key if key.startswith("/") else ""
            cover = ""
            isbn13 = choose_isbn13(isbns)
            if isbn13:
                cover = f"https://covers.openlibrary.org/b/isbn/{isbn13}-M.jpg?default=false"
            elif d.get("cover_i"):
                cover = f"https://covers.openlibrary.org/b/id/{d.get('cover_i')}-M.jpg?default=false"
            out.append({
                "source": "OpenLibrary",
                "id": key,
                "title": normalize_text(d.get("title", "")),
                "authors": d.get("author_name", []) or [],
                "year": first_year(d.get("first_publish_year", "")),
                "publisher": first_nonempty(*(d.get("publisher", []) or [])),
                "isbns": [str(x) for x in isbns],
                "isbn13": isbn13,
                "categories": [str(x) for x in (d.get("subject", []) or [])[:8]],
                "url": url,
                "cover_url": cover,
            })
        return out

    def query_google_books(self, title: str, author: str = "") -> list[dict[str, Any]]:
        self._throttle()
        fa = first_author(author)
        # Google Books supports field-specific operators such as intitle: and inauthor:.
        q = f'intitle:"{title}"'
        if fa:
            q += f' inauthor:"{fa}"'
        params: dict[str, Any] = {
            "q": q,
            "maxResults": self.max_results,
            "printType": "books",
            "projection": "lite",
            "orderBy": "relevance",
        }
        if self.google_books_api_key:
            params["key"] = self.google_books_api_key
        gb = http_json("https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode(params), timeout=self.timeout)
        out: list[dict[str, Any]] = []
        if not gb or not gb.get("items"):
            return out
        self.stats["googlebooks"] += 1
        for item in gb.get("items", [])[: self.max_results]:
            info = item.get("volumeInfo", {}) if isinstance(item, dict) else {}
            identifiers = info.get("industryIdentifiers", []) or []
            isbns = [str(x.get("identifier", "")) for x in identifiers if isinstance(x, dict) and x.get("identifier")]
            out.append({
                "source": "GoogleBooks",
                "id": str(item.get("id", "") or ""),
                "title": normalize_text(info.get("title", "")),
                "authors": info.get("authors", []) or [],
                "year": first_year(info.get("publishedDate", "")),
                "publisher": normalize_text(info.get("publisher", "")),
                "isbns": isbns,
                "isbn13": choose_isbn13(isbns),
                "categories": [str(x) for x in (info.get("categories", []) or [])[:8]],
                "url": normalize_text(info.get("canonicalVolumeLink") or info.get("infoLink") or ""),
                "cover_url": normalize_text((info.get("imageLinks", {}) or {}).get("thumbnail", "")),
            })
        return out


def make_online_enricher(args: argparse.Namespace, out_dir: Path) -> Optional[OnlineBookEnricher]:
    if not getattr(args, "online_enrich", False):
        return None
    cache_arg = getattr(args, "online_cache", "") or ""
    cache_path = Path(cache_arg) if cache_arg else out_dir / "book-enrichment-cache.json"
    return OnlineBookEnricher(
        cache_path=cache_path,
        providers=getattr(args, "online_provider", "both"),
        max_results=getattr(args, "online_max_results", 5),
        delay=getattr(args, "online_delay", 0.35),
        timeout=getattr(args, "online_timeout", 15),
        min_match_score=getattr(args, "online_min_match_score", 0.56),
        google_books_api_key=getattr(args, "google_books_api_key", "") or os.environ.get("GOOGLE_BOOKS_API_KEY", ""),
    )


# ------------------------- commands -------------------------


def command_scan(args: argparse.Namespace) -> int:
    rules = load_rules(Path(args.rules) if args.rules else None)
    seeds = load_seed_catalog(Path(args.seed_catalog) if args.seed_catalog else None)
    client = DocspellClient(args.url, timeout=args.timeout)
    client.login(args.account)

    out_dir = Path(args.out)
    search_dir = out_dir / "book-searches"
    search_dir.mkdir(parents=True, exist_ok=True)

    queries = list(DEFAULT_QUERIES)
    if args.include_seed_queries:
        queries.extend(seed_queries(seeds, args.max_seed_queries))
    if args.include_all_query:
        queries.insert(0, "")

    candidates: dict[str, CandidateItem] = {}
    for q in queries:
        label = q or "EMPTY_ALL_QUERY"
        print(f"Searching: {label}", file=sys.stderr)
        try:
            raw_items = client.search_items(q, limit=args.limit, with_details=True)
        except Exception as exc:
            print(f"WARN: query failed {label}: {exc}", file=sys.stderr)
            continue
        safe_name = slugify(label, 80)
        with (search_dir / f"{safe_name}.json").open("w", encoding="utf-8") as f:
            json.dump({"query": q, "count": len(raw_items), "items": raw_items}, f, ensure_ascii=False, indent=2)
        merge_candidates(candidates, [item_from_docspell(raw, label) for raw in raw_items])

    enricher = make_online_enricher(args, out_dir)
    classes = [classify_item(c, seeds, rules, enricher) for c in candidates.values()]
    if enricher:
        enricher.save_cache()
        print(f"Online enrichment stats: {enricher.stats}", file=sys.stderr)
    write_classifications(out_dir, classes)
    print(f"Wrote {len(classes)} classified candidate rows to {out_dir}")
    return 0


def command_classify_csv(args: argparse.Namespace) -> int:
    rules = load_rules(Path(args.rules) if args.rules else None)
    seeds = load_seed_catalog(Path(args.seed_catalog) if args.seed_catalog else None)
    candidates = read_candidates_from_csv(Path(args.input))
    out_dir = Path(args.out)
    enricher = make_online_enricher(args, out_dir)
    classes = [classify_item(c, seeds, rules, enricher) for c in candidates]
    if enricher:
        enricher.save_cache()
        print(f"Online enrichment stats: {enricher.stats}", file=sys.stderr)
    write_classifications(out_dir, classes)
    print(f"Wrote {len(classes)} classified rows to {args.out}")
    return 0


def read_actions(path: Path, decisions: set[str], min_confidence: float) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            decision = row.get("review_decision", "")
            try:
                conf = float(row.get("confidence", "0") or 0)
            except ValueError:
                conf = 0.0
            if decision in decisions and conf >= min_confidence:
                rows.append(row)
    return rows


def command_apply(args: argparse.Namespace) -> int:
    decisions = {"safe_book"}
    if args.include_probable:
        decisions.add("probable_book")
    actions = read_actions(Path(args.actions), decisions, args.min_confidence)
    if not actions:
        print("No eligible actions found.")
        return 0
    print(f"Eligible actions: {len(actions)}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    print("Sample:")
    for row in actions[:20]:
        print(f"  - {row.get('confidence')} {row.get('original_title')} -> {row.get('suggested_folder')} [{row.get('suggested_tags')}]")

    if not args.apply:
        print("\nDry-run only. To modify Docspell, add: --apply --confirm APPLY-BOOKS")
        return 0
    if args.confirm != "APPLY-BOOKS":
        print("Refusing to apply without --confirm APPLY-BOOKS", file=sys.stderr)
        return 2

    client = DocspellClient(args.url, timeout=args.timeout)
    client.login(args.account)

    target_folder_name = args.folder
    folders = client.list_folders()
    folder_id = ""
    for f in folders:
        if str(f.get("name", "")).lower() == target_folder_name.lower():
            folder_id = f.get("id", "")
            break
    if not folder_id:
        if args.create_folder:
            folder_id = client.create_folder(target_folder_name)
            print(f"Created folder {target_folder_name}: {folder_id}")
        else:
            raise RuntimeError(f"Folder not found: {target_folder_name}. Create it or pass --create-folder.")

    existing_tags = {str(t.get("name", "")): t for t in client.list_tags()}
    needed_tags: set[str] = set()
    for row in actions:
        needed_tags.update(split_semicolon(row.get("suggested_tags", "")))
    missing = sorted(t for t in needed_tags if t and t not in existing_tags)
    if missing:
        if args.create_missing_tags:
            for tag in missing:
                client.create_tag(tag, category=args.tag_category)
                print(f"Created tag: {tag}")
            existing_tags = {str(t.get("name", "")): t for t in client.list_tags()}
        else:
            print("Missing tags; create them in Docspell or rerun with --create-missing-tags:")
            for t in missing:
                print("  - " + t)
            return 2

    log_dir = Path(args.out)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "book-apply-log.csv"
    with log_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["item_id", "title", "status", "error"])
        writer.writeheader()
        for row in actions:
            item_id = row.get("item_id", "")
            title = row.get("original_title", "")
            try:
                item = client.get_item(item_id)
                current_tags = []
                for t in item.get("tags", []) or []:
                    if isinstance(t, dict):
                        current_tags.append(first_nonempty(t.get("name"), t.get("id")))
                new_tags = sorted(set(current_tags) | set(split_semicolon(row.get("suggested_tags", ""))))
                client.set_item_folder(item_id, folder_id)
                client.link_item_tags(item_id, new_tags)
                writer.writerow({"item_id": item_id, "title": title, "status": "ok", "error": ""})
                print(f"OK {title}")
            except Exception as exc:
                writer.writerow({"item_id": item_id, "title": title, "status": "error", "error": str(exc)[:500]})
                print(f"ERROR {title}: {exc}", file=sys.stderr)
    print(f"Wrote apply log: {log_path}")
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Find and classify books in Docspell")
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", default=DEFAULT_URL)
    common.add_argument("--timeout", type=int, default=30)
    common.add_argument("--rules", default="")
    common.add_argument("--seed-catalog", default="")
    common.add_argument("--online-enrich", action="store_true", help="Query Open Library / Google Books with title/author metadata only")
    common.add_argument("--online-provider", choices=["both", "openlibrary", "googlebooks"], default="both", help="Which online metadata provider(s) to use")
    common.add_argument("--online-cache", default="", help="Path to JSON cache. Default: <out>/book-enrichment-cache.json")
    common.add_argument("--online-max-results", type=int, default=5, help="Max provider results to score per title")
    common.add_argument("--online-delay", type=float, default=0.35, help="Delay between external API calls")
    common.add_argument("--online-timeout", type=int, default=15, help="External API HTTP timeout")
    common.add_argument("--online-min-match-score", type=float, default=0.56, help="Minimum online title/author score to accept metadata")
    common.add_argument("--google-books-api-key", default=os.environ.get("GOOGLE_BOOKS_API_KEY", ""), help="Optional Google Books API key; can also use GOOGLE_BOOKS_API_KEY env var")

    scan = sub.add_parser("scan", parents=[common], help="Read-only scan Docspell and classify book candidates")
    scan.add_argument("--account", required=True, help="Docspell account, usually collective/user")
    scan.add_argument("--out", default="out/books")
    scan.add_argument("--limit", type=int, default=200)
    scan.add_argument("--include-seed-queries", action="store_true", help="Also search Docspell for seed catalog title snippets")
    scan.add_argument("--max-seed-queries", type=int, default=80)
    scan.add_argument("--include-all-query", action="store_true", help="Try an empty query to fetch all items; use if supported by your Docspell")
    scan.set_defaults(func=command_scan)

    ccsv = sub.add_parser("classify-csv", parents=[common], help="Classify an existing Docspell triage CSV")
    ccsv.add_argument("--input", required=True)
    ccsv.add_argument("--out", default="out/books")
    ccsv.set_defaults(func=command_classify_csv)

    apply = sub.add_parser("apply", help="Dry-run/apply reviewed book actions")
    apply.add_argument("--url", default=DEFAULT_URL)
    apply.add_argument("--timeout", type=int, default=30)
    apply.add_argument("--account", required=True)
    apply.add_argument("--actions", default="out/books/book-actions-safe_book.csv")
    apply.add_argument("--out", default="out/books")
    apply.add_argument("--folder", default="Library")
    apply.add_argument("--min-confidence", type=float, default=0.82)
    apply.add_argument("--include-probable", action="store_true")
    apply.add_argument("--create-folder", action="store_true")
    apply.add_argument("--create-missing-tags", action="store_true")
    apply.add_argument("--tag-category", default="library")
    apply.add_argument("--apply", action="store_true")
    apply.add_argument("--confirm", default="")
    apply.set_defaults(func=command_apply)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
