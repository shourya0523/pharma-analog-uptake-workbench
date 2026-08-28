# Gold validation dataset

Ground truth for the 20 products in `seed/example_drugs.csv`.

Coverage is **per-drug full commercial life**: every calendar quarter from FDA
approval through the latest completed quarter (`as_of_quarter` in
`manifest.json`). Analog peak sales cannot be observed from a global recent-year
window; a peak requires the complete comparable quarter grid.

The previous 2022–2026 window lives in `archive/window-2022-2026/`.

## Files

- `lifecycle.jsonl` — per-drug approval date, expected quarter span, coverage
- `quarterly_revenue.jsonl` — cited product-quarter values
- `unresolved_quarters.jsonl` — explicit non-disclosures for every remaining expected quarter (not zero revenue)
- `peak_sales.jsonl` — selected peak from production `select_peak_from_observations`; `insufficient_lifecycle_history` when the comparable annual series is too short
- `edge_cases.jsonl` — generic/dose false positives and cross-currency history without cited FX
- `manifest.json` — `coverage_mode=full_lifecycle`, as-of quarter
- `metadata.jsonl` — parser/identity fixtures
- `build_report.json` / `audit_report.json`

## Regenerate

Restructure the quarter grid (OpenFDA approval dates + existing cited rows):

```bash
cd backend
uv run python ../scripts/rebuild_gold_lifecycle.py
```

New web-search extraction over the full life, then rebuild peaks/coverage:

```bash
cd backend
uv run python ../scripts/build_gold_web_search.py \
  --manual-revenue ../seed/gold/archive/window-2022-2026/quarterly_revenue.jsonl \
  --manual-unresolved ../seed/gold/archive/window-2022-2026/unresolved_quarters.jsonl
uv run python ../scripts/rebuild_gold_lifecycle.py
```

Run `cd backend && uv run pytest tests/test_gold_dataset.py tests/test_lifecycle.py` to validate.
