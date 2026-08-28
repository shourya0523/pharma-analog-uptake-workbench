# Gold validation dataset (archived 2022–2026 window)

Frozen snapshot of the previous gold set: a global 2022–2026 calendar window
(`start_year` 2022, `end_year` 2026, `max_year_span` 5). Historical rows such as
Tracleer 2016Q4 lived in `edge_cases.jsonl` as `old_record`.

Current gold uses per-drug full commercial life. See `seed/gold/README.md`.

## Files

- `quarterly_revenue.jsonl` — product-quarter values with citations
- `unresolved_quarters.jsonl` — explicit non-disclosures (not zero revenue)
- `edge_cases.jsonl` — out-of-window / generic-dose false positives
- `manifest.json` — drug count and calendar window
- `build_report.json` — last pipeline build summary
- `audit_report.json` — last audit/fill summary

## Gaps closed in audit (2026-08-27)

- **Winrevair** 2025Q1–Q4 worldwide sales from Merck schedules
- **Adempas** 2025Q1–Q4 Merck-recorded net sales (not alliance revenue)
- **Opsumit** 2025Q1–Q4 as OPSUMIT/OPSYNVI product-family WW (J&J combined line)
- **Uptravi** 2025Q1–Q4 worldwide from J&J other financial disclosures
- Scope cleanup: Adcirca/Orenitram → U.S.; Tyvaso DPI/Nebulized → Formulation-specific; Remodulin → Worldwide
- Expanded unresolved coverage for thin non-disclosures (Tracleer/Veletri/Ventavis/Revatio/Flolan/Alyq/Tadliq/Liqrev 2024Q1–Q4)

This snapshot is not regenerated. Rebuild current gold with
`scripts/rebuild_gold_lifecycle.py`.
