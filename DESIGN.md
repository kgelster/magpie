# DollScout Design System — Riso Rebrand

Design intent in one sentence: a playful two-color risograph print — everything sits on
one warm paper sheet, pink is the only thing you can touch, and blue does the talking
(deep) and the decorating (light).

Conflict order, always: accessibility > token consistency > aesthetics.

## Context and goals

Applies to the DollScout deployment of Magpie (`index.html`, the `app.py` 404 page,
and the `/doll/` reference pages — see `DESIGN-REFERENCE-PAGES.md` for that surface).
The 2026-07 retheme replaced the porcelain/gold/serif look. Implementation strategy:
the new tokens are mapped onto the legacy CSS variable slots in `:root` (now in
`riso.css`), and component corrections live in the fenced `RISO RETHEME OVERRIDES`
block at the end of `<style>`. Change colors in `riso.css`; change component behavior
in the override block; never scatter hex values into individual rules.

## Tokens and foundations

Single source of truth is `:root` in `riso.css` (served at `/riso.css`, linked by
`index.html` and the `/doll/` reference pages — extracted 2026-07-11 so the new pages
share one hex source). Legacy slot → spec token:

| Legacy var | Spec token | Value | Role |
|---|---|---|---|
| `--porcelain` | `paper` | `#EDE2E3` | Universal page/section background |
| `--paper` | `surface-raised` | `#FFFFFF` | Cards, inputs, modals ONLY — never a page bg |
| `--ink` | `ink` | `#111827` | Body text, icons, focus rings |
| `--ink-soft` | *(added)* | `#4B4A55` | Muted text. Added because the spec had no muted tier; 6.9:1 on paper (AA) |
| `--pink` | `primary` | `#F237A1` | Interaction FILLS only |
| `--pink-deep`, `--bronze` | `secondary` | `#2C40A7` | Headings, link text, the readable blue |
| `--gold`, `--tint` | `secondary-tint` | `#6DC6EC` | Decorative only: print-shadows, hairline accents |
| `--line` | *(added)* | `rgba(17,24,39,.15)` | Decorative hairlines/borders. Borders that convey meaning use `--ink` |
| `--success/warning/danger` | status | `#16A34A / #D97706 / #DC2626` | Functional status only, never decorative |

Fonts: `--sans`/`--serif`/`--script` all resolve to Poppins (300–700; was Space Grotesk until 2026-07-11);
`--mono` is Overpass Mono (label-caps surfaces). Radius: 8px cards/modals/buttons,
4px inputs/tags/pills.

Measured contrast (WCAG relative-luminance formula, verified by script 2026-07-02):

- ink on paper **14.0:1**, secondary on paper **6.9:1**, ink-soft on paper **6.9:1** — all pass AA at any size.
- primary on paper **2.8:1** — fails text AND the 3:1 non-text minimum. Pink only as large fills.
- ink on primary **4.9:1** — the required text color on pink fills. White on primary **3.6:1** — large text only (≥24px, or ≥19px bold).
- tint on paper **1.5:1** — decoration only, carries zero information.
- Status colors on paper: success **2.6**, warning **2.5**, danger **3.8** — none pass as text on paper. Status = filled chip with ink text (ink on warning 5.6, on success 5.4), or icon + ink label. Never bare colored text.

## Component rules

**Buttons (primary action: Search, Buy now, Show dolls, price Apply)**
Pink fill, ink text, mono caps, 8px radius (4px for the inline price Apply). Hover:
ink fill, white text. Focus-visible: 2px ink outline, 2px offset. Disabled: paper fill,
ink-soft text, line border. Long labels truncate never — button grows; min touch target 24×24px.

**Secondary buttons / pills (facets, In Stock, Auto-load, Saved)**
White surface fill, ink mono caps, line border, 4px radius. Selected state = pink fill +
ink text + ink border (never pink or white small text). The facet count badge is a pink
fill with ink text.

**Links (View, More like her, footer, about)**
Secondary text with primary underline (`border-bottom` or `text-decoration-color`).
Hover deepens underline/fill; text never drops below 4.5:1 at rest or on hover.
Never bare pink text on paper.

**Headings**
One h1 per page (the wordmark), levels never skip. h1/h2 secondary 700; h3 secondary 500.
Signature print-shadow: `text-shadow: 3px 3px 0 var(--tint)`, no blur, readable layer
is always the dark one. Print-shadow belongs to h1/h2-scale display text only — at body
sizes it smears.

**Cards**
White surface on paper, 8px radius, `overflow: hidden`, line border. Seller name =
mono caps ink-soft; title = body 16px ink (linked: plain ink, underline on hover);
price = secondary; stars = ink-soft (meaningful, so never tint). Hover lift + tint
hairline accent are decorative and disabled under `prefers-reduced-motion`.
Empty grid state uses the voice strings (see Content). Overflowing titles wrap, never clip.

**Search + suggest**
Input: white surface, ink text, 4px radius, ink border, secondary focus border +
2px ink outline. Placeholder = ink-soft (6.9:1; the typewriter animation already
respects reduced-motion). Suggest panel: white surface, tint hairline border
(decorative), matches highlighted in secondary 600.

**Sticky controls bar**
Paper background with line border-bottom. It is chrome, not a CTA — no pink field.
Labels ink mono caps.

**Quick-view modal / mobile sheet**
White surface, 8px radius, scrim `rgba(17,24,39,.55)`. Focus trapped, Esc closes,
focus returns to trigger (already implemented — keep under test). Sheet Apply is a
primary button.

