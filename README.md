# awesome-ucp-demo

**Build a product finder for any hobby, on Shopify's Universal Commerce Protocol.**

`awesome-ucp-demo` is a boilerplate for hobby product-search sites: one Python file of
engine, one HTML file of UI, zero dependencies. It ships configured as
[**DollScout**](https://www.dollscout.com), a live collector-Barbie finder, so cloning
this template gives you a complete working demo. Point it at your own obsession
(vinyl, fountain pens, trading cards, diecast cars) by editing one config file: see
[RETARGETING.md](RETARGETING.md).

It's exactly what the name says: a demo of how much you can build on UCP with almost
nothing — no merchant auth, no API tokens, no dependencies.

**Live demo:** [dollscout.com](https://www.dollscout.com)

![Search results with taxonomy chips, price/stock filters, and sort](screenshots/search.png)

<p>
  <img src="screenshots/quick-view.png" alt="Quick-view modal with image gallery, description, and buy-now link" width="70%">
  <img src="screenshots/mobile-filters.png" alt="Mobile filter bottom-sheet" width="24%">
</p>

## Why this exists

**UCP is demand generation for Shopify stores.** Every result card a finder like this
shows is a free, high-intent referral to an independent merchant: the shopper arrived
already hunting for exactly what that store sells, and one click lands them on the
store's own product page. The merchants do nothing to participate. No integration, no
feed, no fees; their catalog is already in UCP the moment they're on Shopify.

Shopify's [Universal Commerce Protocol](https://www.shopify.com/ucp) exposes that
global catalog, spanning millions of independent stores, searchable with **no merchant
auth and no API tokens**. So a niche product finder is buildable by one person in a
weekend, and every new one someone builds opens a new demand channel for thousands of
stores at once. That's why this boilerplate is free: more finders means more demand
for everyone. Built by [Kurt Elster](https://ethercycle.com), a Shopify partner, as a
contribution to the community. A rising tide lifts all ships.

The model is search-and-referral only. Result cards link out to each merchant's own
product and buy-now pages. The demo never builds carts, never takes payment, holds no
inventory.

## Sponsored results

DollScout has no sponsors: no seller pays for placement, and results are shown in
the order UCP returns them. The template still ships the disclosure mechanism, and
using it is the rule, not a suggestion: **if your deployment has a paid, affiliate,
or other material relationship with a seller, list them in `SPONSORED_SELLERS`
(`domain.py`) and their results get a visible "Sponsored" label** on every result
card and in quick view. Sponsorship never changes ranking or filtering; the label
is pure disclosure. Running a finder that takes money from sellers without labeling
their results is deceptive (and in most jurisdictions, illegal): don't strip this.

## Features

The engine is small but production-hardened (it runs dollscout.com):

- **UCP global catalog search** via the `ucp` CLI: query + taxonomy chips + price /
  in-stock / condition (New / Pre-owned, UCP `filters.condition`) filters, cursor
  pagination, "more like this" similarity search
- **In-memory TTL cache** (repeat searches ~780ms → ~0ms, LRU-capped, warmed at boot
  with your popular queries)
- **Rate limiting** (sliding window per IP) and a **process semaphore** so a traffic
  spike can't fork-bomb the box
- **SEO out of the box**: crawlable `/?q=` deep-links with server-injected per-query
  meta, sitemap of popular searches, robots.txt, canonical/OG/Twitter tags, JSON-LD
- **Security headers** on every response (CSP, nosniff, frame-deny, referrer policy)
- **Polished UI**: suggestion dropdown, typewriter placeholder, quick-view modal with
  gallery, localStorage wishlist, client-side sort, infinite scroll, mobile filter
  bottom-sheet, `prefers-reduced-motion` support
- **Observability**: `/api/stats` with cache hit rate, p50/p95 search latency, and
  reference-match hit rate
- **Reference match index (optional)**: drop three JSON files into `data/` and every
  result whose listing matches a canonical entry in your reference catalog gets a
  "matched: …" badge (see below)
- **Reference pages (optional)**: drop `pages.json` into `data/` and the engine
  server-renders a collector-record page per entry at `/doll/<slug>` — fact table,
  prose, FAQ, live matched listings, line rail, BreadcrumbList+ItemPage JSON-LD —
  and adds them to the sitemap and `/llms.txt`. Gate the routed set with
  `domain.REFERENCE_PILOT_SLUGS`; matched badges link to these pages once
  `domain.REFERENCE_PAGE_BASE` is set

## Quickstart

Requires Python 3.7+ and Node (for the `ucp` CLI). Nothing from PyPI, no build step.

```sh
npm install -g @shopify/ucp-cli@0.6.2
ucp profile init --name agent --activate   # local protocol metadata, no secrets
python3 app.py                             # http://127.0.0.1:8787
```

The profile init is required because the CLI refuses to run without an active local
profile; it contains no keys or credentials. Global catalog search needs no merchant
authorization at all.

> **Version note:** tested against `@shopify/ucp-cli@0.6.2`. UCP is pre-1.0; newer CLI
> versions may change the JSON shape that `normalize()` in `app.py` expects.

## How it works

```
browser (index.html) → /api/search → app.py builds UCP input
                                    → `ucp catalog search --input {...}`
                                    → normalize → JSON → photo grid
```

| File | What it is |
|---|---|
| `app.py` | The engine: HTTP server, cache, rate limiting, SEO routes, security headers, UCP subprocess glue. Domain-agnostic; you should never need to edit it. |
| `domain.py` | **The entire domain configuration.** Brand terms, query anchor, taxonomy chips, popular queries, site origin, meta templates. Retargeting starts here. |
| `index.html` | Single-file vanilla-JS UI. Brand copy is isolated in six fenced `BRAND BLOCK` regions. |
| `DESIGN.md` | Design system for the 2026-07 riso rebrand: tokens (with measured contrast), component rules, a11y acceptance criteria, QA checklist. Read before any visual change. |
| `DESIGN-REFERENCE-PAGES.md` | Design spec for the `/doll/<slug>` reference pages (anatomy, states, JSON-LD rules, QA). |
| `riso.css` | The `:root` design tokens — the single source of every hex value, linked by `index.html` and the reference pages. |
| `privacy.html`, `terms.html` | DollScout's real legal pages. **Replace with your own** (see RETARGETING.md). |
| `og-image.png` | 1200×630 social share card. Replace with your own. |
| `Dockerfile`, `fly.toml` | Fly.io deployment (Node for the CLI + Python for the app in one image). |

Useful implementation details:

- Prices arrive in **minor units** with a currency (`3000` = `$30.00 USD`).
- Pagination is **cursor-based**, followed verbatim from `result.pagination.cursor`.
- UCP has **no server-side sort**, so sorting is client-side over the loaded set.
- Result relevance is a layered pipeline — see the next section.

## Getting accurate results from UCP

The UCP global catalog is all-of-ecommerce with no niche awareness: ask it for
"Ken doll" and it drifts into baby dolls; ask for "1980s" and it returns
80th-anniversary product. The demo gets collector-grade accuracy by **shaping the
query on the way out and filtering results on the way back** — neither layer
alone is enough. Every tip below came from tuning DollScout against live
results, and each maps to a `domain.py` knob.

### Shape the query (send UCP something it can answer)

- **Anchor every query** (`QUERY_ANCHOR`). Prepend the one word your niche
  always includes to any query that lacks it. This single knob does more for
  relevance than anything else.
- **Declare intent** (`SEARCH_INTENT`). Sent as UCP `context.intent` on every
  search, with active chips appended — it nudges ranking toward the shopping
  context you name.
- **Chips are query text, not filters.** UCP has no taxonomy facets, so each
  chip appends sharpener words to the query string. Chip on traits sellers
  actually type into listings: if merchants don't write "playline" or credit a
  designer in the title/description, no query phrasing will surface it.
- **Qualify ambiguous terms.** Bare "1980s" pulled 80th-anniversary dolls;
  "1980s vintage" took that chip from 83% to 96% on-topic.
- **Search names, not numbers.** The catalog doesn't index stock/model numbers.
  When a query contains a stock number that maps to exactly one catalog record,
  append the record's name to the query (`_expand_stock_query` in `app.py`) —
  "1703" alone then finds the 1988 Happy Holidays doll.

### Filter the results (UCP still returns junk)

- **Keep-guard** (`BRAND_TERMS`): drop any result whose title AND description
  both lack a brand marker. Even anchored queries pull generic backfill.
- **Drop licensed merch by collab-brand marker** (`EXCLUDE_BRAND_TERMS`):
  Funko, Hot Wheels, UNO... products that carry your niche's name but aren't
  the collectible. Match on **word boundaries** over normalized text, or "uno"
  hits inside "Bruno".
- **Don't over-block.** DollScout deliberately excludes neither "hallmark" nor
  "swarovski" — real collector dolls carry those words. Their merch is caught
  at the badge layer by generic merch words ("ornament", "figurine") instead.
- **Ban sellers that never name the collab brand** (`BANNED_SELLERS`): a Funko
  specialist titling a Pop just "Holiday Barbie 1988" defeats every text
  filter, so filter on the seller (matched vs. domain, custom host, and name).
- **Route each junk class to the right layer.** Off-topic *query drift* →
  `QUERY_ANCHOR`; on-brand *licensed merch* → `EXCLUDE_BRAND_TERMS`; merch from
  sellers who *don't name the brand* → `BANNED_SELLERS`; merchandise *about* an
  item that shouldn't be identified as the item → `MATCH_NEGATIVE_TERMS`
  (suppresses the badge without dropping the listing).

### Narrow to one doll (the fallback ladder)

When a loose query confidently resolves to a single catalog record
(`resolve_query_intent`), the default view auto-narrows to genuine listings of
*that* doll — quality over quantity. The narrowing is a **scored fallback
ladder**, not a boolean filter (`_focus_ladder` + `_sig_score` in `app.py`):

- **Score, don't AND.** Each listing gets a 0–1 identity score: the
  length-weighted mean of how well the doll's identity words match the title
  (exact token, `difflib` fuzzy variant above `_FUZZY_FLOOR`, or a compound
  substring hit). Longer, more identifying words count more; a difflib ratio
  below the floor earns zero, so a short word like "gold" can't collect spurious
  credit from an unrelated token. `<tier> label` bigrams (Gold/Pink Label…) are
  stripped before scoring so Mattel's edition tiers don't masquerade as identity.
- **Three tiers.** `EXACT_CUTOFF` (essentially every identity word present) is
  the default view. Too few exact matches **and** real near-misses in stock →
  broaden to `CLOSE_CUTOFF` (a majority of identity words), banner-labeled
  "close matches". Nothing even close → show the unfiltered grab-bag with a
  re-narrow escape. An auto-focused query never renders a blank page.
- **Hard gates still bind at every tier.** Merch terms (`MATCH_NEGATIVE_TERMS`),
  a foreign character not in the doll's own name (a Malibu *Ken*), and a
  conditional year gate for weak single-word signatures ("malibu" gets reused
  across years, so there the release year is required) reject a listing outright,
  regardless of score.
- **Calibrate before you deploy.** `tools/calibrate_focus.py` (dev-only, not in
  the Docker image) pulls the real `?all=1` grab-bag for a battery of queries
  from live prod, caches it, and runs the *actual* ladder over those cards so you
  can eyeball kept/close/dropped per query. It hard-asserts the precision wins
  (Totally Hair drops Ken/merch/styling-head; Malibu 1971 drops Ken and
  cross-year reissues; no auto-focus query ends empty) and exits non-zero on a
  regression. Tune the three cutoffs there, not by guessing.

### Measure, then cut

- **Sweep chips against live results.** For each chip, score the fraction of
  returned cards that keep the chip's promise. DollScout's sweep cut a whole
  "Designer" group (2–40%: sellers rarely credit designers in listing text) and
  "Playline" (0%: the catalog reads it as "play" and returns playsets).
- **Hold canned queries to a demo bar.** Every `POPULAR_QUERIES` entry and
  placeholder example must score ≥9/10 relevant in its top 10 live results.
  Anything that demos badly gets cut, no matter how good the joke.
- **Let users tell you what's missing.** `/api/stats` reports `unmatched_top`:
  repeated search terms whose results carried zero badges — your census queue
  for what to catalog next.

## Reference match index (optional)

If you maintain a structured catalog of the collectibles in your niche, the engine can
tag live results with the canonical entry they match — DollScout shows
`matched: 1988 Happy Holidays Barbie Doll #1703` on listings it recognizes.

Drop three JSON files into `data/` (path overridable via `MATCH_INDEX_DIR`); without
them the feature is silently off:

| File | Shape |
|---|---|
| `master.json` | `{id: {name, year, stock_number, ...}}` — one entry per canonical item |
| `stock-numbers.json` | `{normalized_stock: [ids]}` — digits zero-stripped, alpha uppercased |
| `aliases.json` | `{lowercased alias: [ids]}` — each alias should carry year + name |
| `pages.json` (optional) | `{slug: {name, year, facts..., description, faq...}}` — enables the `/doll/<slug>` reference pages |

Matching is deliberately precision-first: an alias must cover the listing **title**
(token-subset, word order free), and a stock number alone never matches — manufacturers
reuse stock numbers, so a stock hit also needs the record's year or identifying name
words in the listing text. Titles containing `domain.MATCH_NEGATIVE_TERMS` (ornaments,
mugs, posters... merchandise *about* an item) never get a badge. `/api/stats` reports
`match_rate` over cards served.

The catalog also powers **search autocomplete**: `/api/suggest` serves one entry per
canonical record (name + stock number as a hidden search alias), and the search box
merges them into its dropdown under a catalog group tag — typing a stock number like
`1703` surfaces the doll it belongs to.

Beyond badges and autocomplete, a loaded catalog switches on:

- **Query expansion** — a search containing an unambiguous stock number also
  searches that record's name ("1703" alone finds the 1988 Happy Holidays doll).
- **Zero-results rescue** — a dead query gets up to three "Try:" chips fuzzy-matched
  from the catalog and taxonomy.
- **"Collect the line" rail** — quick view of a matched item links to its line-mates
  as one-click searches.
- **Sitemap deep-links** — every curated (non-stub) record adds its `/?q=` search to
  `sitemap.xml`.
- **`/llms.txt`** — `domain.LLMS_INTRO` plus a generated catalog section, one linked
  line per record (llmstxt.org convention).
- **Census queue** — `/api/stats` lists `unmatched_top`, the search terms (seen 2+
  times) whose results carried zero badges: what to add to the catalog next.
- **Badge links** — set `domain.REFERENCE_PAGE_BASE` when per-record pages exist and
  matched badges become links to them (dormant while it's `""`).

The JSONs are gitignored (`data/*.json`) — DollScout's are generated from a private
database — but `fly deploy` ships them from the local directory context.

## Deploy (Fly.io)

The included `Dockerfile` and `fly.toml` run one always-on machine (no cold starts).

```sh
brew install flyctl && fly auth login
fly launch --no-deploy --copy-config --name YOUR-APP --org YOUR-ORG --region ord --yes
fly deploy --remote-only     # remote builder; no local Docker needed
fly scale count 1 --yes      # Fly's HA default is 2 machines

# custom domain
fly certs add yourdomain.com
fly certs add www.yourdomain.com
# then create the DNS records `fly certs` prints, and verify:
fly certs check yourdomain.com
```

Update `SITE_ORIGIN` and `REDIRECT_HOSTS` in `domain.py` to match your domain.

## Retargeting to your hobby

The whole point. Follow [RETARGETING.md](RETARGETING.md): edit `domain.py`, walk the
six fenced brand blocks in `index.html`, swap the legal pages and share image, deploy.

## Contributing

PRs welcome. The constraint is the point: keep the backend stdlib-only, keep the
frontend one file, keep the engine domain-agnostic (hobby-specific anything belongs
in `domain.py` or a brand block).

## License and credits

MIT. Built by [Kurt Elster](https://ethercycle.com), host of The Unofficial Shopify
Podcast. Functional inspiration: [bricks.stormdevs.com](https://bricks.stormdevs.com/).

The shipped DollScout configuration is independent and unofficial: not affiliated
with, endorsed by, or sponsored by Mattel. Barbie® is a registered trademark of
Mattel, Inc., used descriptively.
