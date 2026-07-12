#!/usr/bin/env python3
"""Calibrate the focused-view fallback ladder against REAL prod grab-bags.

The focused view (auto-narrow + close/all fallback) lives in app.py: `_sig_score`,
`_focus_eval`, `_focus_ladder`, and the EXACT_CUTOFF / CLOSE_CUTOFF / MIN_KEEP
constants. Tuning those blind is how precision regressions ship. This tool pulls
the unfiltered `?all=1` grab-bag for a battery of real queries from live
dollscout.com (once, cached), then runs the ACTUAL prod ladder (imported from
app.py) over those cards so you can eyeball kept/close/dropped per query and, more
importantly, fail the build on a precision regression.

Dev tool only — NOT copied into the Docker image (see Dockerfile). Stdlib only.

    python3 tools/calibrate_focus.py            # use cached grab-bags (fast)
    python3 tools/calibrate_focus.py --refresh  # re-pull from prod (1 req/sec)

Exit non-zero if any regression assert fails.
"""

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.parse
import urllib.request

# Import the real engine (app.py, one dir up). Importing it loads the matcher
# index from data/ and prints a couple of matcher/pages lines to stderr — that's
# the same code path prod runs, which is the point.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import app  # noqa: E402

PROD = "https://www.dollscout.com/api/search"
CACHE_DIR = os.path.join(tempfile.gettempdir(), "dollscout_calibrate")

# The battery: the 12 queries probed on prod during the b4b8b8a auto-narrow work,
# plus a typo case and two "n"/"to" filler-word names. Every one either
# auto-focuses today or is a known edge (empty focus, near-miss drop, false exact).
BATTERY = [
    "Totally Hair Barbie",
    "Malibu Barbie 1971",
    "Happy Holidays Barbie 1988",
    "Crystal Barbie",
    "Day to Night Barbie",
    "Great Shape Barbie",
    "Twist N Turn Barbie",
    "Superstar Barbie 1977",
    "Holiday Barbie 1996",
    "Bob Mackie Gold Barbie",
    "Solo in the Spotlight Barbie",
    "Dream Glow Barbie",
    "Totaly Hair Barbie",          # typo — fuzz must still resolve + keep
    "Peaches n Cream Barbie",
    "Dance Magic Barbie",
]


def _cache_path(query):
    safe = urllib.parse.quote(query, safe="")
    return os.path.join(CACHE_DIR, f"{safe}.json")


def fetch(query, refresh=False):
    """Cached prod grab-bag (?all=1) for a query. Pulls once, then reads cache."""
    path = _cache_path(query)
    if not refresh and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    url = f"{PROD}?query={urllib.parse.quote(query)}&all=1"
    req = urllib.request.Request(url, headers={"User-Agent": "dollscout-calibrate/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    time.sleep(1.0)                             # be gentle: 1 req/sec
    return data


def _norm(title):
    return app._match_norm(title)


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-pull grab-bags from prod")
    args = ap.parse_args()

    print(f"cutoffs: EXACT={app.EXACT_CUTOFF} CLOSE={app.CLOSE_CUTOFF} MIN_KEEP={app.MIN_KEEP}")
    print(f"cache:   {CACHE_DIR}\n")

    failures = []
    # Collect per-query outcomes for the asserts after the eyeball table.
    outcomes = {}

    for q in BATTERY:
        try:
            data = fetch(q, refresh=args.refresh)
        except Exception as e:                  # noqa: BLE001 — dev tool, surface + skip
            print(f"! {q}: fetch failed: {e}")
            failures.append(f"{q}: fetch failed ({e})")
            continue
        cards = data.get("cards") or []
        intent = app.resolve_query_intent(q)
        if not intent:
            print(f"· {q}: no auto-focus (grab-bag {len(cards)} cards) — not narrowed")
            outcomes[q] = {"intent": None, "cards": cards, "kept": None, "mode": "none"}
            continue
        rid = intent["id"]
        kept, mode, exact_n = app._focus_ladder(cards, rid)
        outcomes[q] = {"intent": intent, "cards": cards, "kept": kept, "mode": mode}

        if mode == "all":
            print(f"  {q} -> {intent['label']}: mode=ALL (0 close) — grab-bag "
                  f"{len(cards)} shown")
            continue
        print(f"  {q} -> {intent['label']}: mode={mode.upper()} "
              f"kept={len(kept)} (exact={exact_n}) of {len(cards)} grab-bag")
        for c in kept[:4]:
            s = app._focus_eval(c["title"], c.get("desc", ""), rid)
            print(f"      {s:.2f}  {c['title'][:70]}")

    print("\n--- regression asserts ---")

    def kept_titles(q):
        o = outcomes.get(q, {})
        kept = o.get("kept")
        if kept is None:                        # mode all/none: grab-bag not narrowed
            return []
        return [c["title"] for c in kept]

    def assert_absent(q, needles, label):
        titles = kept_titles(q)
        bad = [t for t in titles
               if any(n in f" {_norm(t)} " for n in needles)]
        if bad:
            failures.append(f"{q}: {label} leaked into focused view: {bad[:3]}")
            print(f"  FAIL {q}: {label} present -> {bad[:3]}")
        else:
            print(f"  ok   {q}: {label} absent ({len(titles)} kept)")

    # 1. Totally Hair: Ken + merch (pop/keychain/tumbler/costume) + styling head out.
    assert_absent("Totally Hair Barbie",
                  [" ken ", " pop ", " keychain ", " tumbler ", " costume ", " styling head "],
                  "Ken/merch/styling-head")

    # 2. Malibu 1971: Malibu Ken out, and every kept title carries 1971 (no cross-year).
    assert_absent("Malibu Barbie 1971", [" ken "], "Malibu Ken")
    mal = kept_titles("Malibu Barbie 1971")
    cross = [t for t in mal if "1971" not in _norm(t)]
    if cross:
        failures.append(f"Malibu Barbie 1971: cross-year reissue kept: {cross[:3]}")
        print(f"  FAIL Malibu Barbie 1971: cross-year kept -> {cross[:3]}")
    elif mal:
        print(f"  ok   Malibu Barbie 1971: all {len(mal)} kept carry 1971")
    else:
        print("  ok   Malibu Barbie 1971: (no exact keepers to check for cross-year)")

    # 3. No auto-focus query ends with zero renderable results.
    for q, o in outcomes.items():
        if not o.get("intent"):
            continue
        if o["mode"] == "all":
            renderable = len(o["cards"])
        else:
            renderable = len(o["kept"] or [])
        if renderable == 0:
            failures.append(f"{q}: auto-focus rendered ZERO results (mode={o['mode']})")
            print(f"  FAIL {q}: zero renderable results (mode={o['mode']})")

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all regression asserts passed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
