# Independent peak-sales gold dataset

This directory is a source of truth built from issuer SEC filings and investor
relations disclosures. It is not produced by the application extraction
pipeline, an LLM, or pipeline post-processing.

All 20 products in `seed/example_drugs.csv` have one explicit disposition:

- 12 have independently supported peak labels.
- 9 of those also have complete quarter-by-quarter sales from commercial start
  through 2026Q2, totaling 424 cited observations at 100% coverage.
- 6 labels are observed numeric peaks.
- 6 labels are `not_yet_observed` because the complete reported history is
  still growing or lacks enough post-peak years.
- 8 products are excluded with a cited reason because public reporting is
  aggregated, scope-incomparable, private, or incomplete before the possible
  peak. No sales are invented for them.

## Files

- `quarterly_revenue.jsonl`: complete independently reported quarterly series.
- `annual_revenue.jsonl`: issuer-reported annual peak series and partial context.
- `series_coverage.jsonl`: exact commercial-start-to-as-of coverage assertions.
- `peak_sales.jsonl`: independent observed/not-yet-observed peak labels.
- `excluded_products.jsonl`: evidence-backed benchmark exclusions.
- `source_manifests/`: researched source indexes and manually modeled
  direct/derived observations.
- `manifest.json`: benchmark boundaries and provenance.
- `build_report.json`: generated counts.
- `unresolved_quarters.jsonl`: intentionally empty. Missing public histories are
  exclusions, not fake quarter-level non-disclosures.

## Rebuild

```bash
cd backend
uv run python ../scripts/build_independent_gold.py
```

The builder imports no `app`, orchestrator, extraction, quality-filter, or LLM
code. It fails if any included quarterly series is incomplete.

Validate with:

```bash
cd backend
uv run pytest tests/test_gold_dataset.py
```
