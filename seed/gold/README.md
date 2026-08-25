# Gold validation dataset

Manually researched ground truth for the 20 products in `seed/example_drugs.csv`, constrained to 2022–2026.

## Files

- `quarterly_revenue.jsonl`: 62 product-quarter values copied from SEC filings or issuer-distributed financial schedules.
- `unresolved_quarters.jsonl`: 13 modern quarters where the issuer does not separately disclose the product. These are non-disclosure labels, not zero revenue.
- `edge_cases.jsonl`: historical out-of-window records plus generic/brand and dosage/revenue ambiguity cases.
- `manifest.json`: required drug count and five-calendar-year boundary used by validation tests.

Each positive row follows the workbench datapoint/export fields and includes the primary source URL, table excerpt, period, scope, units, and provenance.

## Method

1. Search the issuer's SEC filings, investor-relations schedules, and issuer-distributed earnings releases.
2. Accept only product-named quarterly values. Do not derive quarters from YTD or annual totals.
3. Preserve the issuer's scope and currency. Values normalized as USD millions are normalized only when the source itself reports USD millions.
4. Mark a quarter unresolved when the product is aggregated or omitted; never assign a franchise, company-total, or peer-product value.
5. Cross-check table headers so current-quarter values are not confused with prior-year or YTD columns.
6. Keep every reported and unresolved period within the 2022–2026 benchmark window.
7. Keep valid older records in the edge set, and require generic-only or dosage-only evidence to be rejected as branded revenue.
8. Formulation is never left blank on product-family aggregates: use a semicolon list of constituents (e.g. `DPI; nebulized`) or the sentinel `aggregate` when constituents are unknown.

Research performed on 2026-08-24. Source URLs and excerpts are stored per row so the set can be re-audited if issuers amend or move documents.

## Coverage notes

- Tyvaso and Adcirca: United Therapeutics Q1 2023-Q4 2024 SEC earnings exhibits. Tyvaso product-family totals use formulation `DPI; nebulized`.
- Opsumit: Johnson & Johnson Q1 2023-Q4 2024 SEC supplementary sales exhibits.
- Letairis: Gilead Q1 2022-Q4 2023 SEC tables. In 2024, Gilead aggregates Letairis into Other products.
- Tracleer, Veletri, and Ventavis: current issuer schedules do not separately report these products; they remain explicit non-disclosures.
- Uptravi, Remodulin, and Orenitram: Q1-Q4 2024 SEC earnings exhibits.
- Tyvaso DPI and Nebulized Tyvaso: formulation-specific Q1-Q4 2024 SEC values.
- Winrevair and Adempas: Merck 2024 SEC values.
- Yutrepia: Liquidia launch-quarter through Q4 2025 SEC values.
- Revatio, Flolan, Alyq, Tadliq, and Liqrev: explicit recent non-disclosures.

Run `cd backend && uv run pytest tests/test_gold_dataset.py` to validate JSONL integrity against the production domain model and quality filters.
