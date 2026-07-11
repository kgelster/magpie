# DollScout Reference Pages — Design Spec (pilot, ~30 dolls)

Design intent in one sentence: each doll gets a typographic collector record on the same
riso paper — the facts do the posing, since the database is imageless by design.

Inherits `DESIGN.md` wholesale: tokens, contrast math, focus rules, voice, anti-patterns,
QA. This doc adds only what the new surface needs. Token values are NOT restated here;
`:root` in `index.html` stays the single source of truth (server-rendered pages read the
same values from a shared stylesheet or an extracted `:root` block — see Foundations).
Conflict order, always: accessibility > token consistency > aesthetics.

## Context and goals

- New public surface: server-rendered routes (proposed `/doll/<slug>`) built from
  `data/master.json` at boot, enriched records only (`lifecycle >= enriched`). Pilot ~30.
- Two jobs: (1) AEO/citation target — a machine-quotable, provenance-backed fact page;
  (2) landing surface for the matched-listing badges already plumbed in the app
  (`domain.REFERENCE_PAGE_BASE`, currently `""`).
- Hard content constraints carried from the DB: **no doll images** (imageless schema),
  **no value-talk** (worth/value/rare), `msrp_original` framed strictly as historical
  release price, Class A year rendered on conflicts, Mattel non-affiliation disclaimer,
  **no FAQPage JSON-LD** (FAQ renders as visible content only).

## Foundations (deltas only)

- **Stylesheet extraction — as built (2026-07-11)**: `riso.css` (repo root, served at
  `/riso.css`) holds the `:root` tokens ONLY; `index.html` and the doll template both
  link it, so hex has exactly one source. Shared *component* rules were NOT extracted:
  index.html's card/button system is interleaved with its override block and JS, and
  pulling it apart risked regressing the live app for a pilot. The doll template
  defines its own compact component rules referencing `var(--*)` (zero hex — QA-gated).
  Full component extraction is accepted debt, revisit if a third surface appears.
- **New token needed — none.** The surface uses existing tokens only. Explicitly: no new
  hex, no new type sizes, no off-scale spacing.
- **h1 ownership deviation**: on `index.html` the wordmark is the h1. On reference pages
  the **doll name is the h1**; the wordmark becomes a linked logo image/text in the
  header (`aria-label="DollScout home"`). One h1 per page holds.

## Page anatomy (top to bottom)

1. **Site header (chrome, minimal)**: paper bg, line border-bottom. Wordmark (secondary,
   700, 20px, tint print-shadow 2px) linking to `/`. Right side: one link "Search live
   listings" (link style per DESIGN.md). No sticky behavior — the page is a document.
2. **Breadcrumb**: mono label-caps, ink-soft, separators `›` in ink-soft.
   `DollScout › {line} › {name}`. The line crumb links to the line's search
   (`/?q={line}`) until line hub pages exist. Current page is text, not a link,
   `aria-current="page"`. Mirrors the BreadcrumbList JSON-LD exactly.
3. **Record header**:
   - Eyebrow: mono label-caps ink: `{year_released} · #{stock_number}` (Class A year
     always; conflicting B years never render).
   - h1: doll name, secondary 700 32px, signature print-shadow (3px tint, no blur).
   - Subline (body-md ink): `{line} — designed by {designer}` when designer exists;
     otherwise line alone.
   - Decorative backdrop: the stock number oversized (~120-160px, tint, mono, 300)
     bleeding off the right edge behind the header block, `aria-hidden="true"`,
     `user-select: none`. This is the riso "hero" replacing the photo we don't have.
     It is decoration: 1.5:1 is fine, it must remain unreadable-optional.
