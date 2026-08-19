"""FlyRank rematch scraper for https://books.toscrape.com/.

Discovers the 60 book detail pages by following the catalogue's own
"next" links from page-1.html (exactly 3 catalogue pages), extracts 8 raw
fields per book, cleans them into a Pydantic schema, caches HTML to disk,
and writes books.json / errors.json / run-report.json.

Usage:
    python ai-version/rematch/main.py
    python ai-version/rematch/main.py --include-bad-url
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, ValidationError

USER_AGENT = "FlyRankInternship-A9/1.0 (+https://github.com/shanujans/be05-polite-scraper)"
TARGET = "https://books.toscrape.com/"
START_URL = "https://books.toscrape.com/catalogue/page-1.html"
MAX_CATALOGUE_PAGES = 3
TIMEOUT_SECONDS = 10
MIN_DELAY_SECONDS = 0.5
BAD_URL = "https://books.toscrape.com/catalogue/this-book-does-not-exist-rematch_9999/index.html"

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
BOOKS_JSON = BASE_DIR / "books.json"
ERRORS_JSON = BASE_DIR / "errors.json"
REPORT_JSON = BASE_DIR / "run-report.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("flyrank-rematch")

_last_request_ts: float = 0.0


class BookRecord(BaseModel):
    """Validated schema for one scraped book record."""

    title: str = Field(min_length=1)
    product_url: str = Field(min_length=1)
    price_text: str = Field(min_length=1)
    price_gbp: float
    availability_text: str = Field(min_length=1)
    rating_text: str = Field(min_length=1)
    description: str | None = None
    source_page: str = Field(min_length=1)
    fetched_at: str = Field(min_length=1)


def cache_path_for(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.html"


def _throttle() -> None:
    """Enforce at least MIN_DELAY_SECONDS between real network requests."""
    global _last_request_ts
    elapsed = time.monotonic() - _last_request_ts
    if elapsed < MIN_DELAY_SECONDS:
        time.sleep(MIN_DELAY_SECONDS - elapsed)
    _last_request_ts = time.monotonic()


def fetch_html(url: str, retry_left: int = 1) -> str | None:
    """Fetch url over the network (with throttle + retry), caching 200s.

    Never retries 404/403. Retries once on timeout/connection errors or 5xx.
    Returns the decoded HTML text, or None on failure.
    """
    _throttle()
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        log.warning("request error for %s: %s", url, exc)
        if retry_left > 0:
            return fetch_html(url, retry_left - 1)
        return None

    if resp.status_code in (404, 403):
        log.warning("not retrying %s (HTTP %d)", url, resp.status_code)
        return None

    if resp.status_code >= 500:
        log.warning("server error HTTP %d for %s", resp.status_code, url)
        if retry_left > 0:
            return fetch_html(url, retry_left - 1)
        return None

    if resp.status_code != 200:
        log.warning("skipping %s (HTTP %d)", url, resp.status_code)
        return None

    # The site sends no charset header; requests would default to latin-1 and
    # mangle the UTF-8 "£". Decode explicitly as UTF-8.
    resp.encoding = "utf-8"
    html = resp.text

    cache_path = cache_path_for(url)
    cache_path.write_text(html, encoding="utf-8")
    log.debug("cached %s", url)
    return html


def get_html(url: str) -> tuple[str, bool]:
    """Return (html, from_cache). Reuses disk cache when present."""
    cache_path = cache_path_for(url)
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8"), True
    return fetch_html(url), False


def discover_catalogue_pages() -> list[tuple[str, str, bool]]:
    """Follow the catalogue's own 'next' links for exactly 3 pages.

    Returns [(page_url, html, from_cache), ...] in crawl order.
    """
    pages: list[tuple[str, str, bool]] = []
    url = START_URL
    for _ in range(MAX_CATALOGUE_PAGES):
        html, from_cache = get_html(url)
        if html is None:
            raise RuntimeError(f"could not fetch catalogue page {url}")
        pages.append((url, html, from_cache))
        soup = BeautifulSoup(html, "html.parser")
        next_link = soup.select_one("li.next a")
        if next_link is None or next_link.get("href") is None:
            break
        url = urljoin(url, next_link["href"])
    return pages


def extract_book_urls(page_html: str, page_url: str) -> list[str]:
    """Extract book detail URLs from one catalogue page (absolute, via urljoin)."""
    soup = BeautifulSoup(page_html, "html.parser")
    urls = []
    for anchor in soup.select("article.product_pod h3 a"):
        href = anchor.get("href")
        if href:
            urls.append(urljoin(page_url, href))
    return urls


def extract_product(page_html: str, product_url: str, source_page: str, fetched_at: str) -> dict:
    soup = BeautifulSoup(page_html, "html.parser")

    title_el = soup.select_one("div.product_main h1")
    title = title_el.get_text(strip=True) if title_el else ""

    price_el = soup.select_one("p.price_color")
    price_text = price_el.get_text(strip=True) if price_el else ""

    avail_el = soup.select_one("p.instock.availability")
    availability_text = avail_el.get_text(" ", strip=True) if avail_el else ""

    rating_el = soup.select_one("p.star-rating")
    rating_text = ""
    if rating_el:
        for cls in rating_el.get("class", []):
            if cls != "star-rating":
                rating_text = cls
                break

    description = None
    desc_div = soup.select_one("#product_description")
    if desc_div is not None:
        para = desc_div.find_next_sibling("p")
        if para is not None:
            description = para.get_text(strip=True) or None

    return {
        "title": title,
        "product_url": product_url,
        "price_text": price_text,
        "price_gbp": parse_price_gbp(price_text),
        "availability_text": availability_text,
        "rating_text": rating_text,
        "description": description,
        "source_page": source_page,
        "fetched_at": fetched_at,
    }


def parse_price_gbp(price_text: str) -> float:
    match = re.search(r"\d+\.\d{2}", price_text)
    if match is None:
        raise ValueError(f"cannot parse price from {price_text!r}")
    return float(match.group(0))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="FlyRank rematch scraper for books.toscrape.com")
    parser.add_argument(
        "--include-bad-url",
        action="store_true",
        help="append one deliberately broken book URL to prove the run survives a 404",
    )
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    start_time = datetime.now(timezone.utc)
    cache_hits = 0
    pages_fetched = 0
    failed_pages = 0
    books: list[BookRecord] = []
    errors: list[dict] = []

    try:
        catalogue_pages = discover_catalogue_pages()
    except RuntimeError as exc:
        log.error("catalogue discovery failed: %s", exc)
        return 1

    for _, html, from_cache in catalogue_pages:
        if from_cache:
            cache_hits += 1
        else:
            pages_fetched += 1

    # 1. Discover unique book detail URLs from the 3 catalogue pages.
    book_urls: list[str] = []
    seen: set[str] = set()
    source_by_url: dict[str, str] = {}
    for page_url, page_html, _ in catalogue_pages:
        for url in extract_book_urls(page_html, page_url):
            if url not in seen:
                seen.add(url)
                book_urls.append(url)
                source_by_url[url] = page_url

    if args.include_bad_url:
        if BAD_URL not in seen:
            book_urls.append(BAD_URL)
            source_by_url[BAD_URL] = START_URL
        log.info("--include-bad-url: appended broken URL %s", BAD_URL)

    # 2. Fetch + parse + validate each book page.
    for product_url in book_urls:
        try:
            html, from_cache = get_html(product_url)
            if from_cache:
                cache_hits += 1
            else:
                pages_fetched += 1
            if html is None:
                failed_pages += 1
                log.warning("failed page, skipping: %s", product_url)
                continue

            fetched_at = utc_now_iso()
            raw = extract_product(html, product_url, source_by_url[product_url], fetched_at)
            record = BookRecord(**raw)
        except (ValidationError, ValueError, KeyError, TypeError, AttributeError) as exc:
            errors.append({"product_url": product_url, "reason": str(exc)})
            log.warning("invalid record for %s: %s", product_url, exc)
            continue
        except requests.RequestException as exc:
            failed_pages += 1
            log.warning("failed page, skipping: %s (%s)", product_url, exc)
            continue

        books.append(record)

    # 3. Write outputs (overwrite -> idempotent; product_url is identity).
    by_url: dict[str, BookRecord] = {}
    for record in books:
        by_url[record.product_url] = record
    books_json = [record.model_dump() for record in by_url.values()]
    BOOKS_JSON.write_text(
        json.dumps(books_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    ERRORS_JSON.write_text(
        json.dumps(errors, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    finished_at = datetime.now(timezone.utc)
    duration = (finished_at - start_time).total_seconds()

    report = {
        "start_time": start_time.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round(duration, 3),
        "pages_fetched": pages_fetched,
        "cache_hits": cache_hits,
        "valid_records": len(books_json),
        "invalid_records": len(errors),
        "failed_pages": failed_pages,
        "target": TARGET,
        "user_agent": USER_AGENT,
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"valid_records={len(books_json)} failed_pages={failed_pages}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())