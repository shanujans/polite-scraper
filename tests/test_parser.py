"""Parser tests — no network required. Fixtures live in tests/fixtures/."""

from pathlib import Path
from urllib.parse import urljoin

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.main import (
    normalize_price,
    extract_book,
    dedupe_book_pages,
    BookRecord,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

CATALOGUE_PAGE = "https://books.toscrape.com/catalogue/page-1.html"


def test_price_normalization():
    assert normalize_price("£51.77") == 51.77
    assert normalize_price("£1,299.00") == 1299.0
    assert normalize_price(None) is None
    assert normalize_price("") is None


def test_relative_url_becomes_absolute():
    href = "a-light-in-the-attic_1000/index.html"
    assert urljoin(CATALOGUE_PAGE, href) == (
        "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    )


def test_extract_missing_description_is_none():
    html = (FIXTURES / "book-no-description.html").read_text(encoding="utf-8")
    record = extract_book(html, "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html", CATALOGUE_PAGE)
    assert record["description"] is None
    assert record["title"] == "A Light in the Attic"
    assert record["rating_text"] == "Three"
    assert record["source_page"] == CATALOGUE_PAGE


def test_duplicate_urls_deduplicated():
    dupes = [
        ("https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html", CATALOGUE_PAGE),
        ("https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html", "https://books.toscrape.com/catalogue/page-2.html"),
        ("https://books.toscrape.com/catalogue/tipping-the-velvet_999/index.html", CATALOGUE_PAGE),
    ]
    unique = dedupe_book_pages(dupes)
    assert len(unique) == 2
    assert unique[0][0] == dupes[0][0]
    assert unique[0][1] == CATALOGUE_PAGE  # first source_page kept


def test_malformed_fixture_raises():
    html = (FIXTURES / "book-malformed.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="product_main"):
        extract_book(html, "https://books.toscrape.com/catalogue/not-a-book/index.html", CATALOGUE_PAGE)


def test_clean_record_validates_and_has_numeric_price():
    html = (FIXTURES / "book-with-description.html").read_text(encoding="utf-8")
    raw = extract_book(html, "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html", CATALOGUE_PAGE)
    raw["price_gbp"] = normalize_price(raw["price_text"])
    record = BookRecord(**raw)
    assert isinstance(record.price_gbp, float)
    assert record.description is not None
    assert record.product_url.startswith("https://")