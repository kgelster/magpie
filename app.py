#!/usr/bin/env python3
"""Magpie — a hobby product finder built on the Shopify UCP global catalog.

This is the engine: HTTP server, caching, rate limiting, SEO, and the UCP
subprocess glue. Everything hobby-specific (brand terms, taxonomy, copy)
lives in domain.py — it ships configured as DollScout, a collector-Barbie
finder. See RETARGETING.md to point it at your own niche.

Dependency-free (stdlib only). Run:  python3 app.py  then open the printed URL.
Requires the `ucp` CLI (v0.6.x) on PATH. Global catalog search needs no profile.

Search-only: result cards link out to each merchant's product / buy-now page.
This app never builds carts or checkouts.
"""

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


def _cache_key(params):
    # Key on the actual UCP request so equivalent queries (e.g. same chips in a
    # different order) share one entry. Pagination cursor is part of the input,
    # so each page caches separately.
    return json.dumps(build_ucp_input(**params), sort_keys=True)


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
_stats = {"hits": 0, "misses": 0}
_latencies = deque(maxlen=500)          # recent ucp (cache-miss) search durations, ms


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
    total = hits + misses
    return {
        "cache_hits": hits,
        "cache_misses": misses,
        "hit_rate": round(hits / total, 3) if total else None,
        "ucp_samples": len(lat),
        "ucp_p50_ms": _percentile(lat, 50),
        "ucp_p95_ms": _percentile(lat, 95),
        "cache_size": len(_search_cache),
    }
ROBOTS_TXT = (
    "User-agent: *\n"
    "Allow: /\n"
    "Disallow: /api/\n"               # keep crawlers off the search endpoint (spawns a ucp process)
    f"Sitemap: {domain.SITE_ORIGIN}/sitemap.xml\n"
)


def _build_sitemap():
    rows = [f"  <url><loc>{domain.SITE_ORIGIN}/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>"]
    for term in domain.POPULAR_QUERIES:
        loc = f"{domain.SITE_ORIGIN}/?q={quote(term)}"
        rows.append(f"  <url><loc>{loc}</loc><changefreq>weekly</changefreq><priority>0.7</priority></url>")
    for path in ("/privacy", "/terms"):
        rows.append(f"  <url><loc>{domain.SITE_ORIGIN}{path}</loc><changefreq>yearly</changefreq><priority>0.3</priority></url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(rows) + "\n</urlset>\n")


SITEMAP_XML = _build_sitemap()
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
    background: #EDE2E3; color: #111827; font-family: "Space Grotesk", system-ui, sans-serif;
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
# Pure functions (unit-testable without a running server or the ucp CLI)
# ---------------------------------------------------------------------------

def build_ucp_input(query="", chips=None, price_min=None, price_max=None,
                    available=True, cursor=None, limit=DEFAULT_LIMIT, like=None):
    """Request params -> UCP `catalog search --input` dict.

    chips: list of query-enrichment strings (from taxonomy).
    price_min/price_max: dollars (float) -> converted to minor units.
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

    pagination = {"limit": int(limit)}
    if cursor:
        pagination["cursor"] = cursor

    context = {"address_country": "US", "currency": "USD", "language": "en-US"}

    if like:
        return {"like": [{"id": like}], "context": context,
                "filters": filters, "pagination": pagination}

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
    return {
        "id": p.get("id"),
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
    }


def normalize(raw):
    """Raw UCP search response -> {cards, next_cursor, total_count}. Never raises."""
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
        cards.append(card)
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


def search(params):
    """Glue: params dict -> normalized payload or {error}."""
    raw = run_ucp_search(build_ucp_input(**params))
    if isinstance(raw, dict) and raw.get("error"):
        return raw
    return normalize(raw)


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
                  "available": True, "cursor": None, "like": None}
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
        "cursor": (qs.get("cursor") or [None])[0] or None,
        "like": (qs.get("like") or [None])[0] or None,
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
        elif route == "/robots.txt":
            self._send_text(ROBOTS_TXT, cache_control="public, max-age=3600")
        elif route == "/sitemap.xml":
            self._send_text(SITEMAP_XML, "application/xml; charset=utf-8",
                            cache_control="public, max-age=3600")
        elif route == "/api/taxonomy":
            self._send_json(domain.TAXONOMY, headers={"Cache-Control": "public, max-age=3600"})
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
