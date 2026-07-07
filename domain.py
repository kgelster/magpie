"""Domain configuration — this file IS the retargeting surface.

Everything that makes this deployment a collector-Barbie finder (DollScout)
instead of, say, a vinyl or trading-card finder lives here. To point Magpie at
your own hobby, edit this file plus the fenced BRAND blocks in index.html —
see RETARGETING.md for the full checklist. The engine (app.py) never needs
to change.
"""

# --- Site identity ---------------------------------------------------------

# Shown in the startup banner; the visible wordmark lives in index.html.
SITE_NAME = "DollScout"

# Canonical public origin (used for robots/sitemap + host redirects).
SITE_ORIGIN = "https://www.dollscout.com"
# Non-canonical public hostnames that 301 to SITE_ORIGIN (host-gated so Fly
# health checks / direct-IP hits, which use other Host values, pass through).
REDIRECT_HOSTS = {"dollscout.fly.dev", "dollscout.com"}

# --- Search shaping ---------------------------------------------------------

# The UCP global catalog is all-of-ecommerce; without an explicit anchor a
# query like "Ken doll" or "1990s" drifts into generic/baby dolls. The anchor
# is quietly prepended to any query that doesn't already contain it.
QUERY_ANCHOR = "Barbie"

# What an empty search box searches for.
DEFAULT_QUERY = "Barbie collector doll"

# Passed as UCP context.intent on every search (chips get appended).
SEARCH_INTENT = "Barbie collector shopping"

# Relevance guard: keep a result only if its title or description names the
# brand. Distinct from QUERY_ANCHOR — the anchor keeps the *query* on-topic,
# these keep the *results* on-topic.
BRAND_TERMS = ("barbie", "mattel")

# Merchant ban list: results from these sellers are hidden everywhere (search +
# quick-view). Each entry is a lowercase substring matched against the seller's
# myshopify domain, its custom-domain host, and its name — so "sell4value"
# catches sell4value.com, sell4value.myshopify.com, and the "SELL4VALUE"
# display name. Curate per deployment; empty tuple disables the filter.
BANNED_SELLERS = ("sell4value",)

# Sponsored-seller disclosure: any seller with a paid, affiliate, or other
# material relationship to this deployment MUST be listed here. Matching works
# exactly like BANNED_SELLERS (lowercase substring vs. domain, custom-domain
# host, and name). Matched results get a visible "Sponsored" label on the card
# and in quick view. Labeling is the ONLY effect — sponsorship never changes
# ranking or filtering. If you take money from a seller and don't list them
# here, you're deceiving your users. DollScout has no sponsors; empty tuple.
SPONSORED_SELLERS = ()

# Popular searches surfaced as indexable deep-links (/?q=...) so Google can
# crawl them. Also pre-warmed into the search cache at boot.
POPULAR_QUERIES = [
    "Holiday Barbie", "Bob Mackie Barbie", "Silkstone Barbie", "Barbie Signature",
    "NRFB Barbie", "Dolls of the World Barbie", "Byron Lars Barbie", "OOAK Barbie",
    "Barbie Looks", "Birthday Wishes Barbie",
]

# --- SEO meta for /?q= deep-links -------------------------------------------

# .format(q=...) templates for query-specific <title>/description injection.
DEEP_LINK_TITLE = "{q} — Barbie dolls on DollScout"
DEEP_LINK_DESC = ("Find {q} collector Barbie dolls across thousands of independent Shopify shops "
                  "in one search on DollScout.")

# COUPLING TRAP: this must EXACTLY match the og:title / twitter:title content
# attribute in index.html — render_index() find-and-replaces that literal string
# to inject per-query social meta. If they drift apart, deep-link og:title
# silently stops updating. (RETARGETING.md step 3.)
DEFAULT_META_TITLE = "DollScout — The Unofficial Collector Barbie Finder"

# --- 404 page ----------------------------------------------------------------

# Copy for the styled not-found page (app.py wraps these in NOT_FOUND_HTML).
NOT_FOUND_HEADING = "Not every hunt pans out."
NOT_FOUND_BODY = "This page doesn't exist, or wandered off."
NOT_FOUND_CTA = "Back to the search"

# --- Chip taxonomy -----------------------------------------------------------

# Authored public-knowledge taxonomy of Barbie collecting terms: common Barbie
# line/designer/era names used purely as search sharpeners against the UCP
# global catalog. Each chip's ``q`` value is appended to the shopper's query.
# Insertion order is preserved (Python 3.7+ dicts) and drives display order.
TAXONOMY = {
    "Decade": [
        {"label": "1959–60s", "q": "vintage 1960s"},
        {"label": "1970s", "q": "1970s vintage"},
        {"label": "1980s", "q": "1980s"},
        {"label": "1990s", "q": "1990s"},
        {"label": "2000s", "q": "2000s"},
        {"label": "2010s", "q": "2010s"},
        {"label": "2020s", "q": "2020s"},
    ],
    "Line / Series": [
        {"label": "Barbie Signature", "q": "Barbie Signature"},
        {"label": "Silkstone / Fashion Model", "q": "Silkstone Fashion Model Collection"},
        {"label": "Holiday Barbie", "q": "Holiday Barbie"},
        {"label": "Birthday Wishes", "q": "Birthday Wishes"},
        {"label": "Dolls of the World", "q": "Dolls of the World"},
        {"label": "Barbie Looks", "q": "Barbie Looks"},
    ],
    "Designer": [
        {"label": "Bob Mackie", "q": "Bob Mackie"},
        {"label": "Byron Lars", "q": "Byron Lars"},
        {"label": "Robert Best", "q": "Robert Best"},
        {"label": "Carlyle Nuera", "q": "Carlyle Nuera"},
    ],
    "Type": [
        {"label": "Collector", "q": "collector"},
        {"label": "Playline", "q": "playline"},
        {"label": "OOAK", "q": "OOAK one of a kind"},
        {"label": "NRFB", "q": "NRFB new in box"},
    ],
}
