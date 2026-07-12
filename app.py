#!/usr/bin/env python3
"""Magpie — a hobby product finder built on the Shopify UCP global catalog.

This is the engine: HTTP server, caching, rate limiting, SEO, and the UCP
subprocess glue. Everything hobby-specific (brand terms, taxonomy, copy)
lives in domain.py — it ships configured as DollScout, a collector-Barbie
finder. See RETARGETING.md to point it at your own niche.

Dependency-free (stdlib only). Run:  python3 app.py  then open the printed URL.
Requires the `ucp` CLI (v0.6.x) on PATH with an active local profile — run
`ucp profile init --name agent --activate` once (the Dockerfile does this);
without it every operation fails with PROFILE_NOT_FOUND.

Search-only: result cards link out to each merchant's product / buy-now page.
This app never builds carts or checkouts.
"""

import difflib
import html
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque, OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

import domain

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LIMIT = 48          # UCP caps a page at ~50; 48 is the clean max
UCP_TIMEOUT = 20            # global search returns in ~1-2s; fail fast + free the slot

# --- DoS guards for the public /api/search endpoint (each hit spawns a ucp process) ---
UCP_MAX_CONCURRENCY = 4                 # cap simultaneous ucp processes (server-wide)
_ucp_slots = threading.BoundedSemaphore(UCP_MAX_CONCURRENCY)
RATE_LIMIT_MAX = 20                     # max searches per client IP...
RATE_LIMIT_WINDOW = 60                  # ...per this many seconds
RATE_IPS_MAX = 4096                     # sweep idle IPs past this (bounds memory vs. IP rotation)
_rate_lock = threading.Lock()
_rate_hits = defaultdict(deque)         # ip -> deque[monotonic timestamps]