**Wish heart**
Resting: white chip, ink-soft glyph (3:1+ against its chip). Saved: pink chip, ink
glyph, `aria-pressed="true"`. One heartbeat animation on save, none under
reduced-motion. Target ≥24×24 (chip is 34px).

**404 page (`app.py NOT_FOUND_HTML`)**
Paper bg, mono 404 eyebrow, secondary h1 with tint print-shadow, ink-soft body,
primary-fill CTA with ink text. Copy from `domain.NOT_FOUND_*`.

## Accessibility acceptance criteria (testable)

- Contrast (1.4.3/1.4.11): every text pair ≥4.5:1 (≥3:1 large), every meaningful
  non-text element ≥3:1. Test: axe DevTools or the contrast script in repo history;
  re-run whenever `riso.css` changes.
- Focus (2.4.7): tab through hero → controls → cards → modal; every stop shows the
  2px ink outline. Test: keyboard-only pass, no pointer.
- Keyboard (2.1.1/2.1.2): suggest list arrows/Enter/Esc; facet panels open/close;
  modal and sheet trap and release focus. Test: complete a search → filter → save →
  quick view → buy-link flow with keyboard only.
- Target size (2.5.8): all controls ≥24×24. Test: inspect computed size of heart,
  clear-x, thumbs.
- Reduced motion: with `prefers-reduced-motion: reduce`, no typewriter, no reveal,
  no heartbeat, no shimmer. Test: toggle in OS or DevTools rendering panel.
- State not by color alone (1.4.1): selected chips/pills also change border (ink) and
  `aria-pressed`; saved heart changes glyph container, not just hue.

## Content and tone standards

Playful level 2, hunt vocabulary, the doll is "she." Voice strings live in
BRAND BLOCK 5/6 (`MSG_*`, `LABEL_*`, `TITLE_*`) and `domain.NOT_FOUND_*`.

- Empty results: "She's out there. Just not under that name. …" — payoff of the tagline.
- Actionable errors stay plain (timeout, rate-limit, network). No jokes where the user
  is frustrated. No "Click here"/"More" — labels name the action ("Show 48 dolls",
  "Back to the hunt").
- Mono caps for SKU-flavored metadata (seller, counts, labels); never for body copy.
- aria-labels stay literal and descriptive even where visible copy is playful.

## Anti-patterns (prohibited)

- Pink text on paper, pink small icons, pink hairlines. Pink is a fill or it is absent.
- White or tint text below 24px/19px-bold on pink fills (3.6:1 and worse).
- Tint (`#6DC6EC`) as text, icon, or meaning-bearing border anywhere.
- White as a page/section background; white is reserved for raised components.
- Status colors as decoration, or as bare text on paper (all fail contrast).
- New hex literals in component rules — tokens only.
- Off-scale spacing/type in NEW work (see migration note for existing).
- Print-shadow on body text or with blur.

## Migration notes / accepted debt

- Legacy px values (11/13/15px labels, 9/13/22px paddings) predate the scale and are
  grandfathered; normalize opportunistically, never introduce new off-scale values.
- Label-caps renders at weight 700, not the spec's 500 — Overpass Mono 500 was hard to
  read at 10–12px caps (Kurt, 2026-07-02); readability beats the token here.
- The hero is search-first (Kurt, 2026-07-02): full-width masthead card, 24px wordmark
  label, display headline "If she's out there, we'll find her." (clamp 32–56px, tint
  SVG scribble under the promise), Caveat handwritten aside in secondary, oversized
  full-width input, try-chips + surprise-me dice built from `POPULAR`. Punch-up round
  (same day, energy from weird.shopping) also added the mono-caps marquee ticker
  (tint asterisk separators, ink hairlines, static under reduced-motion; currently
  hidden via `display:none` with markup kept for re-enable) and the "located"
  count verb. Caveat is annotation-only: never body text, never labels.
- The hero band is solid `--pink` (Kurt, 2026-07-02) — the one sanctioned decorative
  use of primary, superseding both the photo and the flat-paper hero. All reading
  contrast lives on the white masthead card; nothing readable sits directly on the pink.
  `hero-bg.jpg` is unused; remove from the repo and Dockerfile whenever convenient.
- The hero Search button carries a 1px ink border and a 16px label (Kurt, 2026-07-02);
  other primary buttons keep the mono 12px label.
- The 404 page doesn't load webfonts (kept dependency-free) — Poppins falls back
  to system sans there.
- `secondary-tint` on the count-spinner ring would fail 3:1; spinner uses secondary. 

## QA checklist (code review)

- [ ] No new raw hex outside `riso.css` / the 404 style block / the `theme-color` meta.
- [ ] Any pink background has ink text (or ≥24px/19px-bold white) — grep `--pink` uses.
- [ ] No `--tint`/`--gold` on text, icons, or meaning-bearing borders.
- [ ] New paddings/font-sizes on the scale (4/8/12/16/24/32; 12/14/16/20/24/32).
- [ ] Interactive additions define default, hover, focus-visible, active, disabled.
- [ ] Focus outline is 2px ink 2px offset (pink glow allowed only as decoration).
- [ ] Keyboard pass: reachable, operable, no trap, Esc closes overlays.
- [ ] Reduced-motion pass: no new unguarded animation.
- [ ] Voice strings routed through BRAND BLOCK 5/6 / `domain.py`, not inlined.
- [ ] axe or contrast-script run on any changed color pair; number recorded in the PR.
