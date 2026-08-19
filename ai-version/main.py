#!/usr/bin/env python3
"""
FlyRank Internship challenge (AI version) -- polite books.toscrape.com scraper.

Scope
-----
  * Fetches the first 3 catalogue pages (page-1.html .. page-3.html under /catalogue/).
  * Discovers the 60 unique book detail URLs, fetches each, and extracts 8 raw fields.
  * Validates every record against a Pydantic schema; invalid records go to
    output/errors.json with a reason, never into output/books.json.

Politeness / robustness
-----------------------
  * Honest identifying User-Agent.
  * 10s request timeout.
  * >= 0.5s delay between real network requests.
  * Every fetched HTML page is cached to disk (cache/) and reused on re-runs,
    so the live site is only hit once.
  * Only HTTP 200 responses are parsed; 404/403 are not retried, timeouts/5xx
    are retried once.
  * Each page is fetched inside its own try/except -- one broken page is logged
    and skipped without taking the rest down.

Requirements (Python 3.10+)
---------------------------
    pip install requests beautifulsoup4 lxml pydantic

Run
---
    python ai-version/main.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, ValidationError, field_validator

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

TARGET = "https://books.toscrape.com/"
CATALOGUE_PAGES = [
    "https://books.toscrape.com/catalogue/page-1.html",
    "https://books.toscrape.com/catalogue/page-2.html",
    "https://books.toscrape.com/catalogue/page-3.html",
]

USER_AGENT = "FlyRankInternship-A9/1.0 (+https://github.com/shanujans/be05-polite-scraper)"
TIMEOUT_SECONDS = 10
POLITE_DELAY_SECONDS = 0.5
MAX_ATTEMPTS = 2  # initial attempt + 1 retry (for timeouts / 5xx only)

WORKDIR = Path(__file__).resolve().parent
CACHE_DIR = WORKDIR / "cache"
OUTPUT_DIR = WORKDIR / "output"


# --------------------------------------------------------------------------- #
# Pydantic schema
# --------------------------------------------------------------------------- #

class BookRecord(BaseModel):
    """Validated record for a single book."""

    title: str = Field(min_length=1)
    product_url: str = Field(min_length=1)
    price_text: str = Field(min_length=1)
    availability_text: str = Field(min_length=1)
    rating_text: str = Field(min_length=1)
    description: str | None = None  # nullable: some pages have no description
    source_page: str = Field(min_length=1)
    fetched_at: str = Field(min_length=1)  # ISO-8601 UTC
    price_gbp: float = Field(ge=0)

    @field_validator("fetched_at")
    @classmethod
    def _fetched_at_must_be_iso(cls, value: str) -> str:
        datetime.fromisoformat(value)
        return value


# --------------------------------------------------------------------------- #
# Disk cache
# --------------------------------------------------------------------------- #

def _cache_path_for(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def load_cache(url: str) -> tuple[str, str] | None:
    """Return (html, fetched_at) if a fresh cache entry exists, else None."""
    path = _cache_path_for(url)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("url") == url and isinstance(data.get("html"), str):
            return data["html"], data["fetched_at"]
    except (OSError, json.JSONDecodeError):
        pass
    return None


def store_cache(url: str, html: str, fetched_at: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"url": url, "fetched_at": fetched_at, "html": html}
    _cache_path_for(url).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# Fetching (with politeness + retry rules)
# --------------------------------------------------------------------------- #

class FetchError(Exception):
    """Raised when a page cannot be fetched (after any permitted retries)."""

    def __init__(self, url: str, message: str):
        self.url = url
        super().__init__(f"{url}: {message}")


def fetch(session: requests.Session, url: str, *, attempt: int = 0) -> tuple[str, str, bool]:
    """
    Return (html, fetched_at, from_cache) for a page.

    * Cache is consulted first -- cached pages are never re-fetched.
    * A politeness delay is applied before every real network request.
    * Only HTTP 200 is accepted/parsed.
    * 404/403: fail immediately (no retry).  Timeouts / network errors / 5xx:
      retried once.
    """
    cached = load_cache(url)
    if cached is not None:
        return cached[0], cached[1], True

    time.sleep(POLITE_DELAY_SECONDS)  # space out real requests

    try:
        resp = session.get(url, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        if attempt + 1 < MAX_ATTEMPTS:
            return fetch(session, url, attempt=attempt + 1)
        raise FetchError(url, f"request failed after {MAX_ATTEMPTS} attempts: {exc}") from exc

    status = resp.status_code
    if status == 200:
        # Site sends no charset header; content is UTF-8 (meta charset=utf-8).
        # Force UTF-8 so symbols like "£" aren't mangled by the latin-1 default.
        resp.encoding = "utf-8"
        html = resp.text
        fetched_at = datetime.now(timezone.utc).isoformat()
        store_cache(url, html, fetched_at)
        return html, fetched_at, False

    if status in (404, 403):
        raise FetchError(url, f"HTTP {status} (not retried)")

    if status >= 500 and attempt + 1 < MAX_ATTEMPTS:
        return fetch(session, url, attempt=attempt + 1)

    raise FetchError(url, f"HTTP {status}")


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #

def normalize_url(url: str) -> str:
    """Canonical identity: drop fragment + trailing slash."""
    no_frag = url.split("#", 1)[0]
    return no_frag.rstrip("/") or no_frag


def parse_catalogue(html: str) -> list[str]:
    """Return the relative book-detail hrefs listed on one catalogue page."""
    soup = BeautifulSoup(html, "lxml")
    hrefs = []
    for anchor in soup.select("article.product_pod h3 a[href]"):
        href = anchor.get("href")
        if href:
            hrefs.append(href)
    return hrefs


def _star_rating_class(rating_el) -> str | None:
    classes = rating_el.get("class") or []
    for cls in classes:
        if cls != "star-rating" and cls.strip():
            return cls.strip()
    return None


def parse_price_gbp(price_text: str | None) -> float | None:
    """'£51.77' -> 51.77 ; None/malformed -> None (caught by validation)."""
    if not price_text:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", price_text)
    if not cleaned or cleaned == ".":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_book(html: str, url: str, source_page: str, fetched_at: str) -> dict:
    """Extract the 8 raw fields (+ derived price_gbp) from a book page."""
    soup = BeautifulSoup(html, "lxml")
    main = soup.select_one("div.product_main")

    title_el = main.select_one("h1") if main else None
    price_el = main.select_one("p.price_color") if main else None
    avail_el = main.select_one("p.instock.availability") if main else None
    rating_el = main.select_one("p.star-rating") if main else None
    desc_el = soup.select_one("#product_description ~ p")

    def text_of(el) -> str | None:
        return el.get_text(strip=True) if el else None

    price_text = text_of(price_el)

    return {
        "title": text_of(title_el),
        "product_url": url,
        "price_text": price_text,
        "availability_text": text_of(avail_el),
        "rating_text": _star_rating_class(rating_el) if rating_el else None,
        "description": text_of(desc_el),
        "source_page": source_page,
        "fetched_at": fetched_at,
        "price_gbp": parse_price_gbp(price_text),
    }


# --------------------------------------------------------------------------- #
# Main run
# --------------------------------------------------------------------------- #

def main() -> int:
    start = datetime.now(timezone.utc)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    pages_fetched = 0
    cache_hits = 0
    failed_pages: list[str] = []

    discovered: dict[str, str] = {}   # normalized url -> canonical url
    source_pages: dict[str, str] = {}  # normalized url -> catalogue page it was found on

    # -- 1. Fetch + parse the 3 catalogue pages (each isolated by try/except) --
    for page_url in CATALOGUE_PAGES:
        try:
            html, _, from_cache = fetch(session, page_url)
        except FetchError as exc:
            failed_pages.append(page_url)
            print(f"[skip] catalogue page failed: {exc}", file=sys.stderr)
            continue
        pages_fetched += 1
        if from_cache:
            cache_hits += 1
        try:
            for href in parse_catalogue(html):
                abs_url = urljoin(page_url, href)
                norm = normalize_url(abs_url)
                discovered[norm] = abs_url
                source_pages.setdefault(norm, page_url)
        except Exception as exc:  # noqa: BLE001 -- keep other pages alive
            failed_pages.append(page_url)
            print(f"[skip] catalogue page parse failed: {page_url} ({exc})", file=sys.stderr)

    # -- 2. Fetch + parse every unique book page (each isolated by try/except) --
    records: list[BookRecord] = []
    errors: list[dict] = []

    for norm in sorted(discovered):
        url = discovered[norm]
        try:
            html, fetched_at, from_cache = fetch(session, url)
        except FetchError as exc:
            failed_pages.append(url)
            print(f"[skip] book page failed: {exc}", file=sys.stderr)
            continue
        pages_fetched += 1
        if from_cache:
            cache_hits += 1

        try:
            raw = parse_book(html, url, source_pages[norm], fetched_at)
        except Exception as exc:  # noqa: BLE001
            failed_pages.append(url)
            print(f"[skip] book page parse failed: {url} ({exc})", file=sys.stderr)
            continue

        try:
            records.append(BookRecord(**raw))
        except ValidationError as exc:
            errors.append({"url": url, "reason": f"validation failed: {exc}"})
            print(f"[invalid] {url}: {exc}", file=sys.stderr)

    # -- 3. Stable output (sorted by product_url for idempotency) --
    records.sort(key=lambda r: r.product_url)
    books_json = [record.model_dump() for record in records]
    errors.sort(key=lambda e: e["url"])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "books.json").write_text(
        json.dumps(books_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUTPUT_DIR / "errors.json").write_text(
        json.dumps(errors, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # -- 4. Run report --
    finished = datetime.now(timezone.utc)
    duration = (finished - start).total_seconds()
    run_report = {
        "start_time": start.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round(duration, 3),
        "pages_fetched": pages_fetched,
        "cache_hits": cache_hits,
        "valid_records": len(records),
        "invalid_records": len(errors),
        "failed_pages": failed_pages,
        "target": TARGET,
        "user_agent": USER_AGENT,
    }
    (OUTPUT_DIR / "run-report.json").write_text(
        json.dumps(run_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"valid_records={len(records)} failed_pages={len(failed_pages)}")
    return 0 if not failed_pages else 1


if __name__ == "__main__":
    sys.exit(main())