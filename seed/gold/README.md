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

## Is this dataset complete?

Yes, on the only definition that is checkable: every product in the seed
catalog is accounted for, and no included series is missing a quarter of its
own commercial span. `build_report.json` records this under
`gold_completeness`, the builder refuses to emit an incomplete series, and
`test_the_gold_dataset_is_complete_on_its_own_terms` pins it.

    20 catalog products = 10 complete quarterly series (433 quarters, every one
    covering its full span) + 3 annual benchmark series + 7 evidence-backed
    exclusions

**This is not the percentage `scripts/eval_completeness.py` prints.** That
script scores the *pipeline* against this dataset — how many of the 423
quarters it can read or derive on its own. A shortfall there is a capability
the pipeline is missing, which is exactly what a benchmark is for. Reading it
as a hole in the dataset gets the direction of the measurement backwards.

The 8 exclusions are part of completeness rather than a shortfall in it. A
product is excluded only with a citation showing why no comparable series
exists, and in several cases the issuer never published one at all:

- **Letairis** — Gilead reports it only inside an aggregate. Every quarterly
  disclosure from 2018 through 2020 reads "Other product sales, which include
  ... Letairis, Ranexa and AmBisome", with no standalone figure. A quarterly
  Letairis series does not exist publicly, at any level of effort.
- **Alyq, Tadliq, Liqrev** — no public product-level sales; the only figures
  are CMS payer spend, which is pre-rebate by law and must never fill a
  revenue column.
(**Adempas is no longer among them** — see below.)

Recording those as exclusions with evidence *is* the complete answer for them.
Inventing a series would not be more complete, only less true.

## Adempas: a scoped series, not a worldwide one

Adempas was excluded on the grounds that Merck's figure "includes amortized
collaboration income, so it isn't a product-sales series". That was true of
what Merck reported **until 2020Q1**: a single blended line mixing its own
territory sales with its profit share from Bayer's territories. From 2020Q1
Merck splits them, and the `Adempas` line is territory product sales with
`Alliance Revenue - Adempas/Verquvo` reported separately. The exclusion was
right about the blend and wrong to conclude that no series existed after it.

The series is included with its scope stated in every row —
`revenue_scope: Merck marketing territories`, `geography: International` —
because Bayer commercialises Adempas in the Americas and those sales are never
product revenue to Merck. **Do not compare it to a worldwide series.** It is
the only territory-split product in the catalog, which is precisely why it is
worth having: nothing else exercises that scope.

### Why it stays scoped: Bayer's half is not product sales

The obvious completion is to add Bayer's Americas figures and call the result
worldwide. That does not work, and Bayer's own statements say why. Bayer prints
Adempas in its "Best-selling Pharmaceuticals Products" table (€152m, €171m,
€183m, €186m for Q1 2023 through Q1 2026), but the facing narrative states, in
both statements read and in identical words: *"As in the past, sales reflected
the proportionate recognition of the upfront and milestone payments resulting
from the sGC collaboration with Merck & Co., United States."*

So Bayer's line is product sales plus amortized collaboration payments. Summing
it with Merck's territory sales would yield a worldwide-looking number with
collaboration income inside one half. The scope stays where it is, and the
figure stays out of `seed/gold/` — see `docs/sourcing/excluded-products.md`.

This is also the resolution of the original exclusion note, which said Bayer's
line "includes amortized collaboration income, so it isn't a product-sales
series". That was correct about Bayer. Its only error was concluding no Adempas
series existed anywhere, when Merck's post-2020 split line is clean.

### Series bounds

It starts at 2024Q1 rather than the 2013Q4 launch, and carries a
`series_start_reason` saying so. Two things pushed the boundary: the basis
change above, and provenance — Merck's 2020–2023 filings are reachable here
only as redistributed copies, so those quarters cannot be cited to the document
that reports them. Because the series begins after launch it is a scope and
format benchmark, never a peak: `peak_eligible` is false and a test enforces
that it acquires no peak row.

### Merck rounds, and the derivation shows it

`eval_completeness.py` reports Adempas 2025Q4 as derived 82 against a gold
value of 83. Gold is right and the derivation is not wrong either:

