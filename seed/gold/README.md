# Independent peak-sales gold dataset

This directory is a source of truth built from issuer SEC filings and investor
relations disclosures. It is not produced by the application extraction
pipeline, an LLM, or pipeline post-processing.

All 20 products in `seed/example_drugs.csv` have one explicit disposition:

- 12 have independently supported peak labels.
- 9 of those also have complete quarter-by-quarter sales from commercial start
  through 2026Q2, totaling 423 cited observations at 100% coverage.
- 6 labels are observed numeric peaks.
- 6 labels are `not_yet_observed` because the complete reported history is
  still growing or lacks enough post-peak years.
- 8 products are excluded with a cited reason because public reporting is
  aggregated, scope-incomparable, private, or incomplete before the possible
  peak. No sales are invented for them.

## Files

- `quarterly_revenue.jsonl`: complete independently reported quarterly series.
- `annual_revenue.jsonl`: issuer-reported annual peak series and partial
  context. `value_reported`/`currency` are as-disclosed (Tracleer, Opsumit,
  Veletri and Ventavis are CHF, Flolan is GBP — no issuer ever quoted a USD
  figure). Rows with `series_role: partial_context` belong to products that
  are *excluded* from the quarterly benchmark: Actelion and GSK published
  annual per-product figures for them, but no contiguous launch-to-end
  quarterly series is citable, so they carry a verified number without
  carrying a peak or a coverage assertion. Every row also
  carries `value_normalized_usd_millions`, converted with the annual-average
  FX rate in `fx_rate_to_usd`/`fx_rate_source` (`FX_RATE_USD_PER_CHF` /
  `FX_RATE_USD_PER_GBP` in the builder — the CHF table is sourced directly
  from UBS Group AG's own SEC "Selected Financial Data" filings, which
  disclose the Fed NY noon buying rate every year for exactly this purpose);
  USD rows have `fx_rate_to_usd: null` since no conversion applies. Peak
  selection for annual-only products
  compares `value_normalized_usd_millions`, not the raw currency — this
  matters for Tracleer, whose CHF-nominal peak year (2010) is not its
  USD-normalized peak year (2011, when the franc's surge made a smaller CHF
  figure worth more).
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

## Known issue: Remodulin 2002Q4 does not satisfy its own arithmetic

`2002Q4` is recorded as **$9.7 million**, derived as full-year less the first
three quarters. Its own citations do not produce that number:

| Period | Value | Precision |
|---|---|---|
| FY2002 | $21.174m | exact, quoted from the FY2002 10-K |
| 2002Q2 | $8.7m | "approximately", from the Q2 10-Q |
| 2002Q3 | $2.6m | "approximately", from the Q3 10-Q |

21.174 − 8.7 − 2.6 = **9.874**. Rounding cannot close the gap: for the stated
Q4 to be 9.7, the unrounded Q2 and Q3 would have to sum to 11.474, and no pair
that rounds to 8.7 and 2.6 does — the reachable range for Q4 is about 9.78 to
9.97. Remodulin's `commercial_start_quarter` is 2002Q2, so there is no Q1
figure that could absorb the difference.

`scripts/eval_completeness.py` reports this as a derived-vs-gold disagreement
on every run, so it stays visible rather than silently counting as coverage.
Of the 51 quarters derivation reproduces, this is the only one that disagrees.

It is **not corrected here**, because correcting it would mean writing a value
no reachable document states. Resolving it needs United Therapeutics' 2002 10-Qs
and FY2002 10-K, which this environment cannot fetch: outbound HTTP is blocked,
and the filings index in use does not reach back before roughly 2017. The most
likely explanation is that the two "approximately" quarterly disclosures were
rounded from figures the builder no longer records, but that is a hypothesis,
not a finding.

The six `annual_less_reported_first_nine_months` Q4 rows for 2003–2008 have a
related weakness: their `source_quote` describes the arithmetic ("annual sales
less first-nine-month sales yields...") instead of citing the annual total that
drove it. The values are unaffected, but the total is not recorded as a citable
figure, so the pipeline cannot reproduce those derivations and they score as
gaps. Recording each year's annual figure from the cited 10-K would close them;
that too needs the pre-2017 filings.

**Do not close them by adding an annual row computed as Q1+Q2+Q3+Q4.** Those
four quarters already include the derived Q4, so the total would be
reconstructed from the answer and the derivation would prove only that
subtraction is the inverse of addition. The gap is real until an issuer-stated
annual figure is read.

## Remaining gaps and why

11 of 423 quarters are not deliverable by the pipeline. Each has a specific,
recorded cause rather than being unexplained:

| Product | Quarters | Cause |
|---|---|---|
| Remodulin | 2003Q4–2008Q4 (6) | annual totals never recorded as citable figures; the cited 10-Ks predate the reachable filing index |
| Winrevair | 2024Q2–Q4, 2025Q4 (4) | Merck's IR schedule runs 2025 Q1–Q4 then 2024 Q2–Q4, because the product was approved in March 2024 and has no separate Q1. Aligning it requires the approval date, which no extractor can read off the page — the same defect class this dataset pinned |
| Uptravi | 2017Q2 (1) | the quarter spans J&J's acquisition of Actelion, so it is a bridge of two issuers' partial reporting |

The Winrevair four are the interesting case: they are not a sourcing failure but
a genuine limit on what a self-describing citation can carry. Merck states those
quarters only in a schedule whose column order encodes a fact about the product,
not about the table.
