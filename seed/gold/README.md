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

    20 catalog products = 13 complete quarterly series (502 quarters, every one
    covering its full span) + 1 annual benchmark series + 6 evidence-backed
    exclusions

Every one of those 502 quarters is also reachable by the pipeline: it is either
read from a citation, computed from an issuer's own stated total, or assembled
from the two dated halves of a quarter split by an acquisition.
`test_every_quarterly_row_is_reachable_by_the_pipeline` refuses a row that
takes none of those routes, because a benchmark row nothing can reproduce
measures nothing.

**This is not the percentage `scripts/eval_completeness.py` prints.** That
script scores the *pipeline* against this dataset — how many of the 502
quarters it can read or derive on its own. A shortfall there is a capability
the pipeline is missing, which is exactly what a benchmark is for. Reading it
as a hole in the dataset gets the direction of the measurement backwards.

The 6 exclusions are part of completeness rather than a shortfall in it. A
product is excluded only with a citation showing why no comparable series
exists, and in several cases the issuer never published one at all:

- **Letairis** — Gilead reports it only inside an aggregate. Every quarterly
  disclosure from 2018 through 2020 reads "Other product sales, which include
  ... Letairis, Ranexa and AmBisome", with no standalone figure. A quarterly
  Letairis series does not exist publicly, at any level of effort.
- **Alyq, Tadliq, Liqrev** — no public product-level sales; the only figures
  are CMS payer spend, which is pre-rebate by law and must never fill a
  revenue column.
(**Adempas and Opsumit are no longer among them** — see below. Both were
excluded on conclusions that were true when written and stopped being true: one
when the Bayer/Merck split was read properly, the other when J&J's exhibit
archive turned out to be reachable after all.)

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

## Issuer concentration, and why the catalog stops where it does

### Concentration is the real limit, not product count

United Therapeutics is **73% of these rows** — it publishes the longest
per-product histories in the therapy area. A pipeline that read UTHR perfectly
and every other issuer at half would still score above 85% overall, which is
why `eval_completeness.py` prints delivery **by issuer** and why
`test_no_single_issuer_supplies_more_than_four_fifths_of_the_catalog` fails if
more UTHR history is added without adding anyone else.

| Issuer | Quarters | Share |
|---|---|---|
| United Therapeutics | 368 | 73.3% |
| Actelion/J&J | 58 | 11.6% |
| Johnson & Johnson | 36 | 7.2% |
| Merck | 19 | 3.8% |
| Gilead | 16 | 3.2% |
| Liquidia | 5 | 1.0% |

This is not closable by sourcing harder — the products left in the catalog are
the ones with no series. It is closable only by adding issuers, which is what
Tracleer (Actelion/J&J) and Letairis (Gilead) did: 78.3% down to 73.3%.

### Products with no quarterly series

Every remaining candidate has been checked against primary documents, and each
fails for a reason that is a property of the disclosure, not of the effort:

| Product | Why no quarterly series |
|---|---|
| Revatio | Pfizer discusses it only as a change driver, never a level |
| Adempas (worldwide) | Bayer's half includes amortized collaboration payments; only Merck's territory line is product sales |
| Opsumit (2013–2017, 2025–) | outside the bounded series below: Actelion's CHF quarters are only partly disclosed, and from 2025 J&J reports a combined OPSUMIT/OPSYNVI line |
| Veletri, Ventavis | Actelion-era quarters; pre-2017 filings are not in the reachable index |
| Alyq, Tadliq, Liqrev | no public product-level sales at all |

### Opsumit: a series bounded at both ends

Opsumit is the eleventh quarterly series and the first whose span is cut short
at *both* ends. It runs **2016Q1–2024Q4, 36 quarters, worldwide, USD**. From
2017Q3 on, each quarter is the OPSUMIT `WW` row of the Sales of Key Products /
Franchises schedule J&J publishes with each earnings release. Before that it is
Actelion's own history, which J&J republished in US dollars when the
acquisition closed — the same schedule Uptravi's early quarters come from.

Both boundaries are declared in `series_coverage.jsonl` rather than silently
applied, because a reader who mistakes either one gets a wrong answer from a
right-looking series:

