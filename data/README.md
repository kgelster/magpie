# Reference match index (optional)

Drop `master.json`, `stock-numbers.json`, and `aliases.json` here (shapes documented in
RETARGETING.md) and app.py tags search results with a "matched: …" badge at boot.
The JSON files are gitignored — they're generated from a separate, private database.
Without them the feature is silently off.
