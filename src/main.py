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
    html, cached = fetch(FIRST_CATALOGUE_PAGE)
    verb = "CACHE HIT" if cached else "FETCH"
    print(f"{verb} {FIRST_CATALOGUE_PAGE} ({len(html.encode('utf-8'))} bytes)")


if __name__ == "__main__":
    main()