- **`launch_quarter` 2013Q4, `commercial_start_quarter` 2016Q1.** Opsumit
  launched under Actelion, whose own disclosures were in CHF and covered only
  scattered quarters. J&J republished Actelion's history in US dollars when the
  acquisition closed, but that schedule reaches back only to 2016Q1, which is
  where this series starts. 2013Q4–2015Q4 has no US-dollar quarterly source, so
  uptake measured from here is **not launch-to-date**.
- **`series_end_quarter` 2024Q4.** From 2025Q1 J&J reports a combined
  `OPSUMIT / OPSYNVI` line and restates FY2024 from 2,184 to 2,225 to match.
  Splitting that back apart would invent values.

Because 2013Q4–2015Q4 is not citable at all, 2024's $2,184m is the highest
observed value on a still-rising curve, not a lifetime peak. `peak_eligible` is false and no peak row is emitted — the same
shape as Adempas.

#### What makes it trustworthy

Each quarter comes from a different document, so a mis-keyed or mis-aligned
figure would not contradict anything *inside* the series. It does contradict
the issuer: J&J states a full-year worldwide total in each Q4 schedule, and all
seven complete years reproduce it exactly.

| Year | Q1 | Q2 | Q3 | Q4 | Sum | Stated FY |
|---|---|---|---|---|---|---|
| 2016 | 179 | 207 | 223 | 235 | 844 | 844 |
| 2018 | 271 | 311 | 310 | 323 | 1,215 | 1,215 |
| 2019 | 306 | 348 | 347 | 326 | 1,327 | 1,327 |
| 2020 | 389 | 406 | 392 | 452 | 1,639 | 1,639 |
| 2021 | 450 | 463 | 458 | 448 | 1,819 | 1,819 |
| 2022 | 443 | 438 | 441 | 461 | 1,783 | 1,783 |
| 2023 | 440 | 507 | 490 | 536 | 1,973 | 1,973 |
| 2024 | 524 | 544 | 571 | 545 | 2,184 | 2,184 |

`test_opsumit_quarters_sum_to_the_full_year_jnj_states` pins this. 2016 checks
against the Actelion schedule's own Full Year column; 2018–2024 against J&J's.

**2017 is the one year with nothing to check against.** Actelion reports
through 15 June and J&J from 16 June, and neither publishes a twelve-month
total. All four quarters are present, but the year is only verifiable against
its parts.

#### 2017Q2: the quarter no single issuer reports

The acquisition closed on 16 June 2017, mid-quarter. Actelion's last schedule
stops there and J&J's first one starts there, so 2017Q2 exists only as
**216 + 45 = 261**. The schedule says so itself: its 2017 Q2 column is headed
*"through 6/15"*, with a footnote that those figures "have not been previously
disclosed".

Adding two numbers is easy to get wrong in a way that looks right — counting
the closing day on both sides, or dropping a stretch neither issuer covered —
so the row carries the two parts **with their dates**, and
`assemble_split_ownership_quarter` refuses to add them unless they tile the
quarter: contiguous, non-overlapping, starting on 1 April.

One thing it deliberately allows: J&J runs a 52/53-week fiscal calendar, and
its second quarter of 2017 ended **2 July**, not 30 June. So the assembled
figure covers two days more than calendar Q2 and *cannot* be made exact. The
overshoot is bounded and recorded rather than hidden, because the alternative
is having no value for the quarter at all. Uptravi's 2017Q2 (110 + 9 = 119) is
the same quarter, the same two documents and the same caveat.

Worldwide is read **as reported and never summed from US + International**:
J&J rounds each line independently, so the parts differ from the stated
worldwide figure by 1 in 2019Q1, 2021Q2 and 2024Q1. The US and International
figures ride along in each row's `gold_notes` so this stays checkable.

Every quarter cites its own quarter's schedule, with one exception: **2020Q3**
cites the 3Q2021 schedule's prior-year column, because the 3Q2020 document's
text could not be read past its International row. That row carries a written
column legend naming what each column is, which is the same third citation form
Winrevair uses — and it is why one Opsumit quarter is a gold-side derivation in
`eval_completeness.py` rather than a direct read.

#### What was wrong before, and why

This section previously said Opsumit could not be a series, on the grounds that
only one worldwide quarter (2024Q2, $544m) was stated outright and the rest
would have to be derived — unsafely, since deriving 2024Q3 U.S. from the stated
nine months gives 406 against a stated-percent-implied 405.

