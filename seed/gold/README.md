# Gold validation dataset

Ground truth for the 20 products in `seed/example_drugs.csv`, constrained to 2022–2026.

Built from the OpenRouter web-search pipeline, then **audited and filled** against issuer SEC/IR disclosures (see `audit_report.json`).

## Files

- `quarterly_revenue.jsonl` — product-quarter values with citations
- `unresolved_quarters.jsonl` — explicit non-disclosures (not zero revenue)
- `edge_cases.jsonl` — out-of-window / generic-dose false positives
- `manifest.json` — drug count and calendar window
- `build_report.json` — last pipeline build summary
- `audit_report.json` — last audit/fill summary
- `archive/manual-2026-08-24/` — prior manually researched gold

## Gaps closed in audit (2026-08-27)

- **Winrevair** 2025Q1–Q4 worldwide sales from Merck schedules
- **Adempas** 2025Q1–Q4 Merck-recorded net sales (not alliance revenue)
- **Opsumit** 2025Q1–Q4 as OPSUMIT/OPSYNVI product-family WW (J&J combined line)
- **Uptravi** 2025Q1–Q4 worldwide from J&J other financial disclosures
- Scope cleanup: Adcirca/Orenitram → U.S.; Tyvaso DPI/Nebulized → Formulation-specific; Remodulin → Worldwide
- Expanded unresolved coverage for thin non-disclosures (Tracleer/Veletri/Ventavis/Revatio/Flolan/Alyq/Tadliq/Liqrev 2024Q1–Q4)

## Regenerate pipeline gold

```bash
cd backend
uv run python ../scripts/build_gold_web_search.py \
  --manual-revenue ../seed/gold/archive/manual-2026-08-24/quarterly_revenue.jsonl \
  --manual-unresolved ../seed/gold/archive/manual-2026-08-24/unresolved_quarters.jsonl
uv run python ../scripts/audit_fill_gold.py
```

Run `cd backend && uv run pytest tests/test_gold_dataset.py` to validate.
