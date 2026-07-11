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
# Google Search Console ownership token (URL-prefix property on SITE_ORIGIN).
# Served at /<token>.html; GSC re-checks it periodically, so keep it routed.
GSC_VERIFICATION_TOKEN = "google3dc13d11640e4472"

# --- Search shaping ---------------------------------------------------------
# Accuracy model (full tips: README "Getting accurate results from UCP"): shape
# the query out (anchor, intent, chip text, stock->name expansion), then filter
# results back (BRAND_TERMS keep-guard, EXCLUDE_BRAND_TERMS, BANNED_SELLERS).
# The catalog is all-of-ecommerce; neither layer alone is enough.

# The UCP global catalog is all-of-ecommerce; without an explicit anchor a
# query like "Ken doll" or "1990s" drifts into generic/baby dolls. The anchor
# is quietly prepended to any query that doesn't already contain it.
QUERY_ANCHOR = "Barbie"

# What an empty search box searches for.
DEFAULT_QUERY = "Barbie collector doll"

# Passed as UCP context.intent on every search (chips get appended).
SEARCH_INTENT = "Barbie collector shopping"

# Condition filter the UI starts with (UCP filters.condition values; empty tuple
# = Any). Collectors hunt the secondary market, so Pre-owned is the default.
# COUPLING: must match DEFAULT_CONDITIONS in index.html — app.py warms the boot
# cache with these so first visitors hit warm entries.
DEFAULT_CONDITION = ("secondhand",)

# Base path for matched-badge links to reference pages ("" = badges stay plain
# text). Set since 2026-07-11: every matched badge whose record slug is routed
# (see REFERENCE_PILOT_SLUGS below) links to its /doll/ reference page.
REFERENCE_PAGE_BASE = "/doll/"

# --- Reference pages (/doll/<slug>) ------------------------------------------

# Which pages.json slugs actually get routed. Non-empty = pilot mode (only these
# route; sitemap + llms.txt list exactly this set). Empty tuple = every record
# in data/pages.json routes. Pilot 2026-07-11: the complete enriched Holiday
# Barbie line (closed "Collect the line" rail — every sibling link resolves)
# plus BFMC 2004 (includes the conflicted-year 45th Anniversary QA case).
REFERENCE_PILOT_SLUGS = (
    "1988-happy-holidays-barbie", "1989-happy-holidays-barbie",
    "1990-happy-holidays-barbie-4098", "1990-happy-holidays-barbie-4543",
    "1991-happy-holidays-barbie-1871", "1991-happy-holidays-barbie-2696",
    "1992-happy-holidays-barbie-1429", "1992-happy-holidays-barbie-2396",
    "1993-happy-holidays-barbie-10824", "1993-happy-holidays-barbie-10911",
    "1994-happy-holidays-barbie-12155", "1994-happy-holidays-barbie-12156",
    "1995-happy-holidays-barbie-14123", "1995-happy-holidays-barbie-14124",
    "1996-happy-holidays-barbie-15646", "1996-happy-holidays-barbie-15647",
    "1997-happy-holidays-barbie-17832", "1997-happy-holidays-barbie-17833",
    "1997-happy-holidays-barbie-20416",
    "1998-happy-holidays-barbie-20200", "1998-happy-holidays-barbie-20201",
    "2005-holiday-barbie-doll-by-bob-mackie", "2005-holiday-barbie-doll-by-bob-mackie-2",
    "2005-holiday-barbie-doll-by-bob-mackie-3", "2005-holiday-barbie-doll-by-bob-mackie-4",
    "2006-holiday-barbie-doll-by-bob-mackie", "2006-holiday-barbie-doll-by-bob-mackie-2",
    "45th-anniversary-barbie-2004", "chinoiserie-red-midnight-barbie-2004",
    "chinoiserie-red-moon-barbie-2004", "chinoiserie-red-sunset-barbie-2004",
)

# Fixed reference-page strings (voice: chrome may be playful; the record itself
# renders verbatim from pages.json — no template-side paraphrase, and no
# value-talk anywhere: worth/value/rare are banned tokens on this surface).
REF_SEARCH_LINK = "Search live listings"
REF_H_FACTS = "Collector record"
REF_H_NOTES = "Collector notes"
REF_H_FAQ = "Questions collectors ask"
REF_H_LISTINGS = "Live listings for this doll"
REF_H_LINE = "Collect the line"
REF_EMPTY_LISTINGS = "None on the market right now. She'll turn up."
REF_EMPTY_CTA = "Watch the live search"
REF_LISTINGS_ERROR = "Live listings couldn't load. Refresh to retry."
REF_DISCLAIMER = ("DollScout is an independent collector reference and is not affiliated with, "
                  "endorsed by, or sponsored by Mattel, Inc. Barbie is a trademark of Mattel, Inc.")

# --- /llms.txt (AI-crawler guidance, llmstxt.org convention) -----------------

# Served at /llms.txt; app.py appends the reference-catalog section below this.
LLMS_INTRO = """# DollScout

> DollScout (https://www.dollscout.com) is a search engine for collector Barbie
> dolls across independent Shopify shops, built on the Shopify UCP global
> catalog. Search by doll name, line, designer, era, or Mattel stock number;
> results link to each merchant's own product page.

DollScout is an independent collector reference and is not affiliated with,
endorsed by, or sponsored by Mattel. BARBIE is a trademark of Mattel, Inc.
DollScout publishes no price guides or valuations; listing prices belong to
the individual merchants.
"""

# Heading for the generated catalog section of /llms.txt.
LLMS_CATALOG_HEADING = "Canonical doll catalog (reference page where published, live search otherwise)"

