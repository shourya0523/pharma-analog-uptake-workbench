# Gold validation dataset

Independent source of truth for the 20 products in `seed/example_drugs.csv`.

Gold is **not** pipeline output. Rows are read from issuer SEC exhibits and IR
sales schedules, then modeled here. The extraction pipeline is built and scored
against this set. `scripts/build_gold_web_search.py` evaluates the pipeline and
refuses to write into `seed/gold`.

Coverage is **per-drug full commercial life**: every calendar quarter from FDA
approval through the latest completed quarter (`as_of_quarter` in
`manifest.json`). Analog peak sales cannot be observed from a recent-year
slice; a peak needs the complete comparable quarter grid.

The previous 2022–2026 window lives in `archive/window-2022-2026/`.

## Files

- `lifecycle.jsonl` — per-drug approval date, expected quarter span, coverage
- `quarterly_revenue.jsonl` — cited product-quarter values
- `unresolved_quarters.jsonl` — explicit non-disclosures for every remaining expected quarter (not zero revenue)
- `peak_sales.jsonl` — selected peak from production `select_peak_from_observations`; `insufficient_lifecycle_history` when the comparable annual series is too short
- `edge_cases.jsonl` — generic/dose false positives and cross-currency history without cited FX
- `manifest.json` — `coverage_mode=full_lifecycle`, as-of quarter, `generation=independent_filing_research`
- `metadata.jsonl` — parser/identity fixtures
- `build_report.json`

## Research (writes gold)

Fetch issuer filings/IR and assemble lifecycle + peaks:

```bash
cd backend
uv run python ../scripts/research_gold_from_filings.py
```

## Pipeline eval (does not write gold)

```bash
cd backend
uv run python ../scripts/build_gold_web_search.py --out-dir /tmp/pipeline-eval
```

Run `cd backend && uv run pytest tests/test_gold_dataset.py tests/test_lifecycle.py` to validate.