4. **Provenance strip**: mono label-caps ink on a white chip row (4px radius, line
   border): `VERIFIED · {n} SOURCES · CHECKED {mon year}` for lifecycle=verified/
   enriched records with 2-class verification; `SINGLE-SOURCE RECORD` otherwise. Ink
   text only — verification is information, so it never uses tint, and it is not a
   status color (nothing succeeded or failed; it's a fact about the record).
5. **Fact table — "Collector record"** (h2): definition list styled as the record card.
   White surface card on paper, 8px radius, line border, rows separated by line
   hairlines. Term = mono label-caps ink-soft, min-width 160px; value = body-md ink.
   Row order (omit any row whose field is null/empty — never render "N/A" or
   "Not documented"):
   `Year` (Class A), `Stock number` (+ `Other SKUs` inline when present), `Line`,
   `Designer`, `Label / edition`, `Edition size` (formatted `5,800 pieces`),
   `Body type`, `Character`, `Segment`, `Hair` (`{color}, {style}`), `Outfit`,
   `Accessories` (comma list, wraps), `Original retail price` (value verbatim
   `$74.95` + suffix ` (at release)` — the historical framing is part of the row,
   not a footnote).
6. **Prose**: `content.description` under no heading (it opens the record — first
   paragraph doubles as the quotable summary), then h2 "Collector notes" over
   `content.collector_notes_prose`. Body-md, max-width ~68ch for measure.
7. **FAQ** (h2 "Questions collectors ask"): flat rendering — each `q` an h3 (secondary
   500 20px), each `a` body-md. No disclosure widgets (nothing to mis-handle on
   keyboard, and answers stay visible for citation). **No FAQPage markup** — the
   JSON-LD emitter must not grow one later; note it in code next to the emitter.
8. **Live listings** (h2 "Live listings for this doll") — **as built**: simplified
   cards (image, sponsored disclosure badge, seller mono caps, linked title, price)
   following the same DESIGN.md rules, but WITHOUT wish heart and quick view — those
   drag in the app's wishlist/modal JS, deferred with the component extraction above.
   Cards are fetched client-side from `/api/search?query={name}` and filtered to
   `matched_page === location.pathname` (only listings the matcher pinned to THIS
   record render). The server-rendered page is complete without JS (progressive
   enhancement); the section reserves min-height for one card row during load and
   collapses it on empty/error.
   Empty state: voice string "None on the market right now. She'll turn up." + link
   "Watch the live search" to `/?q={name}`. Loading: existing shimmer, guarded by
   `prefers-reduced-motion`. Error: plain actionable copy per DESIGN.md (no jokes).
9. **Line rail** (h2 "Collect the line", only when siblings exist): reuse the
   quick-view year-button rail — white pills, mono caps, 4px radius, linking to
   sibling reference pages. Current doll's pill: pink fill + ink text + ink border +
   `aria-current="page"`.
10. **Disclaimer + footer**: body-sm ink-soft on paper:
    "DollScout is an independent collector reference and is not affiliated with,
    endorsed by, or sponsored by Mattel, Inc. Barbie is a trademark of Mattel, Inc."
    Then the standard site footer links (About, Privacy, Terms) in link style.

## JSON-LD

- One `<script type="application/ld+json">` block: `BreadcrumbList` (3 items, matching
  the visible crumb) + `ItemPage` (`name` = seo.title, `description` =
  seo.meta_description, `dateModified` = last verification date, `isPartOf` the site).
- **Prohibited in markup**: FAQPage; Product `offers` (live listings are transient and
  third-party); any price property (msrp is prose/fact-table content, not structured
  data); AggregateRating (we have none).

## States, responsive, edge cases

- Interactive elements on this page are: header links, breadcrumb link, listing cards,
  wish hearts, line-rail pills, footer links. Every one defines default / hover /
  focus-visible (2px ink outline, 2px offset) / active; cards and pills also disabled
  isn't applicable (they're links — absent when target absent).
- ≤640px: fact-table terms stack above values (dl rows become two-line), the decorative
  stock number drops to ~80px and moves behind the eyebrow, line rail scrolls
  horizontally with visible scrollbar (no fade-mask hiding overflow — 1.4.10 reflow,
  test at 320px wide with no horizontal body scroll).
- Long doll names (up to ~60 chars exist): h1 wraps, never truncates; breadcrumb
  truncates the middle crumb with `text-overflow: ellipsis` + full name in `title`.
- Records missing designer/edition/hair/etc.: rows omitted (anatomy §5); a record with
  only 4 facts still renders a coherent card.
