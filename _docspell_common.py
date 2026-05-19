#!/usr/bin/env python3
"""Shared helpers for the Docspell write-heavy apply scripts.

Provides:

  * redact()                    — strip credentials from any string before print/log
  * dns_preflight()             — resolve the Docspell host with a friendly error
  * Session                     — authenticated HTTP client with auto-refresh + retry
  * Progress                    — TTY-aware progress reporter with ETA
  * Summary                     — structured end-of-run summary printer

Constraints (do NOT break):

  * urllib only (no requests / httpx)
  * Token captured ONCE via prompt_credentials; auto-refresh on 401
  * If DOCSPELL_TOKEN env var is set, that mode does NOT auto-refresh and
    fails with a clear message if it expires
  * 5xx → retry with backoff (0.5s, 2s, 5s); 4xx (other than 401) → no retry
  * All credential output passes through redact()
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
from dataclasses import dataclass, field
from typing import Any, Callable


__all__ = [
    "redact",
    "api_url",
    "dns_preflight",
    "version_warn",
    "Session",
    "Progress",
    "Summary",
    "extract_host",
]


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

# Empirically verified Docspell version that the codebase targets.
EXPECTED_VERSION_PREFIX = "0.4"

# 5xx retry backoff schedule (seconds).
RETRY_DELAYS: tuple[float, ...] = (0.5, 2.0, 5.0)

# Status codes we retry. Everything else is final.
RETRYABLE_STATUSES: frozenset[int] = frozenset({500, 502, 503, 504})


# ---------------------------------------------------------------------------
# Redaction (security-critical — never remove)
# ---------------------------------------------------------------------------


def redact(text: str) -> str:
    """Remove anything that smells like a credential before printing/logging."""
    text = re.sub(r'("token"\s*:\s*")[^"]+', r"\1<redacted>", text)
    text = re.sub(r'("password"\s*:\s*")[^"]+', r"\1<redacted>", text)
    text = re.sub(r"(X-Docspell-Auth:\s*)\S+", r"\1<redacted>", text, flags=re.I)
    text = re.sub(r"(Cookie:\s*)[^\r\n]+", r"\1<redacted>", text, flags=re.I)
    text = re.sub(r"(Authorization:\s*)\S+", r"\1<redacted>", text, flags=re.I)
    return text


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def api_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/api/v1"):
        return f"{base}{path}"
    return f"{base}/api/v1{path}"


def extract_host(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return parsed.hostname or ""


# ---------------------------------------------------------------------------
# DNS preflight
# ---------------------------------------------------------------------------


def dns_preflight(base_url: str) -> None:
    """Resolve the Docspell host. Exit(2) with a Tailscale-aware hint on failure."""
    host = extract_host(base_url)
    if not host:
        print(f"Could not parse hostname from URL: {base_url}", file=sys.stderr)
        raise SystemExit(2)
    try:
        socket.gethostbyname(host)
    except socket.gaierror as exc:
        print(
            f"DNS resolution failed for {host}. If you're on Tailscale, check "
            f"`tailscale status` and consider adding to /etc/hosts:\n"
            f"100.66.18.7 {host}\n"
            f"(underlying error: {exc})",
            file=sys.stderr,
        )
        raise SystemExit(2)


# ---------------------------------------------------------------------------
# Version sanity check (warning only)
# ---------------------------------------------------------------------------


def version_warn(version_str: str) -> None:
    """Print a warning if version doesn't start with the empirically-verified prefix."""
    if not version_str:
        return
    if not version_str.startswith(EXPECTED_VERSION_PREFIX):
        print(
            f"WARNING: Docspell version '{version_str}' does not start with "
            f"'{EXPECTED_VERSION_PREFIX}'. This codebase is verified against "
            f"Docspell 0.43.0; API quirks may differ.",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Authenticated session with auto token refresh + retry
# ---------------------------------------------------------------------------


@dataclass
class Session:
    """Authenticated Docspell HTTP client.

    * Captures (account, password) ONCE at start via `prompt_credentials()`.
    * Re-logs in transparently on HTTP 401 and retries the failing call.
    * Retries 5xx with backoff (0.5s, 2s, 5s).
    * Does NOT retry 4xx user-error responses (400, 404, etc).
    * If `DOCSPELL_TOKEN` env var was used, sets `static_token=True` and
      will NOT auto-refresh; on 401 it fails clearly.
    """

    base_url: str
    account: str | None
    password: str | None
    token: str = ""
    token_expires_at: float = 0.0
    static_token: bool = False
    default_timeout: int = 60
    # ~5 min TTL per CLAUDE.md; refresh proactively at 4 minutes.
    proactive_refresh_after: float = 240.0
    # Hook for tests / progress messages.
    log: Callable[[str], None] = field(default=lambda _msg: None)

    # -- construction -----------------------------------------------------

    @classmethod
    def from_args(
        cls,
        base_url: str,
        args: argparse.Namespace,
        *,
        log: Callable[[str], None] | None = None,
    ) -> "Session":
        """Construct a Session, honoring DOCSPELL_TOKEN if set.

        If DOCSPELL_TOKEN is set we use it as a static, non-refreshable token.
        Otherwise we capture (account, password) up front so we can re-login
        transparently when the server returns 401 mid-batch.
        """
        env_token = os.environ.get("DOCSPELL_TOKEN")
        if env_token:
            sess = cls(
                base_url=base_url.rstrip("/"),
                account=None,
                password=None,
                token=env_token,
                static_token=True,
            )
        else:
            account = getattr(args, "account", None) or os.environ.get("DOCSPELL_ACCOUNT")
            if not account:
                account = input("Docspell account: ").strip()
            password = os.environ.get("DOCSPELL_PASSWORD")
            if password is None:
                password = getpass.getpass("Docspell password: ")
            sess = cls(
                base_url=base_url.rstrip("/"),
                account=account,
                password=password,
            )
            sess._login()
        if log is not None:
            sess.log = log
        return sess

    # -- internal login ---------------------------------------------------

    def _login(self) -> None:
        if self.static_token:
            raise RuntimeError(
                "Auth token (from DOCSPELL_TOKEN) has expired; this script "
                "was started with a static token and cannot auto-refresh. "
                "Re-run without DOCSPELL_TOKEN or with a fresh token."
            )
        if not self.account or self.password is None:
            raise RuntimeError("Session has no stored credentials to (re-)login with.")
        response = self._raw_request(
            "POST",
            api_url(self.base_url, "/open/auth/login"),
            body={"account": self.account, "password": self.password},
            token=None,
            timeout=self.default_timeout,
        )
        if not response.get("success"):
            raise RuntimeError(
                f"Docspell login failed: {response.get('message', 'unknown')}"
            )
        token = response.get("token")
        if not token:
            raise RuntimeError("Docspell login response did not contain an auth token.")
        self.token = token
        self.token_expires_at = time.monotonic() + self.proactive_refresh_after

    # -- public API -------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | list[Any] | None = None,
        timeout: int | None = None,
    ) -> Any:
        """Authenticated JSON request with auto-refresh on 401 and 5xx backoff.

        `path` may be either an absolute URL or a Docspell API path beginning
        with '/'. Paths beginning with '/' are routed via `api_url()`.
        """
        url = path if path.startswith("http://") or path.startswith("https://") else api_url(self.base_url, path)
        eff_timeout = timeout or self.default_timeout

        # Proactive refresh if our token is about to expire.
        if (
            not self.static_token
            and self.token
            and time.monotonic() >= self.token_expires_at
        ):
            self.log("Token nearing expiry; refreshing.")
            try:
                self._login()
            except Exception as exc:
                # Proactive refresh failure is non-fatal — fall through and
                # let the next reactive 401 handler try again.
                self.log(f"Proactive token refresh failed: {redact(str(exc))}")

        last_exc: Exception | None = None
        for attempt in range(len(RETRY_DELAYS) + 1):  # initial + retries
            try:
                return self._raw_request(
                    method, url, body=body, token=self.token, timeout=eff_timeout
                )
            except DocspellHTTPError as exc:
                status = exc.status
                if status == 401:
                    if self.static_token:
                        raise RuntimeError(
                            "Got HTTP 401 from Docspell and DOCSPELL_TOKEN is "
                            "static — cannot auto-refresh. Re-run without "
                            "DOCSPELL_TOKEN to enable transparent re-login."
                        ) from exc
                    self.log("Got 401; re-logging in and retrying once.")
                    self._login()
                    # Retry exactly once after a fresh login; if it fails
                    # again, the next iteration's 401 will surface.
                    try:
                        return self._raw_request(
                            method, url, body=body, token=self.token, timeout=eff_timeout
                        )
                    except DocspellHTTPError as exc2:
                        last_exc = self._wrap(exc2, method, url)
                        if exc2.status in RETRYABLE_STATUSES and attempt < len(RETRY_DELAYS):
                            delay = RETRY_DELAYS[attempt]
                            self.log(
                                f"HTTP {exc2.status} on retry-after-401; "
                                f"backoff {delay}s then retry."
                            )
                            time.sleep(delay)
                            continue
                        raise last_exc from exc2
                if status in RETRYABLE_STATUSES and attempt < len(RETRY_DELAYS):
                    delay = RETRY_DELAYS[attempt]
                    self.log(
                        f"HTTP {status} from {method} {url}; "
                        f"backoff {delay}s then retry (attempt {attempt + 1}/{len(RETRY_DELAYS)})."
                    )
                    time.sleep(delay)
                    continue
                # Final 4xx (other than 401) or exhausted retries — surface.
                raise self._wrap(exc, method, url) from exc
            except urllib.error.URLError as exc:
                # Network / DNS hiccup mid-batch — treat like a 503 for retries.
                last_exc = RuntimeError(
                    f"URLError from {method} {url}: {exc.reason}"
                )
                if attempt < len(RETRY_DELAYS):
                    delay = RETRY_DELAYS[attempt]
                    self.log(
                        f"URLError on {method} {url}; backoff {delay}s "
                        f"then retry (attempt {attempt + 1}/{len(RETRY_DELAYS)})."
                    )
                    time.sleep(delay)
                    continue
                raise last_exc from exc
        # Should be unreachable
        if last_exc:
            raise last_exc
        raise RuntimeError(f"request() exhausted retries with no exception for {method} {url}")

    # Back-compat alias for older code that called request_json(...).
    def request_json(
        self,
        method: str,
        url_or_path: str,
        *,
        token: str | None = None,
        body: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> Any:
        # Accept positional `token=` for drop-in compatibility but ignore it —
        # the Session knows its own token.
        _ = token
        return self.request(method, url_or_path, body=body, timeout=timeout)

    # -- low-level raw HTTP (no retry logic) ------------------------------

    @staticmethod
    def _raw_request(
        method: str,
        url: str,
        *,
        body: Any = None,
        token: str | None = None,
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
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:
                detail = ""
            raise DocspellHTTPError(
                status=exc.code,
                method=method,
                url=url,
                body=detail,
            ) from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _wrap(exc: "DocspellHTTPError", method: str, url: str) -> RuntimeError:
        return RuntimeError(
            f"HTTP {exc.status} from {method} {url}: {redact(exc.body)[:400]}"
        )


@dataclass
class DocspellHTTPError(Exception):
    status: int
    method: str
    url: str
    body: str = ""

    def __str__(self) -> str:  # pragma: no cover - debug aid
        return f"HTTP {self.status} from {self.method} {self.url}: {redact(self.body)[:200]}"

    def short(self, max_body: int = 80) -> str:
        """Compact rendering suitable for CSV error columns."""
        body = redact(self.body or "").strip().replace("\n", " ")
        if len(body) > max_body:
            body = body[:max_body] + "…"
        return f"HTTP {self.status} {self.method} {self.url} :: {body}"


def err_to_log(exc: BaseException, max_body: int = 80) -> str:
    """Render an exception as a single, redacted, line for CSV logs."""
    if isinstance(exc, DocspellHTTPError):
        return exc.short(max_body=max_body)
    text = redact(str(exc))
    text = text.replace("\n", " ").strip()
    if len(text) > 400:
        text = text[:400] + "…"
    return text


# Convenience wrapper for older code paths that took (base_url, token, ...).
def version_check(base_url: str) -> dict[str, Any]:
    """Fetch /api/info/version. Tries both /api/info/version and /api/v1/api/info/version."""
    urls = [
        f"{base_url.rstrip('/')}/api/info/version",
        api_url(base_url, "/api/info/version"),
    ]
    last_error: Exception | None = None
    for url in urls:
        try:
            return Session._raw_request("GET", url, timeout=20)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Version check failed: {last_error}")


# ---------------------------------------------------------------------------
# Progress reporter
# ---------------------------------------------------------------------------


class Progress:
    """TTY-aware progress with ETA based on running-average rate.

    * On a TTY, rewrites a single line with `\\r`.
    * On a non-TTY (file/pipe), prints a line every `print_every` items.
    * Always prints a final summary line on `done()`.
    """

    def __init__(
        self,
        total: int,
        *,
        prefix: str = "",
        stream: Any = sys.stdout,
        print_every: int = 25,
    ) -> None:
        self.total = max(total, 0)
        self.prefix = prefix
        self.stream = stream
        self.print_every = max(print_every, 1)
        self.start = time.monotonic()
        self.last_tick = self.start
        self.processed = 0
        self.ok = 0
        self.failed = 0
        self.skipped = 0
        self.retried = 0
        self._is_tty = bool(getattr(stream, "isatty", lambda: False)())

    def tick(
        self,
        *,
        ok: bool | None = None,
        failed: bool = False,
        skipped: bool = False,
        retried: bool = False,
    ) -> None:
        self.processed += 1
        if failed:
            self.failed += 1
        elif skipped:
            self.skipped += 1
        elif ok or ok is None:
            self.ok += 1
        if retried:
            self.retried += 1
        self._render()

    def _render(self, force: bool = False) -> None:
        if not force and not self._is_tty:
            # On non-TTY, only print every N items or at the end.
            if (
                self.processed % self.print_every != 0
                and self.processed != self.total
            ):
                return
        elapsed = max(time.monotonic() - self.start, 0.001)
        rate = self.processed / elapsed
        pct = (self.processed / self.total * 100.0) if self.total else 100.0
        remaining = max(self.total - self.processed, 0)
        eta = (remaining / rate) if rate > 0 else 0.0
        msg = (
            f"{self.prefix}{self.processed}/{self.total} "
            f"({pct:5.1f}%)  ok={self.ok} fail={self.failed} skip={self.skipped} "
            f"rate={rate:.2f}/s  eta={_fmt_secs(eta)}  elapsed={_fmt_secs(elapsed)}"
        )
        if self._is_tty:
            # Pad with trailing spaces to clear stale chars, then \r.
            pad = max(0, 100 - len(msg))
            self.stream.write("\r" + msg + (" " * pad))
            self.stream.flush()
        else:
            self.stream.write(msg + "\n")
            self.stream.flush()

    def done(self) -> None:
        self._render(force=True)
        if self._is_tty:
            self.stream.write("\n")
            self.stream.flush()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.start

    @property
    def rate(self) -> float:
        e = self.elapsed
        return self.processed / e if e > 0 else 0.0


def _fmt_secs(s: float) -> str:
    s = max(int(s), 0)
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m{sec:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


# ---------------------------------------------------------------------------
# End-of-run summary
# ---------------------------------------------------------------------------


@dataclass
class Summary:
    total: int = 0
    ok: int = 0
    failed: int = 0
    retried: int = 0
    skipped: int = 0
    elapsed: float = 0.0
    log_path: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def print(self, stream: Any = sys.stdout) -> None:
        rate = self.ok / self.elapsed if self.elapsed > 0 else 0.0
        rows = [
            ("processed",       self.total),
            ("ok",              self.ok),
            ("failed",          self.failed),
            ("retried",         self.retried),
            ("skipped",         self.skipped),
            ("elapsed",         _fmt_secs(self.elapsed)),
            ("rate (ok/sec)",   f"{rate:.2f}"),
        ]
        for k, v in self.extra.items():
            rows.append((k, v))
        width = max(len(r[0]) for r in rows)
        stream.write("\n")
        stream.write("Summary\n")
        stream.write("-------\n")
        for k, v in rows:
            stream.write(f"  {k:<{width}}  {v}\n")
        if self.log_path:
            stream.write(f"\nLog: {self.log_path}\n")
        stream.flush()


# ---------------------------------------------------------------------------
# Apply-log resume helper
# ---------------------------------------------------------------------------


def load_processed_ids(
    log_path: str,
    *,
    item_id_field: str = "item_id",
    status_fields: tuple[str, ...] = ("folder_status",),
    success_values: frozenset[str] = frozenset({"set", "already-set", "ok", "skipped-existing"}),
) -> set[str]:
    """Return the set of item_ids already successfully processed in a prior run.

    Reads an existing apply-log CSV (if present) and returns ids whose
    `status_fields` all contain a value in `success_values` AND whose `error`
    column is empty. Missing file → empty set. Malformed → empty set.
    """
    import csv as _csv
    from pathlib import Path as _Path

    p = _Path(log_path)
    if not p.exists():
        return set()
    seen: set[str] = set()
    try:
        with p.open("r", encoding="utf-8", newline="") as fh:
            reader = _csv.DictReader(fh)
            if not reader.fieldnames:
                return set()
            for row in reader:
                if (row.get("error") or "").strip():
                    continue
                iid = (row.get(item_id_field) or "").strip()
                if not iid:
                    continue
                ok = True
                for field_name in status_fields:
                    val = (row.get(field_name) or "").strip()
                    if val and val not in success_values:
                        ok = False
                        break
                if ok:
                    seen.add(iid)
    except Exception:
        return set()
    return seen