That reasoning was sound and its premise was wrong. It rested on J&J's XBRL
segment table being the only reachable source, which was true of the routes
tested at the time. J&J's Exhibit 99.2 archive states **every** quarter
outright, and nothing has to be derived. The bar did not move; the document
became readable.

A second, later claim in this file was also wrong: that the Actelion era was
not citable because it was reported in CHF. J&J republished it in US dollars,
which is why the series now starts at 2016Q1 rather than 2017Q3. The near-miss is still worth recording, because a derived
series that contradicts the issuer's own figure reads as a win until it is
checked.

## Resolved: Remodulin 2002Q4 now satisfies its own arithmetic

`2002Q4` was recorded as **$9.7 million** and its own citations produced
$9.874 million. The section that used to sit here documented the discrepancy
and declined to correct it, on the grounds that no rounding of the cited
figures reached 9.7.

The cause was a missing row, not a rounding rule. United Therapeutics' third
quarter 2002 10-Q states three quarters in one sentence:

> Sales of Remodulin totaled approximately $205,000 in the three months ended
> March 31, 2002, approximately $8.7 million in the three months ended June 30,
> 2002, and approximately $2.6 million in the three months ended September 30,
> 2002.

**2002Q1 was never recorded.** Remodulin was not approved until 21 May 2002, so
$205,000 of pre-approval supply looked like nothing to carry — but the issuer
reports it as Remodulin revenue, and the full-year total of $21.174 million
includes it. Subtracting only Q2 and Q3 left the missing $205,000 sitting in
Q4, which is exactly the 0.2 the arithmetic was out by.

2002Q1 is now in the series, `commercial_start_quarter` moved to 2002Q1, and
2002Q4 is $9.669 million — full year less the three stated quarters, matching
what the pipeline derives. The figure is marked `approximate`, because all
three inputs are "approximately" figures and a value derived from rounded
inputs is not exact however many decimals it has.

Reading it also needed two things the prose reader could not do: an amount
written out in full (`$205,000`, not "$0.2 million") and an enumeration that
alternates amount, period, amount, period with no "respectively" to state the
pairing. Both are now supported, and both are guarded against over-reading —
see `test_prose_does_not_treat_every_bare_number_as_money`.

## Known difference: Adempas 2025Q4 is 83 by Merck's arithmetic and 82 by ours

Merck states nine-month 2025 Adempas sales of **229** while its own quarters
(68 + 80 + 82) sum to **230**. Both are true: Merck rounds each published period
independently. Gold records **83** (312 less the stated nine months); the
derivation recomputes **82** (312 less the summed quarters).

Neither is a defect, so neither is "fixed". It is listed by name in
`KNOWN_ROUNDING_DISAGREEMENTS` in `scripts/eval_completeness.py`, which is what
makes a *new* disagreement mean something — an unlisted one fails that script
instead of scrolling past as familiar noise.

## Remaining gaps and why

**There are none.** All 502 quarters are deliverable. This section used to list
eleven that were not; what closed them is recorded here because the causes were
different and only one was really about sourcing.

| Product | Quarters | What it turned out to be |
|---|---|---|
| Remodulin | 2003Q4–2008Q4 (6) | Not a sourcing problem. The annual totals were reachable all along — `sec.gov` is fetchable through the Parallel Search connector, which an earlier note wrongly recorded as failing. Worse, the six Q4 rows had quoted a sentence that restated their own value, so nothing could check them |
| Winrevair | 2024Q2–Q4, 2025Q4 (4) | Merck's 10-Q states each quarter in the ordinary way, so two of them are now plain table rows and the other two derive from Merck's stated full years. The IR schedule whose column order encoded the approval date is no longer the only source |
| Uptravi | 2017Q2 (1) | A real structural case, and the only one: the quarter spans J&J's acquisition of Actelion. It is now assembled by the pipeline from the two issuers' dated halves rather than left as a gap |

Three things were wrong in the old list rather than merely incomplete, and are
worth keeping visible: `sec.gov` was recorded as unreachable when it was not;
six Q4 rows carried self-referential quotes; and Remodulin's 2005Q4 was off by
$1,000, which only showed up once the exact annual figure was in hand.

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
