# BE-05 · The Polite Scraper

A small, polite scraping pipeline that turns 3 pages of messy HTML from
[Books to Scrape](https://books.toscrape.com/) into clean, schema-checked JSON —
without ever being rude to the server.

## Target classification

| Question | Answer |
|----------|--------|
| **Site** | Books to Scrape — `https://books.toscrape.com/` |
| **Why** | A public **sandbox** built for scraping practice. The site's own homepage says *"We love being scraped!"* and its title is *"Books to Scrape - Sandbox"*. That sentence on their page is the permission. |
| **Scope** | The first 3 catalogue pages only (`page-1.html` … `page-3.html`) → 60 book detail pages. |
| **Data collected** | For each book: title, product URL, price text, price (GBP), availability, rating, description, plus provenance (source page + fetch time). |
| **Why appropriate** | The site exists so people can practise scraping on it. We take the minimum needed for the task, go slowly, identify ourselves, and never touch another site. |

**Robots check:** `GET https://books.toscrape.com/robots.txt` returned **HTTP 404 — no robots file found**. A missing file is not permission, it is just a missing file. We still behave politely: identifying user-agent, timeout, cache, and a half-second delay between real requests.

> **I will not reuse this code on another site without checking its rules and terms first.**

## Run it

```bash
pip install -r requirements.txt
python src/main.py
```

Output lands in `output/`:

- `output/books.json` — 60 validated, unique book records
- `output/errors.json` — any record that failed schema validation, with the reason
- `output/run-report.json` — honest numbers for the run

## Record schema

```json
{
  "title": "A Light in the Attic",
  "product_url": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
  "price_text": "£51.77",
  "price_gbp": 51.77,
  "availability_text": "In stock (22 available)",
  "rating_text": "Three",
  "description": "It's hard to imagine a world without A Light in the Attic.",
  "source_page": "https://books.toscrape.com/catalogue/page-1.html",
  "fetched_at": "2026-08-19T10:00:00Z"
}
```

`price_gbp` is the cleaned number (kept next to the raw `price_text`). `description`
is `null` when the page has none — we never invent text that was not on the page.

## Politeness rules

- **User-agent:** `FlyRankInternship-A9/1.0 (+<repo-url>)` — an honest, identifying header.
- **Timeout:** every request gives up after 10 seconds.
- **Delay:** at least 0.5 s between real requests to the site.
- **Cache:** every page is saved to `cache/` after its first fetch; re-runs read from cache and never re-hit the server.
- **Status check:** only HTTP 200 is parsed. 404/403 are not retried; 5xx/timeouts get one retry.
- **Scope:** 3 catalogue pages, 60 books. Nothing else.

## Failure behaviour

One broken page is logged and skipped — the other 59 survive and the run still
finishes. `output/run-report.json` records `failed_pages`, cache hits, valid and
invalid records, and duration.

## Why this needed no browser

The data is already in the HTML the server sends — prices, availability, ratings,
descriptions are all present as plain markup. A browser would only render that HTML
into a picture and add cost. A plain HTTP request gets the same data for a fraction
of the time and memory.

## Ethics note

Use an official API when one exists. Never bypass logins, paywalls, or blocks.
Collect only what you need. This project touches exactly one site — a public
sandbox that exists for this purpose — and asks it for the minimum, slowly, once.

## Honest limitation

`rating_text` is the human word ("Three", "Four") from a CSS class, not a number —
the mapping stays in the raw value rather than being guessed into a star count.
The price normaliser only handles the `£1,234.56` format Books to Scrape uses;
a site with a different currency format would need a different normaliser.