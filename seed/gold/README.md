# Gold validation dataset

Ground truth for the 20 products in `seed/example_drugs.csv`, constrained to 2022–2026.

The current set is built from the **OpenRouter web-search pipeline** (SEC earnings exhibits + LLM search fallback), with manual rows retained when the pipeline does not recover a period.

## Files

- `quarterly_revenue.jsonl` — product-quarter values with citations
- `unresolved_quarters.jsonl` — quarters with explicit non-disclosure (not zero revenue)
- `edge_cases.jsonl` — out-of-window records and generic/dose false positives
- `manifest.json` — drug count and calendar window for tests
- `build_report.json` — last pipeline build summary (when regenerated)
- `archive/manual-2026-08-24/` — prior manually researched gold (2026-08-24)

## Regenerate

Requires `OPENROUTER_API_KEY` in `backend/.env`.

```bash
cd backend
uv run python ../scripts/build_gold_web_search.py \
  --manual-revenue ../seed/gold/archive/manual-2026-08-24/quarterly_revenue.jsonl \
  --manual-unresolved ../seed/gold/archive/manual-2026-08-24/unresolved_quarters.jsonl
```

This runs each seed drug through `PipelineOrchestrator` with:

- `earnings_releases` bounded to the manifest window (2022–2026)
- OpenRouter `web_search` / `web_fetch` for CIK resolution and revenue fallback
- Table extraction + evidence judge on retrieved SEC earnings exhibits

Accepted pipeline rows must pass quote/value grounding and per-drug quality checks. Missing manual periods are backfilled from the archived manual gold.

## Method

1. Retrieve issuer SEC earnings exhibits (8-K item 2.02 ex 99.x) and LLM search hits.
2. Extract product-named quarterly values via table parser + LLM span extraction.
3. Accept only rows with verbatim quotes containing the reported value.
4. Deduplicate by drug / period / scope / geography / formulation.
5. Mark unresolved when the issuer aggregates or omits the product.
6. Backfill verified manual rows the pipeline did not recover.

Run `cd backend && uv run pytest tests/test_gold_dataset.py` to validate JSONL integrity.