# Content-Security-Policy: 'unsafe-inline' is required (the app is built on inline
# script/style); merchant product images can come from any https host.
CSP = (
    "default-src 'self'; "
    "img-src 'self' https: data:; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "script-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


def _rate_ok(ip):
    """Sliding-window per-IP rate limit. True if this request is within budget."""
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW
    with _rate_lock:
        if len(_rate_hits) > RATE_IPS_MAX:
            for idle in [k for k, v in _rate_hits.items() if not v or v[-1] < cutoff]:
                del _rate_hits[idle]
        dq = _rate_hits[ip]
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= RATE_LIMIT_MAX:
            return False
        dq.append(now)
        return True


# --- In-memory TTL cache for identical searches (fewer ucp spawns, instant repeats) ---
SEARCH_CACHE_TTL = 300                  # seconds a result stays fresh
SEARCH_CACHE_MAX = 512                  # cap distinct queries (bounds memory)
_cache_lock = threading.Lock()
_search_cache = OrderedDict()           # key -> (expiry_monotonic, payload); LRU order


def _ucp_params(params):
    # `focus`/`all` are post-search result controls, not part of the UCP request.
    return {k: v for k, v in params.items() if k not in ("focus", "all")}


def _cache_key(params):
    # Key on the actual UCP request so equivalent queries (e.g. same chips in a
    # different order) share one entry. Pagination cursor is part of the input,
    # so each page caches separately. `focus` changes the filtered output, so it
    # keys separately.
    # `all` (show the unfiltered grab-bag) changes post-filtering, not the UCP
    # request, so it keys separately from the default auto-focused view.
    return json.dumps({"ucp": build_ucp_input(**_ucp_params(params)),
                       "focus": params.get("focus"),
                       "all": bool(params.get("all"))}, sort_keys=True)


def cache_get(key):
    now = time.monotonic()
    with _cache_lock:
        item = _search_cache.get(key)
        if not item:
            return None
        expiry, payload = item
        if expiry < now:
            _search_cache.pop(key, None)
            return None
        _search_cache.move_to_end(key)          # mark most-recently-used
        return payload


def cache_put(key, payload):
    with _cache_lock:
        _search_cache[key] = (time.monotonic() + SEARCH_CACHE_TTL, payload)
        _search_cache.move_to_end(key)
        while len(_search_cache) > SEARCH_CACHE_MAX:
            _search_cache.popitem(last=False)   # evict least-recently-used


# --- Observability: cache hit-rate + ucp latency (exposed at /api/stats) ---
_stats_lock = threading.Lock()
_stats = {"hits": 0, "misses": 0, "match_cards": 0, "total_cards": 0}
_latencies = deque(maxlen=500)          # recent ucp (cache-miss) search durations, ms
_unmatched = {}                         # search term -> count of zero-badge result sets (census queue)


def record_hit():
    with _stats_lock:
        _stats["hits"] += 1


def record_miss(latency_ms):
    with _stats_lock:
        _stats["misses"] += 1
        _latencies.append(latency_ms)


def _percentile(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    k = max(0, min(len(s) - 1, round((p / 100) * (len(s) - 1))))
    return s[k]


def stats_snapshot():
    with _stats_lock:
        hits, misses, lat = _stats["hits"], _stats["misses"], list(_latencies)
        match_cards, total_cards = _stats["match_cards"], _stats["total_cards"]
        unmatched_top = sorted(_unmatched.items(), key=lambda kv: -kv[1])[:10]
    total = hits + misses
    return {
        "cache_hits": hits,
        "cache_misses": misses,
        "hit_rate": round(hits / total, 3) if total else None,
        "ucp_samples": len(lat),
        "ucp_p50_ms": _percentile(lat, 50),
        "ucp_p95_ms": _percentile(lat, 95),
        "cache_size": len(_search_cache),
        "matcher_records": len(_MATCH["recs"]) if _MATCH else 0,
        "match_cards": match_cards,
        "total_cards": total_cards,
        "match_rate": round(match_cards / total_cards, 3) if total_cards else None,
        # Search terms whose results carried zero badges, seen 2+ times: the
        # census backlog ranked by real demand. In-memory (resets on deploy).
        "unmatched_top": [{"q": k, "n": v} for k, v in unmatched_top if v >= 2],
    }
ROBOTS_TXT = (
    "User-agent: *\n"
    "Allow: /\n"
    "Disallow: /api/\n"               # keep crawlers off the search endpoint (spawns a ucp process)
    f"Sitemap: {domain.SITE_ORIGIN}/sitemap.xml\n"
)


def _build_sitemap():
    rows = [f"  <url><loc>{domain.SITE_ORIGIN}/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>"]
    seen = set(domain.POPULAR_QUERIES)
    for term in domain.POPULAR_QUERIES:
        loc = f"{domain.SITE_ORIGIN}/?q={quote(term)}"
        rows.append(f"  <url><loc>{loc}</loc><changefreq>weekly</changefreq><priority>0.7</priority></url>")
    # Reference pages: exactly the routed set (pilot list), nothing thinner.
    for slug in sorted(_PAGES):
        rows.append(f"  <url><loc>{domain.SITE_ORIGIN}/doll/{quote(slug)}</loc>"
                    f"<changefreq>weekly</changefreq><priority>0.8</priority></url>")
    # Canonical reference-catalog deep-links. T3 crawl-budget gate: emit ?q= deep-links
    # ONLY for records that have a routed /doll/ reference page (the pilot set). The full
    # enriched catalog (~3,000 records) stays out of the sitemap so thousands of thin
    # search-landing pages can't dilute crawl focus during T3; the sitemap's ?q= surface
    # grows exactly as REFERENCE_PILOT_SLUGS grows. Matcher + on-site search still serve
    # every record regardless.
    if _MATCH:
        for r in sorted(_MATCH["recs"].values(), key=lambda r: r["name"]):
            if not r["name"] or r["name"] in seen or r.get("slug") not in _PAGES:
                continue
            seen.add(r["name"])
            loc = f"{domain.SITE_ORIGIN}/?q={quote(r['name'])}"
            rows.append(f"  <url><loc>{loc}</loc><changefreq>weekly</changefreq><priority>0.6</priority></url>")
    for path in ("/privacy", "/terms"):
        rows.append(f"  <url><loc>{domain.SITE_ORIGIN}{path}</loc><changefreq>yearly</changefreq><priority>0.3</priority></url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(rows) + "\n</urlset>\n")


INDEX_PATH = os.path.join(HERE, "index.html")

# Styled 404 (copy lives in domain.py; palette mirrors index.html's :root).
NOT_FOUND_HTML = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Page not found · {domain.SITE_NAME}</title>
<style>
  /* riso tokens: paper #EDE2E3, ink #111827, primary #F237A1 (fill+ink text),
     secondary #2C40A7 (heading), tint #6DC6EC (decorative shadow) */
  body {{ margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #EDE2E3; color: #111827; font-family: "Poppins", system-ui, sans-serif;
    text-align: center; }}
  main {{ padding: 32px; }}
  .code {{ font-family: ui-monospace, "SF Mono", monospace; font-size: 12px; font-weight: 500;
    letter-spacing: .05em; color: #111827; margin: 0 0 12px; }}
  h1 {{ font-size: 32px; font-weight: 700; color: #2C40A7; text-shadow: 3px 3px 0 #6DC6EC;
    margin: 0 0 12px; }}
  p {{ color: #4B4A55; margin: 0 0 24px; }}
  a {{ display: inline-block; background: #F237A1; color: #111827; border: 1px solid #111827;
    border-radius: 8px; padding: 12px 24px; font-family: ui-monospace, "SF Mono", monospace;
    font-size: 12px; font-weight: 500; letter-spacing: .05em; text-transform: uppercase;
    text-decoration: none; }}
  a:hover {{ background: #111827; color: #fff; }}
  a:focus-visible {{ outline: 2px solid #111827; outline-offset: 2px; }}
</style></head>
<body><main>
  <p class="code">404</p>
  <h1>{domain.NOT_FOUND_HEADING}</h1>
  <p>{domain.NOT_FOUND_BODY}</p>
  <a href="/">{domain.NOT_FOUND_CTA}</a>
</main></body></html>
"""


def render_index(query):
    """Serve index.html, injecting query-specific <title>/description for /?q= deep-links
    so shared searches have tailored SEO/social meta. Generic copy when no query."""
    with open(INDEX_PATH, encoding="utf-8") as f:
        doc = f.read()
    q = (query or "").strip()
    if not q:
        return doc.encode("utf-8")
    qe = html.escape(q)                          # user input -> escaped for HTML/attr context
    title = domain.DEEP_LINK_TITLE.format(q=qe)
    desc = domain.DEEP_LINK_DESC.format(q=qe)
    doc = re.sub(r"<title>.*?</title>", lambda m: f"<title>{title}</title>", doc, count=1, flags=re.S)
    doc = doc.replace(f'content="{domain.DEFAULT_META_TITLE}"',
                      f'content="{title}"')      # og:title + twitter:title
    for name in ('name="description"', 'property="og:description"', 'name="twitter:description"'):
        doc = re.sub(rf'(<meta {re.escape(name)} content=")[^"]*(">)',
                     lambda m: m.group(1) + desc + m.group(2), doc, count=1)
    return doc.encode("utf-8")

_CURRENCY_SYMBOLS = {"USD": "$", "CAD": "$", "AUD": "$", "GBP": "£", "EUR": "€", "JPY": "¥"}


# ---------------------------------------------------------------------------
# Reference match index (optional): tags result cards with the canonical
# reference entry they match (a "matched: 1988 Happy Holidays Barbie Doll #1703"
# badge). Drop master.json / stock-numbers.json / aliases.json into data/
# (shapes in data/README.md) and the feature switches on; without them it is
# silently off. Matching is precision-first: an alias must cover the listing
# *title*, and a stock number alone never matches — manufacturers reuse stock
# numbers across decades, so a stock hit also needs the record's year or its
# identifying name words somewhere in the listing text.
# ---------------------------------------------------------------------------

MATCH_INDEX_DIR = os.environ.get("MATCH_INDEX_DIR", os.path.join(HERE, "data"))
_MATCH_TOKEN_RE = re.compile(r"[^a-z0-9]+")


def _match_norm(text):
    """Listing text -> ordered normalized token string: lowercased alphanumeric
    runs, digit runs zero-stripped ("#01703" -> "1703") to mirror the index's
    stock normalization."""
    return " ".join(str(int(t)) if t.isdigit() else t
                    for t in _MATCH_TOKEN_RE.split((text or "").lower()) if t)


def _match_tokens(text):
    return set(_match_norm(text).split())


def _load_match_index(path=MATCH_INDEX_DIR):
    """Build the in-memory matcher from the three index files; None if absent/bad."""
    try:
        with open(os.path.join(path, "master.json"), encoding="utf-8") as f:
            master = json.load(f)
        with open(os.path.join(path, "stock-numbers.json"), encoding="utf-8") as f:
            stock = json.load(f)
        with open(os.path.join(path, "aliases.json"), encoding="utf-8") as f:
            aliases = json.load(f)
    except (OSError, ValueError):
        return None
    stop = set(domain.MATCH_STOPWORDS)
    recs = {}
    for rid, m in master.items():
        name = m.get("name") or m.get("full_name") or ""
        year = str(m.get("year") or "")
        recs[rid] = {
            "name": name,
            "stock": m.get("stock_number"),
            "year": year,
            "line": m.get("line"),
            "slug": m.get("seo_slug"),
            "lifecycle": m.get("lifecycle"),
            # Name words that actually identify the record (brand/stop words and
            # the year dropped) — the corroboration a bare stock number needs.
            "sig": frozenset(_match_tokens(name) - stop - {year}),
        }
    # Longest alias first so the most specific match wins.
    alias_sets = sorted(((frozenset(_match_tokens(a)), ids) for a, ids in aliases.items()),
                        key=lambda kv: -len(kv[0]))
    return {"recs": recs, "aliases": alias_sets,
            "stock": {k.lower(): ids for k, ids in stock.items()},
            "neg": tuple(_match_norm(t) for t in domain.MATCH_NEGATIVE_TERMS)}


_MATCH = _load_match_index()
sys.stderr.write(f"  matcher: {len(_MATCH['recs'])} reference records loaded\n" if _MATCH
                 else f"  matcher: no index at {MATCH_INDEX_DIR} (matched badges off)\n")

# Collab-brand result exclusions (normalized once; see domain.EXCLUDE_BRAND_TERMS).
_EXCLUDE_BRANDS = tuple(_match_norm(t) for t in domain.EXCLUDE_BRAND_TERMS)


# ---------------------------------------------------------------------------
# Reference pages (/doll/<slug>): server-rendered collector-record pages from
# data/pages.json (enriched records exported by the data pipeline). Which slugs
# route is domain.REFERENCE_PILOT_SLUGS's call; without pages.json the feature
# is silently off, exactly like the matcher. Design contract: DESIGN.md +
# DESIGN-REFERENCE-PAGES.md (tokens come from /riso.css — no hex here).
# ---------------------------------------------------------------------------

def _load_pages(path=MATCH_INDEX_DIR):
    """slug -> page record for routed slugs; {} when pages.json is absent/bad."""
    try:
        with open(os.path.join(path, "pages.json"), encoding="utf-8") as f:
            pages = json.load(f)
    except (OSError, ValueError):
        return {}
    pilot = set(domain.REFERENCE_PILOT_SLUGS)
    if pilot:
        missing = pilot - pages.keys()
        if missing:
            sys.stderr.write(f"  reference pages: {len(missing)} pilot slugs not in pages.json: "
                             f"{sorted(missing)[:3]}...\n")
        pages = {s: p for s, p in pages.items() if s in pilot}
    for slug, p in pages.items():
        p["slug"] = slug
    return pages


_PAGES = _load_pages()
sys.stderr.write(f"  reference pages: {len(_PAGES)} routed at /doll/<slug>\n" if _PAGES
                 else "  reference pages: no pages.json (routes off)\n")


def _page_siblings(page):
    """Routed line-mates (incl. the page itself) for the 'Collect the line' rail,
    oldest first. Empty when the doll is alone in its line — rail doesn't render."""
    sibs = [p for p in _PAGES.values() if p["line"] and p["line"] == page["line"]]
    sibs.sort(key=lambda p: (p.get("year") or 0, p.get("stock_number") or "", p["slug"]))
    return sibs if len(sibs) > 1 else []


_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def _prov_chip(page):
    """Mono-caps provenance line. Ink text, deliberately not a status color:
    verification is a fact about the record, not an operation outcome."""
    if page.get("verified"):
        chip = f"VERIFIED · {page['sources_n']} SOURCES"
    else:
        chip = "SINGLE-SOURCE RECORD"
    checked = page.get("checked") or ""
    m = re.match(r"(\d{4})-(\d{2})", checked)
    if m:
        chip += f" · CHECKED {_MONTHS[int(m.group(2)) - 1]} {m.group(1)}"
    return chip


def _fact_rows(page):
    """(term, value) rows for the Collector record <dl>. A missing field omits
    its row — never 'N/A' filler. Year is the Class A year, always."""
    rows = []

    def add(term, value):
        if value not in (None, "", []):
            rows.append((term, str(value)))

    add("Year", page.get("year"))
    stock = page.get("stock_number")
    if stock and page.get("other_skus"):
        stock = f"{stock} (also {', '.join(page['other_skus'])})"
    add("Stock number", stock)
    add("Line", page.get("line"))
    add("Designer", page.get("designer"))
    add("Label / edition", page.get("label_tier"))
    if page.get("edition_size"):
        add("Edition size", f"{page['edition_size']:,} pieces")
    add("Body type", page.get("body_type"))
    add("Character", page.get("character"))
    add("Segment", (page.get("segment") or "").capitalize())
    hair = ", ".join(x for x in (page.get("hair_color"), page.get("hair_style")) if x)
    add("Hair", hair)
    add("Outfit", page.get("outfit"))
    add("Accessories", ", ".join(page.get("accessories") or []))
    if page.get("msrp_original"):
        add("Original retail price", f"{page['msrp_original']} (at release)")
    return rows


def _doll_json_ld(page, canonical):
    """BreadcrumbList + ItemPage only. Prohibited here by design contract:
    FAQPage, Product offers, any price property, AggregateRating."""
    crumbs = [{"@type": "ListItem", "position": 1, "name": domain.SITE_NAME,
               "item": domain.SITE_ORIGIN + "/"}]
    if page.get("line"):
        crumbs.append({"@type": "ListItem", "position": 2, "name": page["line"],
                       "item": f"{domain.SITE_ORIGIN}/?q={quote(page['line'])}"})
    crumbs.append({"@type": "ListItem", "position": len(crumbs) + 1, "name": page["name"]})
    item_page = {
        "@type": "ItemPage",
        "url": canonical,
        "name": page.get("seo_title") or page["name"],
        "description": page.get("meta_description") or "",
        "isPartOf": {"@type": "WebSite", "name": domain.SITE_NAME, "url": domain.SITE_ORIGIN},
    }
    if page.get("checked"):
        item_page["dateModified"] = page["checked"]
    return json.dumps([{"@context": "https://schema.org", "@type": "BreadcrumbList",
                        "itemListElement": crumbs},
                       {"@context": "https://schema.org", **item_page}], ensure_ascii=False)


# Reference-page chrome styles: tokens come from /riso.css; component rules here
# reference var(--*) only (QA gate: no raw hex in this template).
_DOLL_CSS = """
  * { box-sizing: border-box; }
  html { -webkit-font-smoothing: antialiased; }
  body { margin: 0; background: var(--porcelain); color: var(--ink);
    font-family: var(--sans); font-size: 16px; line-height: 1.5; }
  .wrap { max-width: 880px; margin: 0 auto; padding: 0 32px; }
  a:focus-visible, button:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
  .mono { font-family: var(--mono); font-size: 12px; font-weight: 500;
    letter-spacing: .05em; text-transform: uppercase; }

  header.site { border-bottom: 1px solid var(--line); }
  header.site .wrap { display: flex; align-items: baseline; justify-content: space-between;
    padding-top: 16px; padding-bottom: 16px; }
  .wordmark { font-weight: 700; font-size: 20px; color: var(--pink-deep);
    text-shadow: 2px 2px 0 var(--tint); text-decoration: none; }
  .searchlink { color: var(--pink-deep); text-decoration: underline;
    text-decoration-color: var(--pink); text-decoration-thickness: 2px; text-underline-offset: 3px; }
  .searchlink:hover { background: var(--pink); color: var(--ink); text-decoration: none; }

  nav.crumb { margin: 24px 0 0; color: var(--ink-soft); }
  nav.crumb ol { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 8px; }
  nav.crumb a { color: var(--pink-deep); }
  nav.crumb .sep { color: var(--ink-soft); }

  .rec-head { position: relative; overflow: hidden; padding: 24px 0 8px; }
  .rec-head .bigstock { position: absolute; right: -8px; top: 0; z-index: 0;
    font-family: var(--mono); font-weight: 300; font-size: clamp(80px, 18vw, 160px);
    line-height: 1; color: var(--tint); user-select: none; pointer-events: none; }
  .rec-head .eyebrow, .rec-head h1, .rec-head .subline { position: relative; z-index: 1; }
  .rec-head h1 { font-size: 32px; font-weight: 700; color: var(--pink-deep);
    text-shadow: 3px 3px 0 var(--tint); margin: 8px 0; }
  .rec-head .subline { margin: 0 0 16px; }

  .prov { display: inline-block; background: var(--paper); border: 1px solid var(--line);
    border-radius: 4px; padding: 4px 12px; margin: 0 0 24px; }

  h2 { font-size: 24px; font-weight: 700; color: var(--pink-deep); margin: 32px 0 12px; }
  h3 { font-size: 20px; font-weight: 500; color: var(--pink-deep); margin: 24px 0 8px; }
  .prose, .faq { max-width: 68ch; }

  dl.facts { background: var(--paper); border: 1px solid var(--line); border-radius: 8px;
    margin: 0; padding: 8px 24px; }
  dl.facts > div { display: flex; gap: 16px; padding: 12px 0; border-bottom: 1px solid var(--line); }
  dl.facts > div:last-child { border-bottom: 0; }
  dl.facts dt { flex: 0 0 160px; color: var(--ink-soft); padding-top: 2px; }
  dl.facts dd { margin: 0; }
  @media (max-width: 640px) {
    dl.facts > div { flex-direction: column; gap: 4px; }
    dl.facts dt { flex: none; }
    .wrap { padding: 0 16px; }
    .rec-head .bigstock { font-size: 80px; }
  }

  .listings { min-height: 380px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; }
  .lcard { display: flex; flex-direction: column; background: var(--paper);
    border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
  .lcard .img { aspect-ratio: 4/5; background: var(--porcelain); }
  .lcard .img img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .lcard .body { padding: 12px 16px 16px; display: flex; flex-direction: column; gap: 4px; flex: 1; }
  .lcard .seller { color: var(--ink-soft); }
  .lcard .title { font-size: 16px; line-height: 1.25; }
  .lcard .title a { color: var(--ink); text-decoration: none; }
  .lcard .title a:hover { color: var(--pink-deep); text-decoration: underline; text-underline-offset: 2px; }
  .lcard .price { color: var(--pink-deep); margin-top: auto; padding-top: 8px; }
  .lcard .sponsored { align-self: flex-start; background: var(--paper); border: 1px solid var(--ink);
    border-radius: 4px; padding: 1px 6px; margin-top: 4px; }
  .empty { color: var(--ink-soft); }
  .empty a { color: var(--pink-deep); text-decoration: underline;
    text-decoration-color: var(--pink); text-decoration-thickness: 2px; text-underline-offset: 3px; }
  .sk { border-radius: 8px; border: 1px solid var(--line); background: var(--paper); height: 340px; }
  @media (prefers-reduced-motion: no-preference) {
    .sk { animation: pulse 1.2s ease-in-out infinite alternate; }
    @keyframes pulse { from { opacity: 1; } to { opacity: .55; } }
  }

  .rail { display: flex; gap: 8px; overflow-x: auto; padding: 4px 2px 12px; }
  .rail a { flex: 0 0 auto; background: var(--paper); color: var(--ink);
    border: 1px solid var(--line); border-radius: 4px; padding: 8px 12px; text-decoration: none; }
  .rail a[aria-current="page"] { background: var(--pink); border-color: var(--ink); }
  .rail a:hover { border-color: var(--ink); }

  footer.site { margin: 48px 0 32px; border-top: 1px solid var(--line); padding-top: 24px;
    color: var(--ink-soft); font-size: 14px; }
  footer.site nav { margin-top: 12px; display: flex; gap: 24px; }
  footer.site a { color: var(--pink-deep); }
"""


def render_doll(page):
    """One reference page -> full HTML. Server-rendered and complete without JS;
    the live-listings section hydrates client-side (progressive enhancement)."""
    e = html.escape
    canonical = f"{domain.SITE_ORIGIN}/doll/{page['slug']}"
    name = page["name"] or ""
    title = page.get("seo_title") or f"{name} | {domain.SITE_NAME}"
    meta_desc = page.get("meta_description") or ""

    crumb_line = ""
    if page.get("line"):
        crumb_line = (f'<li><a href="/?q={quote(page["line"])}">{e(page["line"])}</a></li>'
                      f'<li aria-hidden="true" class="sep">›</li>')
    facts = "\n".join(
        f'      <div><dt>{e(t)}</dt><dd>{e(v)}</dd></div>' for t, v in _fact_rows(page))

    subline = e(page["line"] or "")
    if page.get("designer"):
        subline += f" — designed by {e(page['designer'])}"

    faq_html = ""
    if page.get("faq"):
        qa = "\n".join(f"    <h3>{e(f['q'])}</h3>\n    <p>{e(f['a'])}</p>"
                       for f in page["faq"] if f.get("q") and f.get("a"))
        if qa:
            faq_html = f'  <section class="faq">\n  <h2>{e(domain.REF_H_FAQ)}</h2>\n{qa}\n  </section>'

    rail_html = ""
    sibs = _page_siblings(page)
    if sibs:
        pills = []
        for s in sibs:
            label = e(f"{s.get('year') or ''} #{s.get('stock_number') or ''}".strip())
            cur = ' aria-current="page"' if s["slug"] == page["slug"] else ""
            pills.append(f'    <a class="mono" href="/doll/{quote(s["slug"])}"'
                         f'{cur} aria-label="{e(s["name"] or "")}">{label}</a>')
        rail_html = (f'  <section aria-label="{e(domain.REF_H_LINE)}">\n'
                     f'  <h2>{e(domain.REF_H_LINE)}</h2>\n'
                     f'  <div class="rail">\n' + "\n".join(pills) + "\n  </div>\n  </section>")

    notes_html = ""
    if page.get("collector_notes"):
        notes_html = (f'  <section class="prose">\n  <h2>{e(domain.REF_H_NOTES)}</h2>\n'
                      f'  <p>{e(page["collector_notes"])}</p>\n  </section>')

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(meta_desc)}">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{e(domain.SITE_NAME)}">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(meta_desc)}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{domain.SITE_ORIGIN}/og-image.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;700&family=Overpass+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/riso.css?v=2">
<style>{_DOLL_CSS}</style>
<script type="application/ld+json">{_doll_json_ld(page, canonical)}</script>
</head>
<body>
<header class="site"><div class="wrap">
  <a class="wordmark" href="/" aria-label="{e(domain.SITE_NAME)} home">{e(domain.SITE_NAME)}</a>
  <a class="searchlink" href="/?q={quote(name)}">{e(domain.REF_SEARCH_LINK)}</a>
</div></header>
<main class="wrap">
  <nav class="crumb mono" aria-label="Breadcrumb"><ol>
    <li><a href="/">{e(domain.SITE_NAME)}</a></li>
    <li aria-hidden="true" class="sep">›</li>
    {crumb_line}
    <li aria-current="page">{e(name)}</li>
  </ol></nav>
  <div class="rec-head">
    <span class="bigstock" aria-hidden="true">#{e(page.get("stock_number") or "")}</span>
    <p class="eyebrow mono">{e(str(page.get("year") or ""))} · #{e(page.get("stock_number") or "")}</p>
    <h1>{e(name)}</h1>
    <p class="subline">{subline}</p>
  </div>
  <p class="prov mono">{e(_prov_chip(page))}</p>
  <section class="prose">
    <p>{e(page.get("description") or "")}</p>
  </section>
  <section>
  <h2>{e(domain.REF_H_FACTS)}</h2>
  <dl class="facts">
{facts}
  </dl>
  </section>
{notes_html}
{faq_html}
  <section class="listings" aria-label="{e(domain.REF_H_LISTINGS)}">
  <h2>{e(domain.REF_H_LISTINGS)}</h2>
  <div id="listings" class="grid" aria-live="polite"><div class="sk"></div><div class="sk"></div><div class="sk"></div></div>
  </section>
{rail_html}
  <footer class="site">
    <p>{e(domain.REF_DISCLAIMER)}</p>
    <nav><a href="/">{e(domain.REF_SEARCH_LINK)}</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a></nav>
  </footer>
</main>
<script>
(function () {{
  var box = document.getElementById('listings');
  var q = {json.dumps(name)};
  var here = location.pathname;
  var esc = function (s) {{ var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }};
  function collapse() {{ box.closest('.listings').style.minHeight = '0'; }}
  function empty() {{
    collapse();
    box.classList.remove('grid');
    box.innerHTML = '<p class="empty">' + esc({json.dumps(domain.REF_EMPTY_LISTINGS)}) +
      ' <a href="/?q=' + encodeURIComponent(q) + '">' + esc({json.dumps(domain.REF_EMPTY_CTA)}) + '</a></p>';
  }}
  fetch('/api/search?query=' + encodeURIComponent(q))
    .then(function (r) {{ return r.json(); }})
    .then(function (d) {{
      var cards = (d.cards || []).filter(function (c) {{ return c.matched_page === here; }}).slice(0, 8);
      if (!cards.length) {{ empty(); return; }}
      box.innerHTML = cards.map(function (c) {{
        var pdp = /^https:\\/\\//.test(c.pdp || '') ? c.pdp : null;
        return '<article class="lcard">' +
          '<div class="img">' + (pdp ? '<a href="' + esc(pdp) + '" rel="nofollow noopener" tabindex="-1" aria-hidden="true">' : '') +
            '<img src="' + esc(c.img) + '" alt="" loading="lazy">' + (pdp ? '</a>' : '') + '</div>' +
          '<div class="body">' +
            (c.sponsored ? '<span class="sponsored mono">Sponsored</span>' : '') +
            '<span class="seller mono">' + esc(c.seller_name) + '</span>' +
            '<span class="title">' + (pdp ? '<a href="' + esc(pdp) + '" rel="nofollow noopener">' : '') +
              esc(c.title) + (pdp ? '</a>' : '') + '</span>' +
            '<span class="price">' + esc(c.price_display || '') + '</span>' +
          '</div></article>';
      }}).join('');
    }})
    .catch(function () {{
      collapse();
      box.classList.remove('grid');
      box.innerHTML = '<p class="empty">' + esc({json.dumps(domain.REF_LISTINGS_ERROR)}) + '</p>';
    }});
}})();
</script>
</body></html>
"""


def _suggest_entries():
    """Search-box autocomplete entries from the reference catalog, served at
    /api/suggest: one row per unique record name (ethnicity variants sharing a
    name collapse into one), with the stock number as `alt` — searchable but
    not displayed, so typing "1703" surfaces the doll. [] when no index."""
    entries, seen = [], set()
    recs = _MATCH["recs"].values() if _MATCH else ()
    for r in sorted(recs, key=lambda r: r["name"]):
        # Stubs stay out (same policy as sitemap/llms.txt): raw feed titles are
        # too noisy for autocomplete, but they still feed the matcher.
        if not r["name"] or r["name"] in seen or r["lifecycle"] == "stub":
            continue
        seen.add(r["name"])
        entries.append({"label": r["name"], "q": r["name"], "alt": r["stock"] or "",
                        "line": r["line"] or "", "year": r["year"] or ""})
    return entries


def _build_llms_txt():
    """/llms.txt (llmstxt.org convention): domain.LLMS_INTRO plus one line per
    curated catalog record — linked to its reference page when one is routed,
    to its live search deep-link otherwise."""
    out = domain.LLMS_INTRO.rstrip() + "\n"
    rows = []
    if _MATCH:
        for r in sorted(_MATCH["recs"].values(), key=lambda r: r["name"]):
            if not r["name"] or r["lifecycle"] == "stub":
                continue
            facts = ", ".join(x for x in (str(r["year"] or ""), r["line"] and f"{r['line']} line",
                                          r["stock"] and f"stock #{r['stock']}") if x)
            if r["slug"] in _PAGES:
                url = f"{domain.SITE_ORIGIN}/doll/{quote(r['slug'])}"
            else:
                url = f"{domain.SITE_ORIGIN}/?q={quote(r['name'])}"
            row = f"- [{r['name']}]({url})" + (f": {facts}" if facts else "")
            if row not in rows:                      # variants sharing a name keep the first row
                rows.append(row)
    if rows:
        out += f"\n## {domain.LLMS_CATALOG_HEADING}\n\n" + "\n".join(rows) + "\n"
    return out


# Built after the matcher loads: both walk the reference catalog.
SUGGEST_ENTRIES = _suggest_entries()
SITEMAP_XML = _build_sitemap()
LLMS_TXT = _build_llms_txt()


def _match_record(title, desc=""):
    """Id of the reference entry this listing matches, or None."""
    if not _MATCH:
        return None
    title_norm = _match_norm(title)
    # Merchandise *about* a doll (ornament, mug, poster...) never gets a badge.
    if any(f" {t} " in f" {title_norm} " for t in _MATCH["neg"]):
        return None
    title_toks = set(title_norm.split())
    all_toks = title_toks | _match_tokens(desc)
    alias_ids = next((ids for toks, ids in _MATCH["aliases"] if toks <= title_toks), [])
    stock_ids = []
    for tok in all_toks:
        for rid in _MATCH["stock"].get(tok, ()):
            r = _MATCH["recs"][rid]
            if (r["year"] and r["year"] in all_toks) or (r["sig"] and r["sig"] <= all_toks):
                stock_ids.append(rid)
    # Alias + stock agreement beats alias alone beats stock alone.
    return (next((i for i in alias_ids if i in stock_ids), None)
            or (alias_ids[0] if alias_ids else None)
            or (stock_ids[0] if stock_ids else None))


def _badge(rec):
    return f"{rec['name']} #{rec['stock']}" if rec["stock"] else rec["name"]


def match_reference(title, desc=""):
    """The reference entry this listing matches -> badge text "name #stock", or None."""
    rid = _match_record(title, desc)
    return _badge(_MATCH["recs"][rid]) if rid else None


def _expand_stock_query(q):
    """A query containing a stock number that maps to exactly one catalog record
    gets that record's name appended ("1703 barbie" also searches "1988 Happy
    Holidays Barbie Doll") — the live catalog understands names, not numbers.
    Expands only on a single unambiguous hit whose name isn't already typed."""
    if not _MATCH or not q:
        return q
    toks = _match_tokens(q)
    names = []
    for t in toks:
        ids = _MATCH["stock"].get(t, ())
        if len(ids) == 1:
            r = _MATCH["recs"][ids[0]]
            if r["name"] and not (r["sig"] and r["sig"] <= toks):
                names.append(r["name"])
    if len(names) == 1:
        return f"{q} {names[0]}"
    return q


def _clean_label(r):
    """Human label for the 'Did you mean' UI. Some vintage records embed their stock in
    the name ('Malibu Barbie Doll #1067') and carry a composite stock ('1067-1971'), so
    _badge would read '... #1067 #1067-1971'. Strip any embedded #code from the name and
    append the stock only when it's a clean code not already shown."""
    name = re.sub(r"\s*#\S+", "", r["name"]).strip()
    stk = r["stock"] or ""
    if stk and "-" not in stk and stk.lower() not in _match_norm(name):
        return f"{name} #{stk}"
    return name


def _intent(rid):
    """Compact catalog-record summary for the search 'Did you mean' affordance."""
    r = _MATCH["recs"][rid]
    page = (domain.REFERENCE_PAGE_BASE + r["slug"]) if _PAGES and r["slug"] in _PAGES else None
    return {"id": rid, "name": r["name"], "stock": r["stock"], "year": r["year"] or None,
            "line": r["line"], "label": _clean_label(r), "page": page}


# Barbie-universe characters other than Barbie herself. A listing naming one of these
# (and not part of the focused doll's own name) is a DIFFERENT doll -- a Malibu Ken must
# not survive a "Malibu Barbie" focused search.
FOREIGN_CHARACTERS = frozenset(
    "ken francie skipper midge christie allan alan ricky kelly stacie stacey todd tutti "
    "chris pj steven curtis nikki teresa kira jamie whitney becky courtney".split())


def _fuzzy_in(word, toks, cutoff=0.82):
    """True if `word` is present in `toks` exactly OR as a close fuzzy match
    (difflib ratio >= cutoff). This is the fuzzywuzzy-style tolerance -- built on
    stdlib difflib to keep the image pip-free -- that lets a listing title's plural
    or typo ("hairs", "totaly") still satisfy a required identity word without
    loosening WHICH words must be present."""
    if word in toks:
        return True
    return any(difflib.SequenceMatcher(None, word, t).ratio() >= cutoff for t in toks)


def _focus_keep(title, desc, rid):
    """True if a listing genuinely IS the focused doll. Strict on purpose -- a
    resolved 'show me X' should drop gift-set/merch/reissue noise:
      - no merch term (lanyard, playset, ornament...) in the title;
      - no FOREIGN character in the title that isn't part of the doll's own name;
      - the doll's distinctive NAME words (letters only -- stock digits excluded) are
        ALL present in the title, matched FUZZILY so a plural/typo doesn't drop a
        genuine listing (but every identity word must still be there -- that's what
        rejects a same-year, same-ethnicity but different doll);
      - the release YEAR is in the title ONLY when the name is too generic to stand
        alone. A single short identity word like "malibu" gets reused across years
        and reissues, so there the year is the disambiguator; a distinctive multi-word
        name ("totally hair") IS its own disambiguator, and demanding the year would
        wrongly drop the many genuine listings that omit it."""
    if not _MATCH or rid not in _MATCH["recs"]:
        return True
    r = _MATCH["recs"][rid]
    tnorm = _match_norm(title)
    ttoks = set(tnorm.split())
    if any(f" {t} " in f" {tnorm} " for t in _MATCH["neg"]):
        return False
    name_toks = set(_match_tokens(r["name"]))
    if any(ct in ttoks and ct not in name_toks for ct in FOREIGN_CHARACTERS):
        return False
    sig_words = {t for t in r["sig"] if not t.isdigit()}
    if not (sig_words and all(_fuzzy_in(s, ttoks) for s in sig_words)):
        return False
    # Year gate applies only to weak (single-word) signatures; distinctive names carry
    # their own identity and shouldn't be filtered on a year sellers routinely omit.
    if r["year"] and len(sig_words) < 2:
        return r["year"] in ttoks
    return True


def resolve_query_intent(query):
    """Resolve a free-text query to the ONE catalog record the user most likely means,
    or None. Stricter than the card matcher: only resolves when the intended doll is
    unambiguous -- an alias whose tokens are all present in the query, corroborated by
    the query's own year or stock number, OR a specific (3+ token) alias that names
    exactly one record -- and only when no equally-good record contends. This is what
    lets a loose query like "Malibu Barbie 1971" point at the vintage doll instead of
    the grab-bag of gift sets, playsets, and reissues the live listing search returns."""
    if not _MATCH or not query:
        return None
    toks = set(_match_norm(query).split())
    if not toks:
        return None
    # A merch word in the query means the user is shopping for that thing, not a doll.
    if any(f" {t} " in f" {' '.join(toks)} " for t in _MATCH["neg"]):
        return None
    recs = _MATCH["recs"]
    # Candidate records: any alias whose tokens are a subset of the query (aliases are
    # pre-sorted longest-first, so the most specific alias is seen first per record).
    cands, seen = [], set()
    for a_toks, ids in _MATCH["aliases"]:
        if a_toks <= toks:
            for rid in ids:
                if rid not in seen:
                    seen.add(rid)
                    cands.append((len(a_toks), rid))
    if not cands:
        return None

    def corrob(rid):
        r = recs[rid]
        return (1 if r["year"] and r["year"] in toks else 0) \
             + (1 if r["stock"] and r["stock"].lower() in toks else 0)

    best_alen, best_rid = max(cands, key=lambda al_rid: (corrob(al_rid[1]), al_rid[0]))
    best_score = corrob(best_rid)
    # Confident only with corroboration or a specific alias, AND no equally-good rival.
    if best_score >= 1 or best_alen >= 3:
        rivals = {rid for alen, rid in cands if corrob(rid) == best_score and alen == best_alen}
        if len(rivals) == 1:
            return _intent(best_rid)
    return None


# ---------------------------------------------------------------------------
# Pure functions (unit-testable without a running server or the ucp CLI)
# ---------------------------------------------------------------------------

def build_ucp_input(query="", chips=None, price_min=None, price_max=None,
                    available=True, condition=None, cursor=None,
                    limit=DEFAULT_LIMIT, like=None):
    """Request params -> UCP `catalog search --input` dict.

    Query-side half of the accuracy pipeline (README: "Getting accurate results
    from UCP"): anchor, intent context, chip text, stock->name expansion.

    chips: list of query-enrichment strings (from taxonomy).
    price_min/price_max: dollars (float) -> converted to minor units.
    condition: list of UCP condition values ("new"/"secondhand"); empty/None = any.
    like: a product GID (gid://shopify/...) for "more like this" similarity search;
          when set, similarity drives results and the text query is ignored.
    """
    filters = {"available": bool(available)}
    price = {}
    if price_min is not None:
        price["min"] = int(round(float(price_min) * 100))
    if price_max is not None:
        price["max"] = int(round(float(price_max) * 100))
    if price:
        filters["price"] = price
    if condition:
        # Sorted so ["new","secondhand"] and its reverse share one cache entry.
        filters["condition"] = sorted(condition)

    pagination = {"limit": int(limit)}
    if cursor:
        pagination["cursor"] = cursor

    context = {"address_country": "US", "currency": "USD", "language": "en-US"}

    if like:
        return {"like": [{"id": like}], "context": context,
                "filters": filters, "pagination": pagination}

    query = _expand_stock_query(query)      # "1703 barbie" also searches the doll's name
    chips = [c for c in (chips or []) if c and c.strip()]
    terms = [t for t in ([query] + chips) if t and t.strip()]
    full_query = " ".join(terms).strip() or domain.DEFAULT_QUERY

    # Quietly keep every search on-topic (see domain.QUERY_ANCHOR for why).
    if domain.QUERY_ANCHOR.lower() not in full_query.lower():
        full_query = f"{domain.QUERY_ANCHOR} {full_query}"

    context["intent"] = domain.SEARCH_INTENT
    if chips:
        context["intent"] += ": " + ", ".join(chips)

    return {"query": full_query, "context": context,
            "filters": filters, "pagination": pagination}


def _format_price(amount, currency):
    if amount is None:
        return "Price N/A"
    sym = _CURRENCY_SYMBOLS.get(currency, "")
    return f"{sym}{amount / 100:,.2f} {currency}"


def _image_urls(obj):
    """All image media URLs on a product/variant (for the quick-view gallery), in order.
    Prefers type=='image'; falls back to any media url when listings omit the type."""
    imgs = [m["url"] for m in (obj.get("media") or [])
            if m.get("type") == "image" and m.get("url")]
    if not imgs:
        imgs = [m["url"] for m in (obj.get("media") or []) if m.get("url")]
    seen = set()
    return [u for u in imgs if not (u in seen or seen.add(u))]     # dedupe, keep order


def _seller_banned(seller):
    """True if this seller is on the merchant ban list (matched against domain,
    custom-domain host, and name)."""
    if not domain.BANNED_SELLERS:
        return False
    parts = [seller.get("domain") or "", seller.get("name") or ""]
    url = seller.get("url") or ""
    if url:
        try:
            parts.append(urlparse(url).hostname or "")
        except ValueError:
            pass
    hay = " ".join(parts).lower()
    return any(b in hay for b in domain.BANNED_SELLERS)


def _seller_sponsored(seller):
    """True if this seller is on the sponsored-disclosure list (same matching
    as _seller_banned). Drives the mandatory "Sponsored" label; never ranking."""
    if not domain.SPONSORED_SELLERS:
        return False
    parts = [seller.get("domain") or "", seller.get("name") or ""]
    url = seller.get("url") or ""
    if url:
        try:
            parts.append(urlparse(url).hostname or "")
        except ValueError:
            pass
    hay = " ".join(parts).lower()
    return any(s in hay for s in domain.SPONSORED_SELLERS)


def _card_from_product(p):
    """One UCP product -> card dict, or None if it carries no image or its seller
    is banned.

    Shared by search (list) and get_product (quick-view detail): search media is
    a single image; get_product media is the full gallery. `id` is the product GID
    (for "more like this" + quick-view detail)."""
    variants = p.get("variants") or []
    v = variants[0] if variants else {}
    seller = v.get("seller") or {}
    if _seller_banned(seller):
        return None
    pr = (p.get("price_range") or {}).get("min") or {}
    amount = pr.get("amount")
    currency = pr.get("currency") or "USD"
    images = _image_urls(p) or _image_urls(v)
    if not images:
        return None
    desc_full = (p.get("description") or {}).get("plain") or ""
    # `id` is interpolated into HTML attributes client-side; only accept a real
    # Shopify GID so a malformed/hostile id can't break out of the attribute
    # (same guard as run_ucp_get_product). A dropped id degrades gracefully: the
    # card still renders, it just loses quick-view/wishlist/more-like-this.
    pid = p.get("id")
    if not (isinstance(pid, str) and _PRODUCT_ID_RE.match(pid)):
        pid = None
    rid = _match_record(p.get("title") or "", desc_full)
    rec = _MATCH["recs"][rid] if rid else None
    return {
        "id": pid,
        "title": p.get("title") or "Untitled",
        "seller_name": seller.get("name") or seller.get("domain") or "Unknown seller",
        "seller_domain": seller.get("domain"),
        "price_display": _format_price(amount, currency),
        "amount": amount,
        "currency": currency,
        "pdp": v.get("url"),
        "buy": v.get("checkout_url"),
        "img": images[0],
        "images": images[:8],       # quick-view gallery (capped)
        "rating": (p.get("rating") or {}).get("value"),
        "desc": desc_full[:600],
        "sponsored": _seller_sponsored(seller),
        "matched": _badge(rec) if rec else None,
        "matched_line": rec["line"] if rec else None,          # powers the quick-view line rail
        "matched_page": (domain.REFERENCE_PAGE_BASE + rec["slug"]
                         if rec and rec["slug"] in _PAGES and domain.REFERENCE_PAGE_BASE
                         else None),   # badge links only to ROUTED slugs — a non-pilot
                                       # record's page would 404 (see REFERENCE_PILOT_SLUGS)
    }


def normalize(raw):
    """Raw UCP search response -> {cards, next_cursor, total_count}. Never raises.

    Result-side half of the accuracy pipeline (README: "Getting accurate results
    from UCP"): brand keep-guard, collab-merch exclusion, seller ban (in
    _card_from_product)."""
    result = (raw or {}).get("result") or {}
    cards = []
    for p in (result.get("products") or []):
        card = _card_from_product(p)
        if not card:
            continue  # collectors browse by photo; drop image-less listings
        # The UCP global catalog returns some off-topic items even with the query
        # anchor; drop anything whose title AND description both lack a brand marker.
        if not any(term in (card["title"] + " " + card["desc"]).lower() for term in domain.BRAND_TERMS):
            continue
        # Collab/licensed merch (Funko, Hot Wheels, UNO...) carries the brand
        # marker but isn't the collectible itself; word-boundary match so "uno"
        # can't hit inside "Bruno" (uses the matcher's normalizer, which works
        # with or without a match index loaded).
        hay = f" {_match_norm(card['title'] + ' ' + card['desc'])} "
        if any(f" {t} " in hay for t in _EXCLUDE_BRANDS):
            continue
        cards.append(card)
    if _MATCH and cards:
        # Matcher hit-rate over cards actually served (cache misses only —
        # cached payloads carry their matched badges without re-counting).
        with _stats_lock:
            _stats["total_cards"] += len(cards)
            _stats["match_cards"] += sum(1 for c in cards if c["matched"])
    pagination = result.get("pagination") or {}
    next_cursor = pagination.get("cursor") if pagination.get("has_next_page") else None
    return {
        "cards": cards,
        "next_cursor": next_cursor,
        "total_count": pagination.get("total_count"),
    }


# ---------------------------------------------------------------------------
# UCP subprocess call (deterministic error handling, no model)
# ---------------------------------------------------------------------------

def run_ucp_search(input_dict, timeout=UCP_TIMEOUT):
    try:
        proc = subprocess.run(
            ["ucp", "catalog", "search", "--input", json.dumps(input_dict)],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        sys.stderr.write("  ucp CLI not found on PATH\n")
        return {"error": "Search is temporarily unavailable."}
    except subprocess.TimeoutExpired:
        return {"error": "Search timed out. Try a narrower query."}
    if proc.returncode != 0:
        # Log the real detail server-side; don't leak ucp internals to clients.
        detail = (proc.stderr or proc.stdout or "").strip()[:500]
        sys.stderr.write(f"  ucp exited {proc.returncode}: {detail}\n")
        return {"error": "Search failed. Please try again."}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.stderr.write("  ucp returned non-JSON output\n")
        return {"error": "Search failed. Please try again."}


def _note_unmatched(query, cards):
    """Count search terms whose (non-empty) results carried zero matched badges:
    the census backlog, ranked by real demand. Bounded, in-memory, surfaces at
    /api/stats once a term has been seen twice."""
    if not (_MATCH and cards and query):
        return
    term = " ".join(query.split()).lower()[:60]
    if not term or "@" in term or any(c.get("matched") for c in cards):
        return
    with _stats_lock:
        if term in _unmatched or len(_unmatched) < 500:
            _unmatched[term] = _unmatched.get(term, 0) + 1


def search(params):
    """Glue: params dict -> normalized payload or {error}.

    Accuracy affordances layer on top of the raw listing search:
      - AUTO-FOCUS: when a loose query confidently resolves to one catalog doll, the
        default view is already narrowed to genuine matches of that doll -- quality
        over quantity. A wrong first card (a same-year, same-ethnicity but DIFFERENT
        doll UCP happened to rank first) never surfaces; an empty filtered set is the
        honest answer, with a 'show all N results' escape.
      - focus: an explicit client focus on a resolved doll (same filter, no escape).
      - all=1: the visitor's escape from auto-focus -- return the unfiltered grab-bag
        but still offer to re-narrow (show_all_active + did_you_mean)."""
    focus = params.get("focus")
    show_all = bool(params.get("all"))
    orig_query = params.get("query")
    raw = run_ucp_search(build_ucp_input(**_ucp_params(params)))
    if isinstance(raw, dict) and raw.get("error"):
        return raw
    result = normalize(raw)
    auto = False
    if not focus and not show_all:
        dym = resolve_query_intent(orig_query)
        if dym:
            focus, auto = dym["id"], True          # narrow by default
    if focus:
        raw_total = result.get("total_count")
        result["cards"] = [c for c in result["cards"]
                           if _focus_keep(c["title"], c.get("desc", ""), focus)]
        result["total_count"] = len(result["cards"])
        result["next_cursor"] = None            # focused view is one curated page
        if _MATCH and focus in _MATCH["recs"]:
            result["focused"] = _intent(focus)
            if auto:
                result["focused"]["auto"] = True
                result["focused"]["raw_total"] = raw_total
    else:
        dym = resolve_query_intent(orig_query)
        if dym:
            result["did_you_mean"] = dym
            if show_all:
                result["show_all_active"] = True   # escaped the auto-narrow; offer to re-narrow
    _note_unmatched(orig_query, result["cards"])
    return result


# Product GIDs are the only accepted id (they're passed as a subprocess arg, so this
# also blocks flag-injection like an id starting with "-").
_PRODUCT_ID_RE = re.compile(r"^gid://shopify/[A-Za-z0-9._/-]+$")


def run_ucp_get_product(pid, timeout=UCP_TIMEOUT):
    if not _PRODUCT_ID_RE.match(pid or ""):
        return {"error": "Invalid product id."}
    try:
        proc = subprocess.run(
            ["ucp", "catalog", "get_product", pid, "--format", "json"],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        sys.stderr.write("  ucp CLI not found on PATH\n")
        return {"error": "Product detail is temporarily unavailable."}
    except subprocess.TimeoutExpired:
        return {"error": "Timed out fetching product detail."}
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:500]
        sys.stderr.write(f"  ucp get_product exited {proc.returncode}: {detail}\n")
        return {"error": "Could not load product detail."}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.stderr.write("  ucp get_product returned non-JSON output\n")
        return {"error": "Could not load product detail."}


def get_product(pid):
    """Full product detail (all gallery images + longer description) for quick view."""
    raw = run_ucp_get_product(pid)
    if isinstance(raw, dict) and raw.get("error"):
        return raw
    p = ((raw or {}).get("result") or {}).get("product") or {}
    card = _card_from_product(p)
    if not card:
        return {"error": "Product detail unavailable."}
    return card


def warm_cache():
    """Pre-fill the TTL cache with the popular sitemap queries so first visitors hit
    a warm cache. Runs in a daemon thread at boot; params mirror a real /?q= request."""
    for term in domain.POPULAR_QUERIES:
        params = {"query": term, "chips": [], "price_min": None, "price_max": None,
                  "available": True, "condition": sorted(domain.DEFAULT_CONDITION),
                  "cursor": None, "like": None}
        key = _cache_key(params)
        if cache_get(key) is not None:
            continue
        result = search(params)
        if not (isinstance(result, dict) and result.get("error")):
            cache_put(key, result)
        time.sleep(0.5)                     # gentle: don't spawn every ucp at once at boot
    sys.stderr.write(f"  cache warmed: {len(domain.POPULAR_QUERIES)} popular queries\n")


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

def _parse_search_query(qs):
    def _num(key):
        vals = qs.get(key)
        if not vals or vals[0] == "":
            return None
        try:
            return float(vals[0])
        except ValueError:
            return None

    return {
        "query": (qs.get("query") or [""])[0],
        "chips": qs.get("chip") or [],
        "price_min": _num("price_min"),
        "price_max": _num("price_max"),
        "available": (qs.get("available") or ["1"])[0] != "0",
        "condition": [c for c in (qs.get("condition") or [])
                      if c in ("new", "secondhand")],
        "cursor": (qs.get("cursor") or [None])[0] or None,
        "like": (qs.get("like") or [None])[0] or None,
        "focus": (qs.get("focus") or [None])[0] or None,
        "all": (qs.get("all") or [None])[0] == "1",
    }


class Handler(BaseHTTPRequestHandler):
    def end_headers(self):
        # Baseline security headers on every response (incl. errors).
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        self.send_header("Content-Security-Policy", CSP)
        super().end_headers()

    def _client_ip(self):
        # Behind Fly's proxy, Fly-Client-IP is the trusted client address.
        return (self.headers.get("Fly-Client-IP")
                or self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or self.client_address[0])

    def _send_json(self, obj, status=200, headers=None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body, content_type, cache_control=None):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type, cache_control=None):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            self._send_not_found()
            return
        self._send_bytes(body, content_type, cache_control)

    def _send_not_found(self):
        body = NOT_FOUND_HTML.encode("utf-8")
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, content_type="text/plain; charset=utf-8", cache_control=None):
        self._send_bytes(text.encode("utf-8"), content_type, cache_control)

    def do_GET(self):
        # Health check: cheap 200, no ucp, no host redirect (Fly checks hit this).
        if self.path == "/healthz":
            self._send_text("ok\n")
            return
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        if host in domain.REDIRECT_HOSTS:
            self.send_response(301)
            self.send_header("Location", domain.SITE_ORIGIN + self.path)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/" or route == "/index.html":
            q = (parse_qs(parsed.query).get("q") or [""])[0]
            self._send_bytes(render_index(q), "text/html; charset=utf-8",
                             cache_control="public, max-age=300")
        elif route == "/riso.css":
            self._send_file(os.path.join(HERE, "riso.css"), "text/css; charset=utf-8",
                            cache_control="public, max-age=3600")
        elif route.startswith("/doll/"):
            page = _PAGES.get(route[len("/doll/"):])
            if page:
                self._send_bytes(render_doll(page).encode("utf-8"),
                                 "text/html; charset=utf-8",
                                 cache_control="public, max-age=300")
            else:
                self._send_not_found()
        elif route == "/og-image.png":
            self._send_file(os.path.join(HERE, "og-image.png"), "image/png",
                            cache_control="public, max-age=86400")
        elif route == "/hero-bg.jpg":
            self._send_file(os.path.join(HERE, "hero-bg.jpg"), "image/jpeg",
                            cache_control="public, max-age=86400")
        elif route == "/privacy":
            self._send_file(os.path.join(HERE, "privacy.html"), "text/html; charset=utf-8",
                            cache_control="public, max-age=3600")
        elif route == "/terms":
            self._send_file(os.path.join(HERE, "terms.html"), "text/html; charset=utf-8",
                            cache_control="public, max-age=3600")
        elif route == "/" + domain.GSC_VERIFICATION_TOKEN + ".html":
            # Search Console ownership check (see domain.GSC_VERIFICATION_TOKEN).
            self._send_text("google-site-verification: %s.html\n"
                            % domain.GSC_VERIFICATION_TOKEN,
                            "text/html; charset=utf-8")
        elif route == "/robots.txt":
            self._send_text(ROBOTS_TXT, cache_control="public, max-age=3600")
        elif route == "/llms.txt":
            self._send_text(LLMS_TXT, cache_control="public, max-age=3600")
        elif route == "/sitemap.xml":
            self._send_text(SITEMAP_XML, "application/xml; charset=utf-8",
                            cache_control="public, max-age=3600")
        elif route == "/api/taxonomy":
            self._send_json(domain.TAXONOMY, headers={"Cache-Control": "public, max-age=3600"})
        elif route == "/api/suggest":
            # Reference-catalog autocomplete entries (no ucp spawn; cheap).
            self._send_json(SUGGEST_ENTRIES, headers={"Cache-Control": "public, max-age=3600"})
        elif route == "/api/stats":
            self._send_json(stats_snapshot(), headers={"Cache-Control": "no-store"})
        elif route == "/api/search":
            if not _rate_ok(self._client_ip()):
                self._send_json({"error": "Too many searches. Please slow down."}, status=429)
                return
            params = _parse_search_query(parse_qs(parsed.query))
            key = _cache_key(params)
            cached = cache_get(key)
            if cached is not None:                      # cache hit: no ucp process needed
                record_hit()
                self._send_json(cached, headers={"X-Cache": "HIT", "Cache-Control": "no-store"})
                return
            if not _ucp_slots.acquire(blocking=False):
                self._send_json({"error": "The finder is busy right now. Try again in a moment."}, status=503)
                return
            try:
                t0 = time.monotonic()
                result = search(params)
                ms = round((time.monotonic() - t0) * 1000)
                record_miss(ms)
                if not (isinstance(result, dict) and result.get("error")):
                    cache_put(key, result)              # only cache successes
                sys.stderr.write(f"  search MISS {ms}ms q={params.get('query') or params.get('like') or ''!r}\n")
                self._send_json(result, headers={"X-Cache": "MISS", "Cache-Control": "no-store"})
            finally:
                _ucp_slots.release()
        elif route == "/api/product":
            # Quick-view detail: fetch the full gallery + description for one product.
            if not _rate_ok(self._client_ip()):
                self._send_json({"error": "Too many requests. Please slow down."}, status=429)
                return
            pid = (parse_qs(parsed.query).get("id") or [""])[0]
            key = "product:" + pid
            cached = cache_get(key)
            if cached is not None:
                record_hit()
                self._send_json(cached, headers={"X-Cache": "HIT", "Cache-Control": "no-store"})
                return
            if not _ucp_slots.acquire(blocking=False):
                self._send_json({"error": "The finder is busy right now. Try again in a moment."}, status=503)
                return
            try:
                t0 = time.monotonic()
                result = get_product(pid)
                ms = round((time.monotonic() - t0) * 1000)
                record_miss(ms)
                if not (isinstance(result, dict) and result.get("error")):
                    cache_put(key, result)
                self._send_json(result, headers={"X-Cache": "MISS", "Cache-Control": "no-store"})
            finally:
                _ucp_slots.release()
        else:
            self._send_not_found()

    def log_message(self, fmt, *args):  # quieter console
        sys.stderr.write("  %s\n" % (fmt % args))


def main():
    port = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 8787))
    # HOST defaults to localhost for safe local runs; the container sets HOST=0.0.0.0.
    host = os.environ.get("HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"{domain.SITE_NAME} running at {url}  (Ctrl-C to stop)")
    if os.environ.get("WARM_CACHE", "1") != "0":
        threading.Thread(target=warm_cache, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
