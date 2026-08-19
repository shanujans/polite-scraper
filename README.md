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

## Sample run report

```json
{
  "start_time": "2026-08-20T18:26:54.611471Z",
  "finished_at": "2026-08-20T18:26:55.212964Z",
  "duration_seconds": 0.61,
  "pages_fetched": 3,
  "cache_hits": 63,
  "valid_records": 60,
  "invalid_records": 0,
  "failed_pages": 0,
  "target": "https://books.toscrape.com",
  "user_agent": "FlyRankInternship-A9/1.0 (+https://github.com/shanujans/be05-polite-scraper)"
}
```

A fresh run that hits the network reports `cache_hits: 0` and takes a few
minutes (60 detail pages at 0.5 s apart); re-runs read from cache and finish in
under a second.

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

## Verify failure handling (optional)

```bash
python src/main.py --include-bad-url
```

Adds one made-up book URL to the list on purpose. The run finishes, the 60 good
records survive, and `output/run-report.json` shows `failed_pages: 1`.

## AI vs me (bonus rematch)

The bonus stage: I wrote the spec, asked an AI to build the same pipeline in
quarantine (`ai-version/`), ran it against my own checkpoints, then improved my
prompt and regenerated once (`ai-version/rematch/`).

### My prompt (round 2, after the first rematch)

> Target: https://books.toscrape.com/, first 3 catalogue pages. Do NOT hardcode
> the 3 page URLs — start at page-1.html, follow the catalogue's own "next" link
> and stop after exactly 3 pages. Discover the 60 unique book URLs (urljoin, never
> string glue, dedupe). Extract 8 raw fields per book (title, product_url,
> price_text, availability_text, rating_text, description [nullable], source_page,
> fetched_at). Clean schema: add price_gbp as a float via Pydantic; failing records
> go to errors.json, never books.json. Politeness: honest user-agent, 10s timeout,
> >=0.5s delay, cache every page and reuse on re-runs, only HTTP 200 parsed.
> Idempotency: two runs → same 60 records. Failure handling: per-page try/except,
> one broken page skipped; never retry 404/403, retry once on timeout/5xx.
> Add `--include-bad-url` flag that injects one 404 URL and must still finish with
> failed_pages=1. Run report: start/end time, duration, pages, cache hits, valid,
> invalid, failed_pages (an integer count). Python 3.10+, decode responses as UTF-8.

### Checkpoint results

| Checkpoint | Mine | AI (round 1) | AI (round 2) |
|------------|------|--------------|--------------|
| Discovers 60 unique URLs | ✅ | ✅ | ✅ |
| All 8 raw fields + price_gbp | ✅ | ✅ | ✅ |
| Idempotent (2 runs → 60, not 120) | ✅ | ✅ (byte-identical) | ✅ |
| One broken page skipped, run survives | ✅ | ✅ | ✅ |
| `failed_pages` reported as a count | ✅ | ❌ (list of URLs) | ✅ |
| Follows site's own "next" links | ✅ | ❌ (hardcoded 3 URLs) | ✅ |

### What the AI did better — and do I understand it?

- **Byte-identical re-runs.** The AI cached `fetched_at` *with* the HTML, so a
  cache hit returns the original timestamp and two runs produce identical files.
  Mine re-stamps `fetched_at` on every run, so the field differs run to run. I
  understand the code and I'd actually call this a wash: the assignment's
  idempotency test only requires the same 60 records, but the AI's approach is
  the more defensible "don't lie about when you fetched it" behaviour.
- **Non-zero exit code when pages failed** — tiny, sensible, I understand it.

### What it got wrong or silently skipped

- Round 1 **hardcoded the 3 catalogue page URLs** instead of following the site's
  `next` links. It worked today because the site didn't change, but it would break
  silently the day the site reorders pages. My round-2 prompt called this out
  explicitly, and the AI fixed it.
- Round 1 reported `failed_pages` as a **list of URLs**, not the count the spec
  asks for. Again, my round-2 prompt fixed it by saying "an integer count".
- Round 1 had **no failure-injection test** — it *handled* broken pages but I had
  to ask for a way to *prove* it. My prompt added `--include-bad-url`.

### What my prompt forgot to say

- I never specified the **exit code** convention or that `errors.json` should hold
  only schema failures (not page-level 404s). The AI made reasonable judgment
  calls here that I agree with.
- I said "cache every page" but didn't say **what a cache hit should return for
  `fetched_at`** — the AI chose to freeze the original timestamp, which changed
  the observable output. A better prompt names the exact contract.

### One rematch

Round 1 → round 2 changed: hardcoded page list → follow `next` links;
`failed_pages` list → integer count; added `--include-bad-url`. Both rounds
collected 60 records and survived a broken page, which tells me the core spec was
clear — the differences were all in details I hadn't pinned down, and once I wrote
them into the prompt they disappeared. The skill is writing the prompt that leaves
nothing to guess.