- Unenriched or unverified slugs: **no route** (404 with the existing riso 404 page),
  never a thin stub page. Sitemap and llms.txt list exactly the routed set.

## Accessibility acceptance criteria (testable)

- 1.4.3/1.4.11: all new pairs are existing measured pairs (ink/paper 14.0, secondary/
  paper 6.9, ink-soft/paper 6.9, ink-on-white ≥14). Test: run the repo contrast script
  over the template's computed pairs; record numbers in the PR.
- 1.3.1: fact table is a real `<dl>` with `<dt>/<dd>`; headings h1→h2→h3 never skip.
  Test: axe "definition list" + "heading order" rules, zero violations.
- 2.4.7/2.1.1: keyboard-only pass — header → breadcrumb → listings → hearts → rail →
  footer, every stop shows the ink outline, Esc not required (no overlays on this page).
- 2.5.8: hearts and rail pills computed ≥24×24. Test: DevTools box inspect.
- 1.4.10: 320px-wide viewport shows no horizontal scroll except inside the rail.
- 4.1.2: decorative stock number `aria-hidden`; provenance chip is text, not an
  ARIA status. Test: VoiceOver rotor reads header → crumb → h1 with no phantom "R4484".
- Reduced motion: only listing shimmer + card hover lift exist here; both already
  guarded — verify with DevTools emulation.

## Content and tone standards

- Record prose renders **verbatim from `content.*`** — no template-side paraphrase,
  no injected adjectives. The page chrome may be playful level 2; the record is neutral.
- Banned tokens in template strings (mirror the DB guard): worth, value, valuable,
  rare, investment. Test: grep the template.
- Section headings are fixed strings routed through `domain.py` (like `NOT_FOUND_*`),
  not inlined: `REF_H_FACTS`, `REF_H_NOTES`, `REF_H_FAQ`, `REF_H_LISTINGS`,
  `REF_H_LINE`, `REF_EMPTY_LISTINGS`, `REF_DISCLAIMER`.
- Labels name destinations: "Search live listings", "Watch the live search",
  never "Click here"/"More".

## Anti-patterns (additions to DESIGN.md's list)

- A marketplace listing photo promoted to page hero or og:image (transient, not ours,
  breaks the imageless contract). og:image stays the site card.
- FAQ as `<details>` accordions (hides citable content) or as FAQPage JSON-LD.
- "N/A" / em-dash filler rows in the fact table — omission is the empty state.
- Rendering a Class B year anywhere, including meta/JSON-LD, on conflicted records.
- Provenance chip styled as success-green (status colors are for operations, not facts).
- Routes for unenriched records "to have more pages" — thin pages poison the AEO bet.

## Migration / activation notes

- Ship order: template + routes → sitemap gains `/doll/<slug>` for the pilot set →
  llms.txt gains the same list → **flip `domain.REFERENCE_PAGE_BASE` to `/doll/`
  last** (badges across the app start linking the moment it's non-empty).
- The `:root`→`riso.css` extraction (Foundations) lands with this feature; index.html
  keeps working unchanged during it.
- seo.title already contains " | DollScout" — template must not append the suffix twice.

## QA checklist (code review)

- [ ] DESIGN.md checklist passes in full (tokens, hex, states, focus, motion, voice).
- [ ] One h1 = doll name; wordmark demoted to link; heading order axe-clean.
- [ ] Fact table omits null rows; renders `<dl>`; Class A year everywhere.
- [ ] JSON-LD: BreadcrumbList + ItemPage only — grep template for `FAQPage|offers|price`.
- [ ] Decorative stock number `aria-hidden` and absent from screen-reader walk.
- [ ] Disclaimer string present and verbatim from `domain.py`.
- [ ] 404 for non-enriched slugs; sitemap/llms.txt match the routed set exactly.
- [ ] `REFERENCE_PAGE_BASE` flipped only in the final commit of the stack.
- [ ] Grep template for banned value-talk tokens: zero hits.
- [ ] Lighthouse SEO + axe pass on one long-name record, one sparse record, one
      conflicted-year record (d_04152c9451 has the 2003/2004 giftset adjacency).
