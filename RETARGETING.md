# Retargeting awesome-ucp-demo to your hobby

`awesome-ucp-demo` ships configured as DollScout, a collector-Barbie finder. Everything
Barbie-specific lives in two places: `domain.py` (all of it) and six fenced
`BRAND BLOCK` regions in `index.html`. Work through this checklist in order and
you'll have your own finder running in an afternoon.

## 1. `domain.py`: the config seam

Every constant, with its DollScout value and what to change it to:

| Constant | DollScout value | Change to |
|---|---|---|
| `SITE_NAME` | `"DollScout"` | Your site name (startup banner) |
| `SITE_ORIGIN` | `https://www.dollscout.com` | Your canonical origin |
| `REDIRECT_HOSTS` | dollscout.fly.dev, dollscout.com | Hostnames that 301 to the canonical origin |
| `QUERY_ANCHOR` | `"Barbie"` | The word that keeps free-text queries on-topic. Prepended to any query that lacks it. Pick the term your niche always includes ("vinyl", "NES", "fountain pen") |
| `DEFAULT_QUERY` | `"Barbie collector doll"` | What an empty search box searches |
| `SEARCH_INTENT` | `"Barbie collector shopping"` | Sent as UCP `context.intent` |
| `DEFAULT_CONDITION` | `("secondhand",)` | Condition filter the UI starts with (`()` = Any). Keep in sync with `DEFAULT_CONDITIONS` in index.html — cache warming uses it |
| `BRAND_TERMS` | `("barbie", "mattel")` | Relevance guard: a result is kept only if its title or description contains one of these. Lowercase substrings |
| `EXCLUDE_BRAND_TERMS` | `("funko", "hot wheels", ...)` | Collab/licensed-merch brands whose products carry your niche's name but aren't the collectible (vinyl figures, apparel, games). Word-boundary matched vs title+description; matching results are dropped. Sellers that never name the brand go in `BANNED_SELLERS` instead |
| `BANNED_SELLERS` | `("sell4value",)` | Merchants to hide. Start empty: `()` |
| `SPONSORED_SELLERS` | `()` | Sellers with a paid/affiliate/material relationship to YOUR deployment. Their results get a visible "Sponsored" label (never a ranking change). Labeling paid relationships is mandatory, not optional |
| `POPULAR_QUERIES` | 12 Barbie searches | Your niche's popular searches (free text). Drives the sitemap, the suggestion dropdown seeds, and boot-time cache warming. Mirror the same list in `POPULAR` (block 6) |
| `DEEP_LINK_TITLE` / `DEEP_LINK_DESC` | Barbie phrasing | `<title>`/description templates for `/?q=` deep-links. Keep the `{q}` placeholder |
| `DEFAULT_META_TITLE` | DollScout og:title | See the coupling trap below |
| `NOT_FOUND_HEADING` / `NOT_FOUND_BODY` / `NOT_FOUND_CTA` | Hunt-themed 404 copy | Your niche's 404 page copy (app.py wraps these in a styled page) |
| `TAXONOMY` | Barbie chip vocabulary | Your niche's filter chips: groups of `{"label", "q"}` where `q` is appended to the query. Dict order = display order |
| `MATCH_STOPWORDS` | `("barbie", "mattel", "doll", ...)` | Only used with the optional reference match index (README §"Reference match index"): name words too generic to corroborate a stock-number match |
| `REFERENCE_PAGE_BASE` | `""` | Base path for matched-badge links to per-record reference pages; `""` keeps badges plain text |
| `LLMS_INTRO` / `LLMS_CATALOG_HEADING` | DollScout intro + disclaimer | Served at `/llms.txt`; the reference-catalog section is appended automatically when a match index is loaded |
| `MATCH_NEGATIVE_TERMS` | `("ornament", "figurine", "mug", ...)` | Titles containing these never get a matched badge — merchandise *about* an item, not the item. Also match-index only |

If you maintain a structured catalog of your niche's items, see the README's
"Reference match index" section — drop `master.json` / `stock-numbers.json` /
`aliases.json` into `data/` and results get a "matched: …" badge. No catalog,
nothing to do: the feature stays off.

