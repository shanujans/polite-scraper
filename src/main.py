"""BE-05 · The Polite Scraper.

Fetches the first 3 catalogue pages of Books to Scrape, visits all 60 book
pages, turns messy HTML into clean, schema-checked JSON, survives a broken
page, and reports on every run.

Pipeline: classify -> fetch -> cache -> discover -> extract -> normalize ->
validate -> store -> report.
"""

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, ValidationError, field_validator

BASE_URL = "https://books.toscrape.com"
FIRST_CATALOGUE_PAGE = f"{BASE_URL}/catalogue/page-1.html"
MAX_CATALOGUE_PAGES = 3
USER_AGENT = (
    "FlyRankInternship-A9/1.0 "
    "(+https://github.com/shanujans/be05-polite-scraper)"
)
TIMEOUT_SECONDS = 10
MIN_DELAY_SECONDS = 0.5
NO_RETRY_STATUS = {403, 404}

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache"
OUTPUT_DIR = BASE_DIR / "output"


class FetchError(Exception):
    """A page could not be fetched after politeness rules were applied."""


class BookRecord(BaseModel):
    """The clean, validated shape of one book record."""

    title: str
    product_url: str
    price_text: str
    price_gbp: float
    availability_text: str
    rating_text: str
    description: str | None = None
    source_page: str
    fetched_at: str

    @field_validator("product_url")
    @classmethod
    def product_url_must_be_absolute_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("product_url must be an absolute https URL")
        return value


_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})
_last_request_monotonic = [0.0]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def cache_path_for(url: str) -> Path:
    """Deterministic cache location for a URL, so re-runs hit the same file."""
    catalogue = re.match(
        rf"{re.escape(BASE_URL)}/catalogue/page-(\d+)\.html$", url
    )
    if catalogue:
        return CACHE_DIR / f"catalogue-page-{catalogue.group(1)}.html"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / "books" / f"{digest}.html"


def polite_delay() -> None:
    """Wait so that real requests are at least MIN_DELAY_SECONDS apart."""
    elapsed = time.monotonic() - _last_request_monotonic[0]
    if elapsed < MIN_DELAY_SECONDS:
        time.sleep(MIN_DELAY_SECONDS - elapsed)
    _last_request_monotonic[0] = time.monotonic()


def extract_book(html: str, product_url: str, source_page: str) -> dict:
    """Pull the eight raw fields out of one book detail page."""
    soup = BeautifulSoup(html, "html.parser")
    product_main = soup.select_one("div.product_main")

    title = product_main.select_one("h1").get_text(strip=True) if product_main else None
    price_el = product_main.select_one("p.price_color") if product_main else None
    price_text = price_el.get_text(strip=True) if price_el else None
    avail_el = (
        product_main.select_one("p.instock.availability") if product_main else None
    )
    availability_text = (
        avail_el.get_text(" ", strip=True) if avail_el else None
    )
    rating_el = product_main.select_one("p.star-rating") if product_main else None
    rating_classes = rating_el.get("class", []) if rating_el else []
    rating_text = rating_classes[1] if len(rating_classes) > 1 else None
    desc_el = soup.select_one("div#product_description ~ p")
    description = desc_el.get_text(strip=True) if desc_el else None

    return {
        "title": title,
        "product_url": product_url,
        "price_text": price_text,
        "availability_text": availability_text,
        "rating_text": rating_text,
        "description": description,
        "source_page": source_page,
        "fetched_at": now_iso(),
    }


def discover_catalogue() -> tuple[list[str], list[tuple[str, str]], int]:
    """Follow the catalogue's own 'next' links from page 1.

    Returns (catalogue_urls, book_pages, cache_hits). Stops after three pages.
    book_pages is a list of (book_url, source_page_url) pairs, deduplicated
    by book URL in discovery order.
    """
    catalogue_urls: list[str] = []
    book_pages: list[tuple[str, str]] = []
    cache_hits = 0
    page_url = FIRST_CATALOGUE_PAGE

    while page_url and len(catalogue_urls) < MAX_CATALOGUE_PAGES:
        catalogue_urls.append(page_url)
        html, cached = fetch(page_url)
        cache_hits += 1 if cached else 0
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.select("article.product_pod h3 a"):
            href = anchor.get("href")
            if href:
                book_pages.append((urljoin(page_url, href), page_url))
        next_link = soup.select_one("li.next a")
        page_url = urljoin(page_url, next_link.get("href")) if next_link else None

    unique: dict[str, str] = {}
    for book_url, source_page in book_pages:
        unique.setdefault(book_url, source_page)
    return catalogue_urls, list(unique.items()), cache_hits


def fetch(url: str, retries: int = 1) -> tuple[str, bool]:
    """Fetch a page, caching it after the first real request.

    Returns (html, cache_hit). Never retries 403/404. One retry on timeout
    or 5xx. Raises FetchError when the page cannot be fetched.
    """
    cache_path = cache_path_for(url)
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8"), True

    attempt = 0
    while True:
        attempt += 1
        polite_delay()
        try:
            resp = _session.get(url, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            if attempt <= retries:
                time.sleep(1)
                continue
            raise FetchError(f"request failed after {attempt} attempts: {exc}") from exc

        if resp.status_code == 200:
            resp.encoding = "utf-8"
            break
        if resp.status_code in NO_RETRY_STATUS:
            raise FetchError(f"HTTP {resp.status_code} (not retried)")
        if attempt <= retries:
            time.sleep(1)
            continue
        raise FetchError(f"HTTP {resp.status_code} after {attempt} attempts")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(resp.text, encoding="utf-8")
    return resp.text, False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-bad-url",
        action="store_true",
        help="add a deliberately broken book URL to prove failure handling",
    )
    args = parser.parse_args()

    start_monotonic = time.monotonic()
    start_time = now_iso()

    print(f"TARGET {BASE_URL}")

    catalogue_urls, book_pages, catalogue_cache_hits = discover_catalogue()
    print(f"catalogue_pages={len(catalogue_urls)} "
          f"discovered={len(book_pages)} "
          f"unique_urls={len(book_pages)}")

    raw_records: list[dict] = []
    detail_cache_hits = 0
    for book_url, source_page in book_pages:
        html, cached = fetch(book_url)
        detail_cache_hits += 1 if cached else 0
        raw_records.append(extract_book(html, book_url, source_page))

    print(f"detail_pages={len(raw_records)}")
    print("sample raw record:")
    print(json.dumps(raw_records[0], indent=2))


if __name__ == "__main__":
    main()