| Route | Result |
|---|---|
| Merck's stated full year (312) less its stated nine months (229) | **83** |
| 312 less the sum of the three stated quarters (68 + 80 + 82 = 230) | 82 |

Merck's published nine-month figure is 229, not 230, because it rounds each
period independently. The same thing appears in Winrevair (stated first half
$615m against quarters summing to $616m) and in Adempas's own first half
($147m against 68 + 80 = 148). The gold value is the one Merck states in its
quarterly schedule. The disagreement is left visible rather than reconciled,
because it is a real property of issuer disclosure that any consumer deriving
quarters needs to know about.

## Why the catalog stops at 11 quarterly products

Every remaining candidate has been checked against primary documents, and each
fails for a reason that is a property of the disclosure, not of the effort:

| Product | Why no quarterly series |
|---|---|
| Letairis | Gilead reports it only inside "Other product sales"; no standalone quarterly figure exists publicly |
| Revatio | Pfizer discusses it only as a change driver, never a level |
| Adempas (worldwide) | Bayer's half includes amortized collaboration payments; only Merck's territory line is product sales |
| Opsumit | see below |
| Tracleer, Veletri, Ventavis | Actelion-era quarters; pre-2017 filings are not in the reachable index |
| Alyq, Tadliq, Liqrev | no public product-level sales at all |

### Opsumit: one stated quarter is not a year

J&J's XBRL segment table does carry OPSUMIT, but only FY2024 is indexed here,
and within it exactly one worldwide quarter is stated outright (2024Q2,
$544m). The rest would have to be derived, and J&J's rounding makes that
unsafe: deriving 2024Q3 U.S. from the stated nine months gives 406, while the
stated percent change on that row implies 405. A derived value that
contradicts the issuer's own stated figure is worse than an absent one, so
Opsumit stays annual-only.

The near-miss is worth recording because it is the kind of thing that reads as
a win until it is checked: the figures are all there, they cross-check to the
full year, and the series still cannot be built honestly.

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

### How to close them (for a session with network access)

Each gap needs one specific document. The values are already known and
cross-checked; what is missing is a citation that carries its own period.

**Remodulin 2003Q4–2008Q4.** Each Q4 row already cites the FY 10-K that the
annual total was read from — the URLs are in `uthr_remodulin_early.csv`. Fetch
each, read "Remodulin net product sales" for the year, and add it to
`annual_product_sales.csv`. The derivation stage then reproduces all six Q4s,
since Q1–Q3 of every one of those years is already a direct citation. Nothing
else is required.

**Winrevair 2024Q2–Q4, 2025Q4.** Fetch Merck's quarterly *Other Financial
Disclosures* schedule (linked from the row's `source_url`) and capture the
Winrevair line **with the schedule's own period header row**, exactly the way
Uptravi's rows carry J&J's exhibit header. That replaces the hand-written
legend with the document's own declaration and `positional.py` handles the
rest. Alternatively, Merck's 10-Q MD&A "Cardiovascular" table states them in
the standard order and would work as a `table_row`:

    Winrevair | 70  | - | - | - | 70  | - | - | -   (Q2 2024 10-Q)
    Winrevair | 149 | - | - | - | 219 | - | - | -   (Q3 2024 10-Q)

Both were read during sourcing and independently confirm the recorded values
(219 = 70 + 149), but only from a redistributor's copy, so they are not cited
here — see the note on provenance below. Adding Merck's stated full years
(2024 $419m, 2025 $1,443m) as annual rows would additionally derive both Q4s.

**Uptravi 2017Q2.** The quarter straddles J&J's 16 June 2017 acquisition of
Actelion, so it needs Actelion's stub-period disclosure plus J&J's, not a
single figure from either.

### A note on provenance

Several figures above were verified during sourcing but are *not* cited,
because they were read in a redistributor's mirror of a filing rather than in
the filing itself. Citing the SEC URL for text read elsewhere would assert that
the document contains an exact string that was never checked against it — the
same class of unverified provenance this dataset exists to eliminate. A gap
with a known cause is worth more here than a citation that cannot be trusted.