# Relevance guard: keep a result only if its title or description names the
# brand. Distinct from QUERY_ANCHOR — the anchor keeps the *query* on-topic,
# these keep the *results* on-topic.
BRAND_TERMS = ("barbie", "mattel")

# Collab/licensed-merch brand markers: a result whose title or description
# names one of these is that brand's product wearing the Barbie license (vinyl
# figure, toy car, card game, footwear...), never a doll. Matched on word
# boundaries against normalized title+description and DROPPED from results
# entirely (unlike MATCH_NEGATIVE_TERMS below, which only suppresses the
# matched badge). Deliberately absent: "hallmark" (Hallmark sold real exclusive
# dolls — Victorian Elegance, Holiday Memories) and "swarovski" (legit collector
# dolls advertise Swarovski crystals in their outfits); their merch is caught by
# generic words like "ornament"/"figurine" at the badge layer instead. Sellers
# that stock only collab merch but never name the brand in listings (e.g. Funko
# shops titling a Pop "Holiday Barbie 1988") go in BANNED_SELLERS below.
EXCLUDE_BRAND_TERMS = (
    "funko",                                  # Pop! vinyl figures
    "hot wheels",                             # Barbie-livery cars
    "happy meal", "mcdonald", "mcdonalds",    # 90s McDonald's premiums
    "uno", "monopoly",                        # card/board games
    "mega bloks", "mega construx",            # brick sets
    "little people",                          # Fisher-Price figure sets
    "squishmallow", "squishmallows",          # plush
    "crocs", "vans", "puma",                  # footwear collabs
    "stanley",                                # tumbler collab
    "impala",                                 # roller skates
    "opi", "nyx",                             # cosmetics collabs
    "pez",                                    # dispensers
)

# Reference match index (see app.py "reference match index"): name words too
# generic to corroborate a stock-number match on their own — brand terms plus
# the hobby's nouns and filler that appear in nearly every listing title.
MATCH_STOPWORDS = ("barbie", "mattel", "doll", "dolls", "the", "a", "an", "and", "of")

# Titles containing any of these (word-boundary phrases) never get a matched
# badge: they're merchandise *about* a doll, not the doll a reference record
# describes (same merch classes as the POPULAR_QUERIES accuracy bar below).
MATCH_NEGATIVE_TERMS = ("ornament", "figurine", "music box", "advertisement", "print ad",
                        "poster", "magazine", "costume", "funko", "mug", "keychain",
                        "shirt", "plate", "pin", "sticker", "book")

# Merchant ban list: results from these sellers are hidden everywhere (search +
# quick-view). Each entry is a lowercase substring matched against the seller's
# myshopify domain, its custom-domain host, and its name — so "sell4value"
# catches sell4value.com, sell4value.myshopify.com, and the "SELL4VALUE"
# display name. Curate per deployment; empty tuple disables the filter.
# "pops of the galaxy" is a Funko specialist whose listings never say "Funko"
# (a Pop titled just "Holiday Barbie 1988"), so EXCLUDE_BRAND_TERMS can't catch it.
BANNED_SELLERS = ("sell4value", "pops of the galaxy")

# Sponsored-seller disclosure: any seller with a paid, affiliate, or other
# material relationship to this deployment MUST be listed here. Matching works
# exactly like BANNED_SELLERS (lowercase substring vs. domain, custom-domain
# host, and name). Matched results get a visible "Sponsored" label on the card
# and in quick view. Labeling is the ONLY effect — sponsorship never changes
# ranking or filtering. If you take money from a seller and don't list them
# here, you're deceiving your users. DollScout has no sponsors; empty tuple.
SPONSORED_SELLERS = ()

# Popular searches surfaced as indexable deep-links (/?q=...) so Google can
# crawl them. Also pre-warmed into the search cache at boot, and mirrored in
# index.html's POPULAR (brand block 6) for the try-bar + suggestion dropdown —
# keep the two lists in sync.
# ACCURACY BAR (2026-07-11): every entry here (and every PH_EXAMPLES string)
# must score >=9/10 relevant in the top 10 live results — marker text present
# AND not merch (Funko/ornament/costume/apparel). The fun "weird doll" list
# (Weird Barbie, Earring Magic Ken, Tanner...) was tried and failed this bar:
# thin inventory means costumes, Funkos, and generic backfill crowd the top.
# Anything that demos badly gets cut, no matter how good the joke is.
POPULAR_QUERIES = [
    "Ponytail Barbie", "Bubble Cut Barbie", "Enchanted Evening Barbie",
    "Twist N Turn Barbie", "Malibu Barbie 1971", "Day to Night Barbie",
    "Totally Hair Barbie", "Happy Holidays Barbie 1995", "Holiday Barbie",
    "Silkstone Fashion Model", "Birthday Wishes Barbie", "Bob Mackie Barbie",
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
        {"label": "1980s", "q": "1980s vintage"},  # bare "1980s" pulled 80th-Anniversary dolls (83%→96% with "vintage")
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
    ],
    # Removed 2026-07-11 after a per-chip accuracy sweep against live results
    # (fraction of returned cards actually matching the chip's promise):
    # - "Designer" group (Bob Mackie / Byron Lars / Robert Best / Carlyle Nuera):
    #   2-40% for everyone but Mackie — sellers rarely credit the designer in
    #   listing text, so the catalog backfills with generic dolls.
    # - "Playline" 0% — the term never appears in listings; the search reads it
    #   as "play" and returns playsets.
    # - "Barbie Looks" 46% — bleeds into adjacent Signature fashion lines;
    #   alternate query phrasings scored worse.
    # - "Dolls of the World" 68%, "OOAK" 41% (thin results) — moderate drift,
    #   no query fix found.
    "Type": [
        {"label": "Collector", "q": "collector"},
        {"label": "NRFB", "q": "NRFB new in box"},
    ],
}