## 2. `index.html`: six fenced brand blocks

Search the file for `BRAND BLOCK`. Each region is paired with a closing fence:

1. **SEO head**: `<title>`, meta description, OG/Twitter tags, JSON-LD (site name,
   description, publisher organization). Replace every DollScout/Barbie/Ethercycle value.
2. **Welcome mat + hero**: wordmark (`Doll<em>Scout</em>`), display headline (with the
   `.scribble` underline span), sub line, handwritten `.scout-note`, search placeholder,
   and the marquee `.ticker` one-liners (rewrite all six for your niche's in-jokes).
   The try-bar chips build themselves from `POPULAR` (block 6); only `LABEL_SURPRISE`
   (block 5) needs wording.
3. **About / What & Why**: the project story and trademark disclaimer. Rewrite for
   your niche and your operator.
4. **Footer**: wordmark, tagline, operator copyright, trademark note.
5. **JS brand constants**: `NOUN`/`NOUNS` (the item word used in every count, wishlist
   message, and aria-label: "doll"/"dolls" → "record"/"records"), the `MSG_*`/`LABEL_*`/
   `TITLE_*` voice strings (empty states, loading line, card links — DollScout's are
   she/her and hunt-flavored; rewrite for your niche's vocabulary), the `console.log`
   easter egg, and `WISHLIST_KEY` (namespace it to your site so localStorage doesn't
   collide).
6. **Suggestion + placeholder copy**: `POPULAR` (free-text searches, run verbatim; keep
   in sync with `POPULAR_QUERIES` in `domain.py` — the try-bar samples 4 of them at
   random per page load), `PH_EXAMPLES` (typewriter placeholder examples), `BASE_PH`.

One unfenced straggler, an intentional static fallback that JS overwrites at runtime;
change it to match your `NOUNS`:

- the mobile sheet apply button: `Show <span id="sheetcount">dolls</span>`

Theme: the `:root` CSS custom-property palette (in `riso.css`, shared by index and
the `/doll/` reference pages) and the
Google Fonts `<link>` (Fraunces + Archivo) are your visual levers. Not fenced because
they're taste, not brand strings.

## 3. The coupling trap (read this one)

`app.py` injects per-query social meta for `/?q=` deep-links by find-and-replacing the
og:title string. That means:

> `DEFAULT_META_TITLE` in `domain.py` must **exactly** match the `content` attribute of
> the `og:title` and `twitter:title` tags in `index.html`.

If you edit one and not the other, nothing errors: deep-link og:title just silently
stops updating. After any head edit, verify with:

```sh
PORT=8791 WARM_CACHE=0 python3 app.py &
curl -s "http://127.0.0.1:8791/?q=test" | grep 'og:title'
# expect: content="test — ..." (your DEEP_LINK_TITLE), not your default title
```

## 4. Replace the DollScout assets

These ship as DollScout's real files and MUST be replaced before you deploy:

- **`privacy.html` / `terms.html`**: DollScout's actual legal pages, naming its actual
  operator (Ethercycle LLC). Rewrite them for your operator and brand, or delete the
  `/privacy` and `/terms` routes in `app.py` and the footer links in `index.html`.
- **`og-image.png`**: 1200×630 social share card.
- **`screenshots/`**: DollScout UI captures used by the README. Retake them from your
  own deployment (or delete the folder and the README image tags).

## 5. `fly.toml`

Change `app = 'dollscout'` to your Fly app name, or delete `fly.toml` and re-run
`fly launch`. Then update `SITE_ORIGIN`/`REDIRECT_HOSTS` in `domain.py` (step 1)
to match.

## 6. Final sweep

Nothing brand-specific should survive outside your own copy:

```sh
grep -niE 'doll|barbie|mattel|ethercycle|dollscout' app.py domain.py index.html
```

After a full retarget the only acceptable hit is `app.py`'s module docstring, which
describes what the template ships as. Everything else should be your own copy.
